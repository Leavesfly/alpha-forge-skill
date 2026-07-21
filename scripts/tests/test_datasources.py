"""数据源抽象与降级链路测试（全程 mock，不走网络）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import datafeed
from data.cache import _key
from data.sources import (
    AkshareSource,
    BaostockSource,
    YFinanceSource,
    _to_yahoo_symbol,
    get_sources,
    source_label,
)
from envconfig import reset_env_config
from tests.helpers import make_ohlcv


class _FailingSource:
    name = "failing"

    def supports(self, symbol, period):
        return True

    def fetch(self, symbol, period, count, adjust):
        raise RuntimeError("simulated outage")


class _OkSource:
    name = "ok"

    def __init__(self, df):
        self._df = df

    def supports(self, symbol, period):
        return True

    def fetch(self, symbol, period, count, adjust):
        return self._df


@pytest.fixture
def df():
    close = 100.0 * (1.0 + 0.001) ** np.arange(60)
    return make_ohlcv(close)


def test_fallback_chain(monkeypatch, df, capsys):
    """主源失败时自动降级到兜底源，并输出告警到 stderr。"""
    monkeypatch.setattr(
        datafeed, "get_sources", lambda: [_FailingSource(), _OkSource(df)]
    )
    out = datafeed._fetch_ohlcv_raw("600000.SH", "1d", 60, "forward")
    assert len(out) == 60
    captured = capsys.readouterr()
    assert "降级" in captured.err
    assert "failing" in captured.err


def test_all_sources_fail(monkeypatch):
    """全部源失败时抛出汇总错误。"""
    monkeypatch.setattr(
        datafeed, "get_sources", lambda: [_FailingSource(), _FailingSource()]
    )
    with pytest.raises(RuntimeError, match="已尝试 2 个数据源"):
        datafeed._fetch_ohlcv_raw("600000.SH", "1d", 60, "forward")


def test_akshare_supports_scope():
    """akshare 兜底仅覆盖 A 股日/周/月 K。"""
    src = AkshareSource()
    assert src.supports("600000.SH", "1d")
    assert src.supports("000001.SZ", "1w")
    assert src.supports("920662.BJ", "1d")  # 北交所
    assert not src.supports("AAPL.US", "1d")
    assert not src.supports("600000.SH", "5m")


def test_baostock_supports_scope():
    """baostock 仅覆盖沪深 A 股日/周/月 K（不含北交所）。"""
    src = BaostockSource()
    assert src.supports("600000.SH", "1d")
    assert src.supports("000001.SZ", "1w")
    assert src.supports("600519.SH", "1M")
    assert not src.supports("920662.BJ", "1d")  # 北交所不支持
    assert not src.supports("AAPL.US", "1d")
    assert not src.supports("600000.SH", "5m")


def test_forced_source_env(monkeypatch):
    """环境变量强制单源。"""
    monkeypatch.setenv("ALPHA_FORGE_DATA_SOURCE", "akshare")
    reset_env_config()
    assert source_label() == "akshare"
    sources = get_sources()
    assert len(sources) == 1 and sources[0].name == "akshare"

    monkeypatch.setenv("ALPHA_FORGE_DATA_SOURCE", "tickflow")
    reset_env_config()
    sources = get_sources()
    assert len(sources) == 1 and sources[0].name == "tickflow"

    monkeypatch.setenv("ALPHA_FORGE_DATA_SOURCE", "baostock")
    reset_env_config()
    assert source_label() == "baostock"
    sources = get_sources()
    assert len(sources) == 1 and sources[0].name == "baostock"

    monkeypatch.delenv("ALPHA_FORGE_DATA_SOURCE", raising=False)
    reset_env_config()
    assert source_label() == "auto"
    assert [s.name for s in get_sources()] == ["tickflow", "baostock", "akshare", "yfinance"]


def test_yfinance_forced_source_env(monkeypatch):
    monkeypatch.setenv("ALPHA_FORGE_DATA_SOURCE", "yfinance")
    reset_env_config()
    assert source_label() == "yfinance"
    sources = get_sources()
    assert len(sources) == 1 and sources[0].name == "yfinance"


def test_yfinance_supports_scope():
    """yfinance 兜底仅覆盖港股/美股日/周/月 K。"""
    src = YFinanceSource()
    assert src.supports("AAPL.US", "1d")
    assert src.supports("00700.HK", "1w")
    assert src.supports("MSFT.US", "1M")
    assert not src.supports("600000.SH", "1d")  # A 股交给 baostock/akshare
    assert not src.supports("AAPL.US", "5m")  # 分钟级不兜底
    assert not src.supports("ABCD.HK", "1d")  # 非数字港股代码无法映射


def test_yahoo_symbol_mapping():
    """本项目代码 -> Yahoo 代码：美股去后缀，港股压成 4 位。"""
    assert _to_yahoo_symbol("AAPL.US") == "AAPL"
    assert _to_yahoo_symbol("00700.HK") == "0700.HK"
    assert _to_yahoo_symbol("09988.HK") == "9988.HK"


def test_yfinance_column_normalization(monkeypatch):
    """yfinance 列名（含 MultiIndex）归一为标准 OHLCV。"""
    dates = pd.date_range("2024-01-02", periods=30, freq="B", tz="America/New_York")
    cols = pd.MultiIndex.from_product(
        [["Open", "High", "Low", "Close", "Volume"], ["AAPL"]]
    )
    values = np.column_stack(
        [
            np.linspace(10, 11, 30),
            np.linspace(10.2, 11.2, 30),
            np.linspace(9.9, 10.9, 30),
            np.linspace(10.1, 11.1, 30),
            np.full(30, 1000.0),
        ]
    )
    yf_df = pd.DataFrame(values, index=dates, columns=cols)
    yf_df.index.name = "Date"

    class _FakeYf:
        @staticmethod
        def download(ticker, period, interval, auto_adjust, progress, threads):
            assert ticker == "AAPL"
            assert interval == "1d"
            assert auto_adjust is True
            return yf_df

    import sys

    monkeypatch.setitem(sys.modules, "yfinance", _FakeYf)
    out = YFinanceSource().fetch("AAPL.US", "1d", 20, "forward")
    assert list(out.columns) == ["trade_date", "open", "high", "low", "close", "volume"]
    assert len(out) == 20
    assert out["trade_date"].is_monotonic_increasing
    assert out["trade_date"].dt.tz is None  # 时区已去除


def test_yfinance_backward_adjust_unsupported():
    with pytest.raises(RuntimeError, match="后复权"):
        YFinanceSource().fetch("AAPL.US", "1d", 20, "backward")


def test_cache_key_includes_source():
    """不同数据源的缓存键互不混用。"""
    k1 = _key("600000.SH", "1d", "forward", "auto")
    k2 = _key("600000.SH", "1d", "forward", "akshare")
    assert k1 != k2


def test_akshare_column_normalization(monkeypatch, df):
    """akshare 中文列名归一为标准 OHLCV。"""
    ak_df = pd.DataFrame(
        {
            "日期": pd.date_range("2024-01-02", periods=30, freq="B").strftime("%Y-%m-%d"),
            "开盘": np.linspace(10, 11, 30),
            "收盘": np.linspace(10.1, 11.1, 30),
            "最高": np.linspace(10.2, 11.2, 30),
            "最低": np.linspace(9.9, 10.9, 30),
            "成交量": np.full(30, 1000.0),
            "成交额": np.full(30, 1e7),
        }
    )

    class _FakeAk:
        @staticmethod
        def stock_zh_a_hist(symbol, period, adjust):
            assert symbol == "600000"
            assert period == "daily"
            assert adjust == "qfq"
            return ak_df

    import sys

    monkeypatch.setitem(sys.modules, "akshare", _FakeAk)
    out = AkshareSource().fetch("600000.SH", "1d", 20, "forward")
    assert list(out.columns) == ["trade_date", "open", "high", "low", "close", "volume"]
    assert len(out) == 20
    assert out["trade_date"].is_monotonic_increasing


# ---------------------------------------------------------------- fetch_dividends


def test_fetch_dividends_success(monkeypatch):
    """正常分红数据解析：每 10 股 -> 每股，按除权除息日升序。"""
    ak_df = pd.DataFrame(
        {
            "报告期": ["2022-12-31", "2023-12-31"],
            "除权除息日": ["2023-06-15", "2024-06-20"],
            "现金分红-现金分红比例": [3.0, 5.0],  # 每 10 股派 3 元、5 元
        }
    )

    class _FakeAk:
        @staticmethod
        def stock_fhps_detail_em(symbol):
            assert symbol == "600000"
            return ak_df

    import sys

    monkeypatch.setitem(sys.modules, "akshare", _FakeAk)
    series = datafeed.fetch_dividends("600000.SH")
    assert len(series) == 2
    assert series.iloc[0] == pytest.approx(0.3)  # 3/10
    assert series.iloc[1] == pytest.approx(0.5)  # 5/10
    assert series.index.is_monotonic_increasing


def test_fetch_dividends_no_record_typeerror(monkeypatch):
    """股票从未分红时 akshare 内部抛 TypeError，应转为友好 RuntimeError。"""

    class _FakeAk:
        @staticmethod
        def stock_fhps_detail_em(symbol):
            # 模拟 akshare 内部 API 返回 result=None 时的 TypeError
            raise TypeError("'NoneType' object is not subscriptable")

    import sys

    monkeypatch.setitem(sys.modules, "akshare", _FakeAk)
    with pytest.raises(RuntimeError, match="无分红记录"):
        datafeed.fetch_dividends("688981.SH")


def test_fetch_dividends_non_a_share_raises():
    """非 A 股标的应提前拦截。"""
    with pytest.raises(RuntimeError, match="仅支持 A 股"):
        datafeed.fetch_dividends("AAPL.US")
