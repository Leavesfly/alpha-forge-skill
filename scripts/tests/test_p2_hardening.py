"""Phase 3 P2 修复测试：debug 堆栈、verify 扩源、缓存治理、count 不缩水。"""

from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd
import pytest

from data.cache import (
    CacheConfig,
    _call_fetch,
    _safe_call,
    cache_usage,
    config_with_ttl,
    load_json_obj,
    load_klines,
    prune_cache,
)
from envconfig import reset_env_config
from tests.helpers import make_ohlcv


def _boom():
    raise RuntimeError("上游 502 了")


# ─── P2-8 _safe_call 的 debug 堆栈 ──────────────────────────────────────────────


class TestSafeCallDebug:
    def test_returns_none_and_stays_quiet_by_default(self, capsys, monkeypatch):
        monkeypatch.delenv("ALPHA_FORGE_DEBUG", raising=False)
        reset_env_config()
        assert _safe_call(_boom) is None
        assert capsys.readouterr().err == ""

    def test_prints_traceback_when_debug(self, capsys, monkeypatch):
        monkeypatch.setenv("ALPHA_FORGE_DEBUG", "1")
        reset_env_config()
        assert _safe_call(_boom) is None
        err = capsys.readouterr().err
        assert "上游 502 了" in err
        assert "Traceback" in err  # 有堆栈才能定位到哪一层降级了

    def test_success_passthrough(self, monkeypatch):
        monkeypatch.setenv("ALPHA_FORGE_DEBUG", "1")
        reset_env_config()
        assert _safe_call(lambda: 42) == 42


# ─── P2-9 verify 扩源与同上游告警 ──────────────────────────────────────────────


class _Src:
    """最小 verify 源替身。"""

    def __init__(self, name: str, df: pd.DataFrame, supported: bool = True):
        self.name = name
        self._df = df
        self._supported = supported

    def supports(self, symbol: str, period: str) -> bool:
        return self._supported

    def fetch(self, symbol: str, period: str, count: int, adjust: str) -> pd.DataFrame:
        return self._df


@pytest.fixture
def hkus_df():
    return make_ohlcv(100.0 * (1.0 + 0.001) ** np.arange(60))


class TestVerifySources:
    def test_registry_covers_all_five_sources(self):
        from data.verify import VERIFY_SOURCES

        assert set(VERIFY_SOURCES) == {
            "tickflow",
            "openbb",
            "baostock",
            "akshare",
            "yfinance",
        }

    def test_unknown_source_a_raises(self):
        from data.verify import verify_symbol

        with pytest.raises(RuntimeError, match="未知主源"):
            verify_symbol("600000.SH", source_a_name="nonexist")

    def test_unsupported_source_a_raises(self, monkeypatch, hkus_df):
        import data.verify as vmod

        monkeypatch.setitem(
            vmod.VERIFY_SOURCES, "openbb", lambda: _Src("openbb", hkus_df, supported=False)
        )
        with pytest.raises(RuntimeError, match="主源 openbb 不支持"):
            vmod.verify_symbol("600000.SH", source_a_name="openbb", source_b_name="akshare")

    def test_same_upstream_pair_is_flagged(self, monkeypatch, hkus_df):
        """openbb 与 yfinance 同源于 Yahoo：必须显式标注验证强度有限。"""
        import data.verify as vmod

        monkeypatch.setitem(vmod.VERIFY_SOURCES, "openbb", lambda: _Src("openbb", hkus_df))
        monkeypatch.setitem(vmod.VERIFY_SOURCES, "yfinance", lambda: _Src("yfinance", hkus_df))

        result = vmod.verify_symbol(
            "AAPL.US",
            period="1d",
            count=60,
            source_a_name="openbb",
            source_b_name="yfinance",
        )
        assert result.passed  # 数据一致，但……
        assert any("同源于 Yahoo" in w for w in result.warnings)

    def test_independent_pair_not_flagged(self, monkeypatch, hkus_df):
        import data.verify as vmod

        monkeypatch.setitem(vmod.VERIFY_SOURCES, "tickflow", lambda: _Src("tickflow", hkus_df))
        monkeypatch.setitem(vmod.VERIFY_SOURCES, "akshare", lambda: _Src("akshare", hkus_df))

        result = vmod.verify_symbol(
            "600000.SH", period="1d", count=60, source_b_name="akshare"
        )
        assert not any("同源" in w for w in result.warnings)

    def test_source_a_reported_in_result(self, monkeypatch, hkus_df):
        import data.verify as vmod

        monkeypatch.setitem(vmod.VERIFY_SOURCES, "yfinance", lambda: _Src("yfinance", hkus_df))
        monkeypatch.setitem(vmod.VERIFY_SOURCES, "akshare", lambda: _Src("akshare", hkus_df))

        result = vmod.verify_symbol(
            "AAPL.US",
            period="1d",
            count=60,
            source_a_name="yfinance",
            source_b_name="akshare",
        )
        assert result.source_a == "yfinance"
        assert result.source_b == "akshare"

    def test_cli_exposes_source_a(self):
        from run_verify import build_parser

        args = build_parser().parse_args(["--symbols", "AAPL.US", "--source-a", "openbb"])
        assert args.source_a == "openbb"
        assert build_parser().parse_args(["--symbols", "600000.SH"]).source_a == "tickflow"


# ─── P2-10 缓存治理：用量统计与过期清理 ────────────────────────────────────────


def _seed_kline(tmp_path, symbol="600000.SH", rows=5, fetched_at=None):
    """写一条 K 线缓存；fetched_at 非空时改写 meta 模拟陈旧条目。"""
    cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600)
    load_klines(
        lambda: make_ohlcv(np.linspace(10.0, 11.0, rows)),
        symbol,
        "1d",
        count=rows,
        adjust="forward",
        config=cfg,
    )
    meta_path = next(tmp_path.glob(f"{symbol.replace('.', '_')}*.meta.json"))
    if fetched_at is not None:
        doc = json.loads(meta_path.read_text(encoding="utf-8"))
        doc["fetched_at"] = fetched_at
        meta_path.write_text(json.dumps(doc), encoding="utf-8")
    return cfg, meta_path


class TestCacheUsage:
    def test_missing_dir_reported_not_crashed(self, tmp_path):
        cfg = CacheConfig(cache_dir=tmp_path / "nope", ttl_seconds=3600)
        usage = cache_usage(cfg)
        assert usage["exists"] is False
        assert usage["kline_entries"] == 0
        assert usage["total_mb"] == 0.0

    def test_counts_kline_entries(self, tmp_path):
        cfg, _ = _seed_kline(tmp_path)
        usage = cache_usage(cfg)
        assert usage["exists"] is True
        assert usage["kline_entries"] == 1
        assert usage["table_entries"] == 0
        assert usage["oldest_entry"] == time.strftime("%Y-%m-%d")

    def test_counts_table_entries_separately(self, tmp_path):
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600)
        load_json_obj(lambda: {"pe": 12.0}, "obb_metrics_AAPL_US", cfg)
        usage = cache_usage(cfg)
        assert usage["kline_entries"] == 0
        assert usage["table_entries"] == 1

    def test_meta_not_double_counted(self, tmp_path):
        """meta 与数据文件是同一条目，不能各记一次。"""
        cfg, _ = _seed_kline(tmp_path)
        _seed_kline(tmp_path, symbol="000001.SZ")
        assert cache_usage(cfg)["kline_entries"] == 2


class TestPruneCache:
    def test_keeps_fresh_entries(self, tmp_path):
        cfg, _ = _seed_kline(tmp_path)
        report = prune_cache(30, config=cfg)
        assert report.removed == 0
        assert list(tmp_path.glob("*.meta.json"))  # 文件还在

    def test_removes_stale_entries_with_data_files(self, tmp_path):
        cfg, _ = _seed_kline(tmp_path, fetched_at=time.time() - 200 * 86400)
        report = prune_cache(180, config=cfg)
        assert report.removed == 1
        assert report.freed_bytes > 0
        assert list(tmp_path.glob("*.meta.json")) == []
        assert list(tmp_path.glob("*.parquet")) + list(tmp_path.glob("*.pkl")) == []

    def test_dry_run_reports_without_deleting(self, tmp_path):
        cfg, _ = _seed_kline(tmp_path, fetched_at=time.time() - 200 * 86400)
        report = prune_cache(180, dry_run=True, config=cfg)
        assert report.removed == 1
        assert report.dry_run is True
        assert list(tmp_path.glob("*.meta.json"))  # 没真删

    def test_prunes_inline_json_snapshots(self, tmp_path):
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600)
        load_json_obj(lambda: {"pe": 12.0}, "obb_metrics_AAPL_US", cfg)
        path = tmp_path / "tables" / "obb_metrics_AAPL_US.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["fetched_at"] = time.time() - 400 * 86400
        path.write_text(json.dumps(doc), encoding="utf-8")

        report = prune_cache(180, config=cfg)
        assert report.entries == ["obb_metrics_AAPL_US"]
        assert not path.exists()

    def test_meta_without_fetched_at_is_left_alone(self, tmp_path):
        """无法判定年龄的条目不删——宁可留垃圾也不误删用户数据。"""
        cfg, meta_path = _seed_kline(tmp_path)
        meta_path.write_text(json.dumps({"symbol": "600000.SH"}), encoding="utf-8")
        assert prune_cache(0, config=cfg).removed == 0

    def test_missing_dir_is_noop(self, tmp_path):
        cfg = CacheConfig(cache_dir=tmp_path / "nope", ttl_seconds=3600)
        assert prune_cache(30, config=cfg).removed == 0

    def test_report_to_dict(self, tmp_path):
        cfg, _ = _seed_kline(tmp_path, fetched_at=time.time() - 200 * 86400)
        doc = prune_cache(180, config=cfg).to_dict()
        assert doc["removed"] == 1
        assert doc["freed_mb"] >= 0.0
        assert doc["dry_run"] is False

    def test_cli_accepts_prune_flags(self):
        from run_sync import build_parser

        args = build_parser().parse_args(["--prune-days", "180", "--dry-run"])
        assert args.prune_days == 180
        assert args.dry_run is True
        assert build_parser().parse_args(["--cache-usage"]).cache_usage is True


# ─── P2-11 小请求不把长历史缓存缩水 ────────────────────────────────────────────


class TestCountNoShrink:
    def test_stale_refetch_keeps_cached_length(self, tmp_path):
        """已缓存 100 根、本次只要 10 根：重拉时仍按 100 根拉，避免缓存抖动。"""
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=0)  # 立刻陈旧
        asked: list[int] = []

        def fetch(count):
            asked.append(count)
            return make_ohlcv(np.linspace(10.0, 11.0, count))

        load_klines(fetch, "600000.SH", "1d", count=100, adjust="forward", config=cfg)
        out = load_klines(fetch, "600000.SH", "1d", count=10, adjust="forward", config=cfg)

        assert asked == [100, 100]  # 第二次没缩到 10
        assert len(out) == 10  # 但只返回请求的根数
        meta = json.loads(
            next(tmp_path.glob("*.meta.json")).read_text(encoding="utf-8")
        )
        assert meta["rows"] == 100  # 落盘仍是长历史

    def test_larger_request_wins(self, tmp_path):
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=0)
        asked: list[int] = []

        def fetch(count):
            asked.append(count)
            return make_ohlcv(np.linspace(10.0, 11.0, count))

        load_klines(fetch, "600000.SH", "1d", count=20, adjust="forward", config=cfg)
        load_klines(fetch, "600000.SH", "1d", count=300, adjust="forward", config=cfg)
        assert asked == [20, 300]

    def test_cache_disabled_still_passes_count(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ALPHA_FORGE_NO_CACHE", "1")
        reset_env_config()
        asked: list[int] = []

        def fetch(count):
            asked.append(count)
            return make_ohlcv(np.linspace(10.0, 11.0, count))

        load_klines(
            fetch,
            "600000.SH",
            "1d",
            count=42,
            adjust="forward",
            config=config_with_ttl(3600),
        )
        assert asked == [42]

    def test_legacy_zero_arg_callback_still_works(self, tmp_path):
        """旧调用方传无参 lambda：不能因为新增 count 形参而炸。"""
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600)
        out = load_klines(
            lambda: make_ohlcv([1.0, 2.0, 3.0]),
            "600000.SH",
            "1d",
            count=3,
            adjust="forward",
            config=cfg,
        )
        assert len(out) == 3

    def test_call_fetch_adapts_both_signatures(self):
        assert _call_fetch(lambda: "zero", 7) == "zero"
        assert _call_fetch(lambda n: f"got {n}", 7) == "got 7"

    def test_call_fetch_does_not_swallow_inner_typeerror(self):
        """回调内部的 TypeError 必须原样抛出，不能被签名探测吞掉。"""

        def fetch(count):
            raise TypeError("回调内部自己的类型错误")

        with pytest.raises(TypeError, match="回调内部"):
            _call_fetch(fetch, 5)

    def test_uninspectable_callable_treated_as_zero_arg(self):
        """签名不可内省的可调用对象按无参处理（不崩）。"""

        class _Opaque:
            __signature__ = "不是合法签名"  # 让 inspect.signature 抛错

            def __call__(self):
                return "zero-arg"

        assert _call_fetch(_Opaque(), 5) == "zero-arg"
