"""Phase 2 P1 修复测试：源级熔断、actual_source 审计、原子写与自愈、非 K 线缓存。"""

from __future__ import annotations

import json
import threading

import pandas as pd
import pytest

from data import health
from data.cache import (
    CacheConfig,
    _safe_read_df,
    _write_df,
    config_with_ttl,
    load_json_obj,
    load_klines,
)
from envconfig import reset_env_config
from tests.helpers import make_ohlcv


@pytest.fixture(autouse=True)
def _reset_health():
    health.reset()
    yield
    health.reset()


class _Src:
    """最小数据源替身。"""

    def __init__(self, name: str):
        self.name = name


class TestCircuitBreaker:
    def test_not_tripped_before_threshold(self):
        for _ in range(2):  # 默认阈值 3
            health.record_failure("openbb")
        assert health.is_tripped("openbb") is False

    def test_tripped_at_threshold(self):
        for _ in range(3):
            health.record_failure("openbb")
        assert health.is_tripped("openbb") is True

    def test_success_resets_counter(self):
        for _ in range(3):
            health.record_failure("openbb")
        health.record_success("openbb")
        assert health.is_tripped("openbb") is False

    def test_custom_threshold(self, monkeypatch):
        monkeypatch.setenv("ALPHA_FORGE_SOURCE_FAILFAST", "1")
        reset_env_config()
        health.record_failure("yfinance")
        assert health.is_tripped("yfinance") is True

    def test_zero_disables_breaker(self, monkeypatch):
        monkeypatch.setenv("ALPHA_FORGE_SOURCE_FAILFAST", "0")
        reset_env_config()
        for _ in range(99):
            health.record_failure("yfinance")
        assert health.is_tripped("yfinance") is False

    def test_filter_removes_tripped(self, capsys):
        a, b = _Src("openbb"), _Src("tickflow")
        for _ in range(3):
            health.record_failure("openbb")
        assert health.filter_sources([a, b]) == [b]
        assert "openbb" in capsys.readouterr().err

    def test_filter_warns_once_per_source(self, capsys):
        a, b = _Src("openbb"), _Src("tickflow")
        for _ in range(3):
            health.record_failure("openbb")
        health.filter_sources([a, b])
        capsys.readouterr()
        health.filter_sources([a, b])
        assert capsys.readouterr().err == ""

    def test_all_tripped_falls_back_to_full_chain(self):
        """保底规则：全部熔断时返回原列表，不能让全市场扫描一次性全灭。"""
        a, b = _Src("openbb"), _Src("tickflow")
        for name in ("openbb", "tickflow"):
            for _ in range(3):
                health.record_failure(name)
        assert health.filter_sources([a, b]) == [a, b]

    def test_empty_list_passthrough(self):
        assert health.filter_sources([]) == []

    def test_snapshot(self):
        for _ in range(3):
            health.record_failure("openbb")
        health.record_failure("akshare")
        snap = health.snapshot()
        assert snap.failures == {"openbb": 3, "akshare": 1}
        assert snap.tripped == ["openbb"]
        assert snap.threshold == 3

    def test_thread_safe_counting(self):
        """sync.py 是多线程，计数必须无丢失。"""

        def _bump():
            for _ in range(100):
                health.record_failure("openbb")

        threads = [threading.Thread(target=_bump) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert health.snapshot().failures["openbb"] == 400


class TestActualSource:
    def test_actual_source_written_to_meta(self, tmp_path):
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600)

        def fetch():
            df = make_ohlcv([1.0, 2.0, 3.0])
            df.attrs["actual_source"] = "baostock"
            return df

        load_klines(fetch, "600000.SH", "1d", count=3, adjust="forward", config=cfg)
        meta = json.loads(
            next(tmp_path.glob("*.meta.json")).read_text(encoding="utf-8")
        )
        # source 是配置标签（缓存键），actual_source 是实际命中的源
        assert meta["source"] == "auto"
        assert meta["actual_source"] == "baostock"

    def test_incremental_rejects_cross_source(self, tmp_path, capsys):
        """缓存来自 A 源、尾段来自 B 源时必须回退全量（复权基准可能不同）。"""
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=0)  # 立刻陈旧
        # 数据末日必须贴近当下，否则估算缺口超 INCR_MAX_TAIL 会提前回退全量，
        # 根本跑不到同源校验那一步
        start = (pd.Timestamp.now().normalize() - pd.tseries.offsets.BDay(31)).strftime(
            "%Y-%m-%d"
        )
        base = make_ohlcv([10.0] * 30, start=start)

        def fetch_full():
            df = base.copy()
            df.attrs["actual_source"] = "akshare"
            return df

        load_klines(
            fetch_full, "600000.SH", "1d", count=30, adjust="forward", config=cfg
        )
        capsys.readouterr()

        full_calls: list[int] = []

        def fetch_full2():
            full_calls.append(1)
            df = base.copy()
            df.attrs["actual_source"] = "openbb"
            return df

        def fetch_tail(n):
            df = base.tail(n).reset_index(drop=True).copy()
            df.attrs["actual_source"] = "openbb"  # 换源了
            return df

        load_klines(
            fetch_full2,
            "600000.SH",
            "1d",
            count=30,
            adjust="forward",
            config=cfg,
            fetch_tail_fn=fetch_tail,
        )
        err = capsys.readouterr().err
        assert "复权基准可能不同" in err
        assert len(full_calls) == 1  # 确实回退了全量


class TestAtomicWriteAndSelfHeal:
    def test_no_tmp_files_left(self, tmp_path):
        base = tmp_path / "k"
        _write_df(make_ohlcv([1.0, 2.0]), base)
        assert list(tmp_path.glob("*.tmp")) == []

    def test_format_switch_cleans_other_file(self, tmp_path):
        """parquet↔pickle 切换时不留另一种格式的残留文件。"""
        base = tmp_path / "k"
        base.with_suffix(".pkl").write_bytes(b"stale-pickle")
        fmt = _write_df(make_ohlcv([1.0, 2.0]), base)
        if fmt == "parquet":
            assert not base.with_suffix(".pkl").exists()

    def test_safe_read_returns_none_on_corruption(self, tmp_path):
        base = tmp_path / "k"
        base.with_suffix(".pkl").write_bytes(b"not-a-pickle")
        assert _safe_read_df(base, "pickle") is None

    def test_safe_read_returns_none_on_missing(self, tmp_path):
        assert _safe_read_df(tmp_path / "nope", "pickle") is None

    def test_corrupt_cache_self_heals(self, tmp_path, capsys):
        """坏缓存不该让此后每次运行都崩：删除后重新拉取。"""
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600)
        df = make_ohlcv([1.0, 2.0, 3.0])
        calls: list[int] = []

        def fetch():
            calls.append(1)
            return df

        load_klines(fetch, "600000.SH", "1d", count=3, adjust="forward", config=cfg)
        assert len(calls) == 1

        # 破坏数据文件（模拟写入中断/磁盘损坏）
        for path in list(tmp_path.glob("*.parquet")) + list(tmp_path.glob("*.pkl")):
            path.write_bytes(b"corrupted")

        out = load_klines(
            fetch, "600000.SH", "1d", count=3, adjust="forward", config=cfg
        )
        assert len(out) == 3
        assert len(calls) == 2  # 自愈后重拉了
        assert "不可读" in capsys.readouterr().err

    def test_meta_write_is_atomic(self, tmp_path):
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600)
        load_klines(
            lambda: make_ohlcv([1.0, 2.0]),
            "600000.SH",
            "1d",
            count=2,
            adjust="forward",
            config=cfg,
        )
        assert list(tmp_path.glob("*.tmp")) == []
        meta = next(tmp_path.glob("*.meta.json"))
        json.loads(meta.read_text(encoding="utf-8"))  # 内容完整可解析


class TestConfigWithTtl:
    def test_overrides_default_ttl(self):
        assert config_with_ttl(7 * 86400).ttl_seconds == 7 * 86400

    def test_user_explicit_ttl_wins(self, monkeypatch):
        """用户显式设置 ALPHA_FORGE_CACHE_TTL 时不被模块默认值覆盖。"""
        monkeypatch.setenv("ALPHA_FORGE_CACHE_TTL", "60")
        assert config_with_ttl(7 * 86400).ttl_seconds == 60

    def test_respects_no_cache(self, monkeypatch):
        monkeypatch.setenv("ALPHA_FORGE_NO_CACHE", "1")
        assert config_with_ttl(3600).enabled is False


class TestNonKlineCaching:
    """非 K 线数据（宏观/估值/分红/财务）接入缓存后的行为。"""

    def test_macro_snapshot_cached(self, monkeypatch):
        from data import macro

        calls: list[int] = []

        def _remote():
            calls.append(1)
            return macro.MacroSnapshot(bond_yield_10y=2.65, cpi_yoy=0.3, pmi=50.1)

        monkeypatch.setattr(macro, "_fetch_macro_snapshot_remote", _remote)
        first = macro.fetch_macro_snapshot()
        second = macro.fetch_macro_snapshot()
        assert len(calls) == 1
        assert first.bond_yield_10y == second.bond_yield_10y == 2.65
        assert second.pmi == 50.1

    def test_macro_failure_not_cached(self, monkeypatch):
        """三项全失败不写缓存——否则失败结果会被锁存 12 小时。"""
        from data import macro

        calls: list[int] = []

        def _remote():
            calls.append(1)
            return macro.MacroSnapshot(errors=["网络不可用"])

        monkeypatch.setattr(macro, "_fetch_macro_snapshot_remote", _remote)
        macro.fetch_macro_snapshot()
        macro.fetch_macro_snapshot()
        assert len(calls) == 2  # 每次都重试，没有缓存失败

    def test_macro_roundtrip_preserves_fields(self):
        from data.macro import MacroSnapshot

        snap = MacroSnapshot(
            bond_yield_10y=2.65,
            bond_yield_trend="rising",
            cpi_yoy=0.3,
            cpi_trend="flat",
            pmi=50.1,
            pmi_trend="falling",
            asof="2026-08-01",
            errors=["x"],
        )
        back = MacroSnapshot.from_dict(snap.to_dict())
        assert back == snap

    def test_valuation_cached_per_symbol_and_years(self, monkeypatch):
        from data import valuation

        calls: list[tuple] = []

        def _remote(symbol, years):
            calls.append((symbol, years))
            return valuation.ValuationPercentile(
                symbol=symbol,
                pe_current=10.0,
                pb_current=1.0,
                pe_percentile=0.2,
                pb_percentile=0.3,
                n_samples=100,
                lookback_years=float(years),
                source="akshare",
            )

        monkeypatch.setattr(valuation, "_fetch_valuation_remote", _remote)
        valuation.fetch_valuation_percentile("600000.SH", 5)
        valuation.fetch_valuation_percentile("600000.SH", 5)
        assert len(calls) == 1
        # 不同回看年数是不同的缓存键
        valuation.fetch_valuation_percentile("600000.SH", 3)
        assert len(calls) == 2
        # 不同标的也是
        valuation.fetch_valuation_percentile("000001.SZ", 5)
        assert len(calls) == 3

    def test_valuation_none_not_cached(self, monkeypatch):
        from data import valuation

        calls: list[int] = []

        def _remote(symbol, years):
            calls.append(1)
            return None

        monkeypatch.setattr(valuation, "_fetch_valuation_remote", _remote)
        assert valuation.fetch_valuation_percentile("600000.SH") is None
        assert valuation.fetch_valuation_percentile("600000.SH") is None
        assert len(calls) == 2

    def test_valuation_roundtrip(self):
        from data.valuation import ValuationPercentile

        vp = ValuationPercentile(
            symbol="600000.SH",
            pe_current=5.12,
            pb_current=0.58,
            pe_percentile=0.1234,
            pb_percentile=0.5678,
            n_samples=1200,
            lookback_years=5.0,
            source="akshare",
            note="ok",
        )
        back = ValuationPercentile.from_dict(vp.to_dict())
        assert back == vp

    def test_dividends_cached(self, monkeypatch):
        from data import dividends

        calls: list[int] = []
        series = pd.Series(
            [0.3, 0.5],
            index=pd.DatetimeIndex(["2023-06-15", "2024-06-20"]),
        )

        def _remote(symbol):
            calls.append(1)
            return series

        monkeypatch.setattr(dividends, "_fetch_dividends_remote", _remote)
        first = dividends.fetch_dividends("600000.SH")
        second = dividends.fetch_dividends("600000.SH")
        assert len(calls) == 1
        pd.testing.assert_series_equal(first, second)
        assert list(second.values) == [0.3, 0.5]
        assert isinstance(second.index, pd.DatetimeIndex)

    def test_dividends_error_message_preserved(self, monkeypatch):
        """拉取失败时必须保留具体原因，不能被 load_json_obj 吞成 None。"""
        from data import dividends

        def _remote(symbol):
            raise RuntimeError("600000.SH 无分红记录（可能从未分红）")

        monkeypatch.setattr(dividends, "_fetch_dividends_remote", _remote)
        with pytest.raises(RuntimeError, match="无分红记录"):
            dividends.fetch_dividends("600000.SH")

    def test_fundamentals_symbol_set_key_is_order_insensitive(self):
        from data.fundamentals import _symbols_key

        assert _symbols_key(["A.US", "B.US"]) == _symbols_key(["B.US", "A.US"])
        assert _symbols_key(["A.US"]) != _symbols_key(["B.US"])

    def test_offline_mode_serves_non_kline_from_cache(self, monkeypatch):
        """接入缓存的附带收益：ALPHA_FORGE_OFFLINE 对非 K 线数据也生效。"""
        from data import macro

        calls: list[int] = []

        def _remote():
            calls.append(1)
            return macro.MacroSnapshot(bond_yield_10y=2.65, cpi_yoy=0.3, pmi=50.1)

        monkeypatch.setattr(macro, "_fetch_macro_snapshot_remote", _remote)
        macro.fetch_macro_snapshot()  # 先预热

        monkeypatch.setenv("ALPHA_FORGE_OFFLINE", "1")
        reset_env_config()
        offline_snap = macro.fetch_macro_snapshot()
        assert offline_snap.bond_yield_10y == 2.65
        assert len(calls) == 1  # 离线模式没有触网

    def test_offline_without_cache_returns_empty_snapshot(self, monkeypatch):
        from data import macro

        monkeypatch.setenv("ALPHA_FORGE_OFFLINE", "1")
        reset_env_config()
        snap = macro.fetch_macro_snapshot()
        assert snap.bond_yield_10y is None
        assert snap.errors  # 有可读的原因说明

    def test_load_json_obj_key_isolation(self):
        """不同键互不干扰（回归：缓存键拼接错会串数据）。"""
        cfg = config_with_ttl(3600)
        assert load_json_obj(lambda: {"v": 1}, "key_a", cfg) == {"v": 1}
        assert load_json_obj(lambda: {"v": 2}, "key_b", cfg) == {"v": 2}
        assert load_json_obj(lambda: {"v": 9}, "key_a", cfg) == {"v": 1}
