"""数据源真实链路实测：默认跳过，用 ``-m live`` 显式开启。

与其余测试的根本区别：本文件**真的联网**，验证的是「此刻这些源还能不能取到
数据」，而不是代码逻辑。因此：

- 全部用例标 ``@pytest.mark.live``，``pyproject.toml`` 的 ``addopts`` 默认排除，
  CI 与日常开发不受外部服务可用性影响；
- API Key 一律从系统环境变量读取，缺失时 ``skip`` 而不是 ``fail``
  （没配 Key 不是代码缺陷）；
- 失败即代表该源当下不可用（限流/改版/网络），是需要如实报告的信息。

运行：
    cd scripts && uv run pytest tests/test_live_sources.py -q -m live
"""

from __future__ import annotations

import os

import pandas as pd
import pytest

from data import health
from data.calendar import last_closed_session
from data.quality import validate_ohlcv
from data.sources import (
    AkshareSource,
    BaostockSource,
    OpenBBSource,
    TickFlowSource,
    YFinanceSource,
)
from datafeed import _fetch_ohlcv_raw

pytestmark = pytest.mark.live

#: 三市场代表标的（流动性好、长期存续，适合做可用性探针）
CN_SYMBOL = "600000.SH"
HK_SYMBOL = "00700.HK"
US_SYMBOL = "AAPL.US"

#: 源 × 该源应当覆盖的代表标的
SOURCE_CASES = [
    ("openbb", OpenBBSource, US_SYMBOL),
    ("tickflow", TickFlowSource, CN_SYMBOL),
    ("baostock", BaostockSource, CN_SYMBOL),
    ("akshare", AkshareSource, CN_SYMBOL),
    ("yfinance", YFinanceSource, US_SYMBOL),
]

#: 数据末根 K 线允许落后最近收盘会话的自然日数（跨长假 + 时区差的余量）
MAX_STALE_DAYS = 12


@pytest.fixture(autouse=True)
def _reset_health():
    """实测会真的打到失败计数上，用例间必须互不干扰。"""
    health.reset()
    yield
    health.reset()


def _last_bar(df: pd.DataFrame) -> pd.Timestamp:
    return pd.Timestamp(pd.to_datetime(df["trade_date"]).iloc[-1]).normalize()


# ─── 单源可用性 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,cls,symbol", SOURCE_CASES, ids=[c[0] for c in SOURCE_CASES]
)
def test_source_returns_usable_data(name, cls, symbol):
    """每个源单独强制拉取：能取到数据、通过质量校验且数据不陈旧。

    三项合在一个用例里是有意的：实测每多一次拉取就多一份限流风险。
    """
    source = cls()
    assert source.supports(symbol, "1d"), f"{name} 应当覆盖 {symbol} 日 K"

    df = source.fetch(symbol, "1d", 60, "forward")
    assert len(df) > 0, f"{name} 返回空数据"
    assert {"open", "high", "low", "close", "volume"} <= set(df.columns)

    report = validate_ohlcv(df, symbol, "1d")
    assert report.passed, f"{name} 数据质量校验未通过：{report.summary()}"

    # 静默返回陈旧历史比直接报错更危险：末根 K 线必须贴近当下
    last_bar = _last_bar(df)
    stale_days = (pd.Timestamp.now().normalize() - last_bar).days
    assert stale_days <= MAX_STALE_DAYS, (
        f"{name} 的 {symbol} 末根 K 线为 {last_bar.date()}，落后 {stale_days} 天"
    )


def test_hk_covered_by_at_least_one_source():
    """港股至少要有一个源能用（openbb 与 yfinance 同上游，可能一起挂）。"""
    errors = []
    for cls in (OpenBBSource, YFinanceSource, TickFlowSource):
        source = cls()
        if not source.supports(HK_SYMBOL, "1d"):
            continue
        try:
            df = source.fetch(HK_SYMBOL, "1d", 30, "forward")
        except Exception as exc:
            errors.append(f"{source.name}: {type(exc).__name__}: {exc}")
            continue
        assert len(df) > 0
        return
    pytest.fail("港股无任何可用源：\n  " + "\n  ".join(errors))


# ─── API Key 路径（缺 Key 跳过，不是失败）──────────────────────────────────────


@pytest.mark.skipif(
    not os.environ.get("TICKFLOW_API_KEY"),
    reason="未配置 TICKFLOW_API_KEY：分钟级数据需要 Key，跳过而非失败",
)
def test_tickflow_minute_data_with_key():
    """配了 Key 才验证分钟级链路（免费档只有日 K）。

    Key 存在 ≠ 有分钟 K 权限：权限不足是账户档位限制，不是代码缺陷，
    因此转为 skip 并在原因里带上上游原词（不隐藏真实报错）。
    """
    from tickflow import _exceptions as tf_exc

    try:
        df = TickFlowSource().fetch(CN_SYMBOL, "5m", 30, "none")
    except tf_exc.PermissionError as exc:
        pytest.skip(f"当前 Key 无分钟 K 级别权限（账户档位限制）：{exc}")
    assert len(df) > 0
    assert "close" in df.columns


# ─── 多源降级链 ────────────────────────────────────────────────────────────────


def test_auto_chain_degrades_when_primary_breaks(monkeypatch, capsys):
    """主源被打断时 auto 链必须降级到下一个源，而不是整体失败。"""

    def _boom(self, symbol, period, count, adjust):
        raise RuntimeError("模拟主源不可用")

    monkeypatch.setenv("ALPHA_FORGE_RETRIES", "0")  # 实测不必等退避
    monkeypatch.setattr(OpenBBSource, "fetch", _boom)

    df = _fetch_ohlcv_raw(US_SYMBOL, "1d", 30, "forward")
    assert len(df) > 0
    assert df.attrs["actual_source"] != "openbb"
    assert "已降级使用" in capsys.readouterr().err


def test_all_tripped_still_fetches_via_fallback():
    """全源熔断后的保底路径：忽略熔断状态用完整链，不能让扫描一次性全灭。"""
    for name, _cls, _symbol in SOURCE_CASES:
        for _ in range(3):
            health.record_failure(name)
    assert all(health.is_tripped(n) for n, _c, _s in SOURCE_CASES)

    df = _fetch_ohlcv_raw(CN_SYMBOL, "1d", 30, "forward")
    assert len(df) > 0


# ─── 真实交易日历 ──────────────────────────────────────────────────────────────


def test_cn_calendar_is_authoritative():
    """akshare 交易日列表可用时应判定为权威（否则新鲜度只能退回 TTL）。"""
    session = last_closed_session("CN")
    assert session is not None
    assert session.authoritative, (
        f"A 股日历降级为启发式（source={session.source}），"
        "akshare tool_trade_date_hist_sina 可能不可用"
    )


def test_calendar_session_matches_real_last_bar():
    """真实日历基准日与真实 K 线末日应当吻合（差距在长假余量内）。

    数据走 auto 降级链而非固定单源：本用例验证的是日历，不应因某一个
    免费源被限流而误报为日历问题。
    """
    session = last_closed_session("CN")
    assert session is not None

    df = _fetch_ohlcv_raw(CN_SYMBOL, "1d", 30, "forward")
    last_bar = _last_bar(df)
    gap = (session.last_closed - last_bar).days
    assert gap <= MAX_STALE_DAYS, (
        f"日历基准 {session.last_closed.date()} 比数据末日 {last_bar.date()} "
        f"超前 {gap} 天：数据陈旧或日历判错"
    )
    # 数据可以比基准更新（盘中已出当日 K 线），但不该超前一周以上
    assert gap >= -7


@pytest.mark.parametrize("market", ["CN", "HK", "US"])
def test_all_markets_have_session(market):
    session = last_closed_session(market)
    assert session is not None
    assert session.last_closed <= pd.Timestamp.now().normalize()
