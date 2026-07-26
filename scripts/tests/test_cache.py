"""数据缓存层测试：复权归一化、带缓存的 K 线读取、增量更新、离线模式与目录解析。"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from data.cache import (
    DEFAULT_TTL,
    MINUTE_TTL,
    CacheConfig,
    default_config,
    load_json_obj,
    load_klines,
    load_table,
    normalize_adjust,
    resolve_cache_dir,
)
from envconfig import reset_env_config
from errors import DataFetchError
from tests.helpers import make_ohlcv


def test_normalize_adjust_aliases():
    assert normalize_adjust("qfq") == "forward"
    assert normalize_adjust("forward") == "forward"
    assert normalize_adjust("前复权") == "forward"
    assert normalize_adjust("hfq") == "backward"
    assert normalize_adjust("后复权") == "backward"
    assert normalize_adjust("none") == "none"
    assert normalize_adjust(None) == "forward"
    assert normalize_adjust("unknown") == "forward"


def _counter_fetch(df, calls):
    def _fn():
        calls.append(1)
        return df
    return _fn


def test_cache_hit_avoids_second_fetch(tmp_path):
    """第二次读取应命中缓存，不再调用 fetch_fn。"""
    df = make_ohlcv([100, 101, 102, 103, 104])
    cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=True)
    calls: list[int] = []
    fn = _counter_fetch(df, calls)

    out1 = load_klines(fn, "TST.SH", "1d", count=5, adjust="forward", config=cfg)
    out2 = load_klines(fn, "TST.SH", "1d", count=5, adjust="forward", config=cfg)

    assert len(calls) == 1  # 仅首次拉取
    assert len(out1) == 5 and len(out2) == 5


def test_cache_disabled_always_fetches(tmp_path):
    df = make_ohlcv([100, 101, 102])
    cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=False)
    calls: list[int] = []
    fn = _counter_fetch(df, calls)
    load_klines(fn, "TST.SH", "1d", 3, "forward", cfg)
    load_klines(fn, "TST.SH", "1d", 3, "forward", cfg)
    assert len(calls) == 2


def test_cache_expiry_triggers_refetch(tmp_path):
    df = make_ohlcv([100, 101, 102])
    cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=0, enabled=True)  # 立即过期
    calls: list[int] = []
    fn = _counter_fetch(df, calls)
    load_klines(fn, "TST.SH", "1d", 3, "forward", cfg)
    time.sleep(0.01)
    load_klines(fn, "TST.SH", "1d", 3, "forward", cfg)
    assert len(calls) == 2


def test_cache_insufficient_rows_refetches(tmp_path):
    """请求行数超过缓存行数时应重新拉取。"""
    small = make_ohlcv([100, 101, 102])
    cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=True)
    calls: list[int] = []

    def fn():
        calls.append(1)
        return small

    load_klines(fn, "TST.SH", "1d", 3, "forward", cfg)   # 缓存 3 行
    load_klines(fn, "TST.SH", "1d", 10, "forward", cfg)  # 想要 10 行 -> 重取
    assert len(calls) == 2


def test_stale_cache_fallback_on_fetch_error(tmp_path):
    """拉取失败但存在过期缓存时，应回退使用过期缓存。"""
    df = make_ohlcv([100, 101, 102, 103])
    cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=0, enabled=True)

    # 首次成功写入缓存
    load_klines(lambda: df, "TST.SH", "1d", 4, "forward", cfg)

    def failing():
        raise RuntimeError("network down")

    out = load_klines(failing, "TST.SH", "1d", 4, "forward", cfg)
    assert len(out) == 4  # 回退到过期缓存


def test_fetch_error_without_cache_raises(tmp_path):
    cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=True)

    def failing():
        raise RuntimeError("network down")

    with pytest.raises(RuntimeError):
        load_klines(failing, "NOPE.SH", "1d", 5, "forward", cfg)


def test_different_adjust_uses_separate_cache(tmp_path):
    """不同复权口径应各自独立缓存，互不覆盖。"""
    qfq = make_ohlcv([10, 11, 12])
    hfq = make_ohlcv([100, 110, 120])
    cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=True)

    out_q = load_klines(lambda: qfq, "TST.SH", "1d", 3, "forward", cfg)
    out_h = load_klines(lambda: hfq, "TST.SH", "1d", 3, "backward", cfg)

    assert out_q["close"].iloc[-1] == 12
    assert out_h["close"].iloc[-1] == 120


def test_default_ttl_graded_by_period(monkeypatch):
    """TTL 分级：分钟级 30 分钟、日级及以上 1 天。"""
    monkeypatch.delenv("ALPHA_FORGE_CACHE_TTL", raising=False)
    assert default_config("5m").ttl_seconds == MINUTE_TTL
    assert default_config("60m").ttl_seconds == MINUTE_TTL
    assert default_config("1d").ttl_seconds == DEFAULT_TTL
    assert default_config("1w").ttl_seconds == DEFAULT_TTL
    assert default_config("1M").ttl_seconds == DEFAULT_TTL


def test_explicit_ttl_env_overrides_grading(monkeypatch):
    """ALPHA_FORGE_CACHE_TTL 显式设置时全局覆盖分级默认值。"""
    monkeypatch.setenv("ALPHA_FORGE_CACHE_TTL", "77")
    assert default_config("5m").ttl_seconds == 77
    assert default_config("1d").ttl_seconds == 77


# ---------------------------------------------------------------- 增量更新


def _recent_df(n: int = 30, end_days_ago: int = 3, base: float = 100.0) -> pd.DataFrame:
    """构造结束于近日的日 K（增量更新依赖「距今天数」估算缺口）。"""
    end = pd.Timestamp.now().normalize() - pd.Timedelta(days=end_days_ago)
    dates = pd.date_range(end=end, periods=n, freq="B")
    close = np.linspace(base, base + n - 1, n)
    return pd.DataFrame(
        {
            "trade_date": dates,
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(n, 1_000_000.0),
        }
    )


def _forbidden_full_fetch():
    raise AssertionError("增量路径不应触发全量拉取")


def test_incremental_merges_tail_without_full_fetch(tmp_path):
    """陈旧缓存 + fetch_tail_fn：只拉尾部小段合并，不走全量。"""
    n = 30
    full = _recent_df(n=n + 2, end_days_ago=1)  # 末尾比缓存多 2 根
    cached = full.iloc[:n].reset_index(drop=True)
    cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=True)

    # 先用新鲜缓存落盘，再改用 ttl=0 使其陈旧
    load_klines(lambda: cached, "TST.SH", "1d", n, "forward", cfg)
    stale_cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=0, enabled=True)
    time.sleep(0.01)

    tail_calls: list[int] = []

    def fetch_tail(k: int) -> pd.DataFrame:
        tail_calls.append(k)
        return full.tail(k).reset_index(drop=True)

    out = load_klines(
        _forbidden_full_fetch, "TST.SH", "1d", n, "forward", stale_cfg,
        fetch_tail_fn=fetch_tail,
    )
    assert len(tail_calls) == 1
    assert len(out) == n
    # 尾部已包含新增的最后一根
    assert float(out["close"].iloc[-1]) == float(full["close"].iloc[-1])


def test_incremental_detects_adjustment_revision(tmp_path):
    """重叠区 close 不一致（复权修订）时回退全量重拉。"""
    n = 30
    cached = _recent_df(n=n, end_days_ago=3)
    cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=True)
    load_klines(lambda: cached, "TST.SH", "1d", n, "forward", cfg)
    stale_cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=0, enabled=True)
    time.sleep(0.01)

    revised = _recent_df(n=n + 2, end_days_ago=1, base=90.0)  # 历史价全部变了
    full_calls: list[int] = []

    def full_fetch() -> pd.DataFrame:
        full_calls.append(1)
        return revised

    out = load_klines(
        full_fetch, "TST.SH", "1d", n, "forward", stale_cfg,
        fetch_tail_fn=lambda k: revised.tail(k).reset_index(drop=True),
    )
    assert full_calls == [1]  # 回退全量
    assert float(out["close"].iloc[0]) == pytest.approx(float(revised["close"].iloc[2]))


def test_incremental_no_overlap_falls_back(tmp_path):
    """尾段未覆盖到缓存末日（无法衔接）时回退全量。"""
    n = 30
    cached = _recent_df(n=n, end_days_ago=5)
    cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=True)
    load_klines(lambda: cached, "TST.SH", "1d", n, "forward", cfg)
    stale_cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=0, enabled=True)
    time.sleep(0.01)

    future_only = _recent_df(n=3, end_days_ago=0)  # 全部晚于缓存末日
    full_calls: list[int] = []

    def full_fetch() -> pd.DataFrame:
        full_calls.append(1)
        return cached

    load_klines(
        full_fetch, "TST.SH", "1d", n, "forward", stale_cfg,
        fetch_tail_fn=lambda k: future_only,
    )
    assert full_calls == [1]


def test_incremental_disabled_by_env(tmp_path, monkeypatch):
    """ALPHA_FORGE_INCR_CACHE=0 关闭增量，陈旧缓存直接全量重拉。"""
    monkeypatch.setenv("ALPHA_FORGE_INCR_CACHE", "0")
    n = 10
    cached = _recent_df(n=n, end_days_ago=2)
    cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=True)
    load_klines(lambda: cached, "TST.SH", "1d", n, "forward", cfg)
    stale_cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=0, enabled=True)
    time.sleep(0.01)

    full_calls: list[int] = []

    def full_fetch() -> pd.DataFrame:
        full_calls.append(1)
        return cached

    load_klines(
        full_fetch, "TST.SH", "1d", n, "forward", stale_cfg,
        fetch_tail_fn=lambda k: cached.tail(k),
    )
    assert full_calls == [1]


# ---------------------------------------------------------------- 离线模式


def _forbidden_fetch():
    raise AssertionError("离线模式不应触发任何网络拉取")


def test_offline_reads_stale_cache_without_fetch(tmp_path, monkeypatch):
    """离线模式：过期缓存直接返回，不调用 fetch_fn。"""
    df = make_ohlcv([100, 101, 102, 103])
    cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=0, enabled=True)  # 立即过期
    load_klines(lambda: df, "TST.SH", "1d", 4, "forward", cfg)
    time.sleep(0.01)

    monkeypatch.setenv("ALPHA_FORGE_OFFLINE", "1")
    reset_env_config()
    out = load_klines(_forbidden_fetch, "TST.SH", "1d", 4, "forward", cfg)
    assert len(out) == 4
    assert float(out["close"].iloc[-1]) == 103


def test_offline_insufficient_rows_returns_existing(tmp_path, monkeypatch):
    """离线模式：行数不足 count 时告警但仍返回现有数据。"""
    df = make_ohlcv([100, 101, 102])
    cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=True)
    load_klines(lambda: df, "TST.SH", "1d", 3, "forward", cfg)

    monkeypatch.setenv("ALPHA_FORGE_OFFLINE", "1")
    reset_env_config()
    out = load_klines(_forbidden_fetch, "TST.SH", "1d", 10, "forward", cfg)
    assert len(out) == 3


def test_offline_without_cache_raises_actionable_error(tmp_path, monkeypatch):
    """离线模式无缓存：报错并提示先用 run_sync.py 同步。"""
    monkeypatch.setenv("ALPHA_FORGE_OFFLINE", "1")
    reset_env_config()
    cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=True)
    with pytest.raises(DataFetchError, match="run_sync"):
        load_klines(_forbidden_fetch, "NOPE.SH", "1d", 5, "forward", cfg)


# ---------------------------------------------------------------- 缓存目录解析


def test_resolve_cache_dir_env_first(monkeypatch, tmp_path):
    """ALPHA_FORGE_CACHE_DIR 环境变量优先级最高。"""
    custom = tmp_path / "custom"
    monkeypatch.setenv("ALPHA_FORGE_CACHE_DIR", str(custom))
    assert resolve_cache_dir() == custom


def test_resolve_cache_dir_legacy_when_nonempty(monkeypatch, tmp_path):
    """项目内旧目录存在且非空时继续使用（老用户零迁移）。"""
    monkeypatch.delenv("ALPHA_FORGE_CACHE_DIR", raising=False)
    legacy = tmp_path / "legacy"
    legacy.mkdir(parents=True)
    (legacy / "x.parquet").write_text("x", encoding="utf-8")
    monkeypatch.setattr("data.cache._project_cache_dir", lambda: legacy)
    assert resolve_cache_dir() == legacy


def test_resolve_cache_dir_defaults_to_home(monkeypatch, tmp_path):
    """无环境变量且旧目录不存在/为空时，落到用户主目录默认。"""
    monkeypatch.delenv("ALPHA_FORGE_CACHE_DIR", raising=False)
    monkeypatch.setattr("data.cache._project_cache_dir", lambda: tmp_path / "nonexistent")
    home_dir = tmp_path / "home" / "klines"
    monkeypatch.setattr("data.cache._home_cache_dir", lambda: home_dir)
    assert resolve_cache_dir() == home_dir


def test_resolve_cache_dir_skips_empty_legacy(monkeypatch, tmp_path):
    """旧目录存在但为空：视同全新环境，落到主目录默认。"""
    monkeypatch.delenv("ALPHA_FORGE_CACHE_DIR", raising=False)
    legacy = tmp_path / "legacy_empty"
    legacy.mkdir(parents=True)
    monkeypatch.setattr("data.cache._project_cache_dir", lambda: legacy)
    home_dir = tmp_path / "home" / "klines"
    monkeypatch.setattr("data.cache._home_cache_dir", lambda: home_dir)
    assert resolve_cache_dir() == home_dir


# ---------------------------------------------------------------------------
# 表格/JSON 快照缓存（load_table / load_json_obj，本地优先）
# ---------------------------------------------------------------------------


def _snapshot_df() -> pd.DataFrame:
    return pd.DataFrame({"code": ["AAPL.US", "MSFT.US"], "pe": [10.0, 20.0]})


class TestLoadTable:
    def test_hit_within_ttl(self, tmp_path):
        """TTL 内第二次读取命中本地，不再调用远端。"""
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=True)
        calls: list[int] = []
        fn = _counter_fetch(_snapshot_df(), calls)
        out1 = load_table(fn, "us_spot_em", cfg)
        out2 = load_table(fn, "us_spot_em", cfg)
        assert len(calls) == 1
        assert list(out1["code"]) == list(out2["code"]) == ["AAPL.US", "MSFT.US"]

    def test_expiry_refetches(self, tmp_path):
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=0, enabled=True)  # 立即过期
        calls: list[int] = []
        fn = _counter_fetch(_snapshot_df(), calls)
        load_table(fn, "us_spot_em", cfg)
        load_table(fn, "us_spot_em", cfg)
        assert len(calls) == 2

    def test_stale_fallback_on_fetch_failure(self, tmp_path):
        """远端失败（返 None/抛异常）时回退陈旧缓存而非返空。"""
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=0, enabled=True)
        load_table(lambda: _snapshot_df(), "us_spot_em", cfg)  # 先落盘
        out_none = load_table(lambda: None, "us_spot_em", cfg)
        assert out_none is not None and len(out_none) == 2

        def _boom():
            raise ConnectionError("limited")

        out_boom = load_table(_boom, "us_spot_em", cfg)
        assert out_boom is not None and len(out_boom) == 2

    def test_no_cache_no_fallback_returns_none(self, tmp_path):
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=True)
        assert load_table(lambda: None, "missing", cfg) is None

    def test_offline_reads_local_only(self, tmp_path, monkeypatch):
        """离线模式：忽略 TTL 只读本地，不触发远端；无缓存返 None。"""
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=0, enabled=True)
        load_table(lambda: _snapshot_df(), "us_spot_em", cfg)
        monkeypatch.setenv("ALPHA_FORGE_OFFLINE", "1")
        reset_env_config()
        calls: list[int] = []
        out = load_table(_counter_fetch(_snapshot_df(), calls), "us_spot_em", cfg)
        assert len(calls) == 0 and out is not None and len(out) == 2
        assert load_table(_counter_fetch(_snapshot_df(), calls), "other", cfg) is None
        assert len(calls) == 0

    def test_disabled_passthrough(self, tmp_path):
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=False)
        calls: list[int] = []
        fn = _counter_fetch(_snapshot_df(), calls)
        load_table(fn, "us_spot_em", cfg)
        load_table(fn, "us_spot_em", cfg)
        assert len(calls) == 2


class TestLoadJsonObj:
    def test_hit_and_stale_fallback(self, tmp_path):
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=True)
        calls: list[int] = []

        def _fn():
            calls.append(1)
            return {"roe": 15.0, "debt_ratio": None}

        out1 = load_json_obj(_fn, "astock_detail_600000", cfg)
        out2 = load_json_obj(_fn, "astock_detail_600000", cfg)
        assert len(calls) == 1
        assert out1 == out2 == {"roe": 15.0, "debt_ratio": None}

        # 过期 + 远端失败 → 陈旧回退
        cfg_expired = CacheConfig(cache_dir=tmp_path, ttl_seconds=0, enabled=True)
        out3 = load_json_obj(lambda: None, "astock_detail_600000", cfg_expired)
        assert out3 == {"roe": 15.0, "debt_ratio": None}

    def test_none_not_cached(self, tmp_path):
        """远端返 None 不写缓存：下次仍会重试远端。"""
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=True)
        assert load_json_obj(lambda: None, "missing", cfg) is None
        calls: list[int] = []

        def _fn():
            calls.append(1)
            return {"ok": 1}

        assert load_json_obj(_fn, "missing", cfg) == {"ok": 1}
        assert len(calls) == 1

    def test_offline_reads_local_only(self, tmp_path, monkeypatch):
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=0, enabled=True)
        load_json_obj(lambda: {"ok": 1}, "detail", cfg)
        monkeypatch.setenv("ALPHA_FORGE_OFFLINE", "1")
        reset_env_config()
        assert load_json_obj(lambda: {"ok": 2}, "detail", cfg) == {"ok": 1}
        assert load_json_obj(lambda: {"ok": 2}, "absent", cfg) is None


class TestScreenerDataLocalFirst:
    """screener 数据层与本地优先缓存的集成（远端仅首拉一次）。"""

    def test_us_snapshot_cached(self, tmp_path, monkeypatch):
        import screener.data as sdata

        monkeypatch.setenv("ALPHA_FORGE_CACHE_DIR", str(tmp_path))
        calls: list[int] = []

        def _remote(log=None, page_size=200, pause=0.3):
            calls.append(1)
            return _snapshot_df().assign(
                name=["Apple", "Microsoft"], close=[100.0, 200.0],
                pb=[None, None], total_mv=[30000.0, 30000.0], div_yield=[None, None],
            )

        monkeypatch.setattr(sdata, "_fetch_us_snapshot_remote", _remote)
        out1 = sdata.fetch_us_snapshot()
        out2 = sdata.fetch_us_snapshot()
        assert len(calls) == 1
        assert len(out1) == len(out2) == 2

    def test_sp500_symbols_cached(self, tmp_path, monkeypatch):
        import screener.data as sdata

        monkeypatch.setenv("ALPHA_FORGE_CACHE_DIR", str(tmp_path))
        calls: list[int] = []

        def _remote(log=None):
            calls.append(1)
            return ["AAPL.US", "BRK-B.US"]

        monkeypatch.setattr(sdata, "_fetch_sp500_symbols_remote", _remote)
        assert sdata.fetch_sp500_symbols() == ["AAPL.US", "BRK-B.US"]
        assert sdata.fetch_sp500_symbols() == ["AAPL.US", "BRK-B.US"]
        assert len(calls) == 1
