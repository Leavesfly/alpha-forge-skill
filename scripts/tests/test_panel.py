"""批量面板读取测试：本地缓存宽表装载、缺失清单与 strict 模式。"""

from __future__ import annotations

import pytest

from data.cache import CacheConfig, load_klines
from data.panel import load_panel
from tests.helpers import make_ohlcv


def _warm(cfg: CacheConfig, symbol: str, closes: list[float]) -> None:
    """预写一只标的的缓存（走正常 load_klines 落盘路径）。"""
    df = make_ohlcv(closes)
    load_klines(lambda: df, symbol, "1d", len(closes), "forward", cfg)


def test_load_panel_reads_cached_wide_table(tmp_path):
    cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=True)
    _warm(cfg, "AAA.SH", [10, 11, 12, 13])
    _warm(cfg, "BBB.SZ", [20, 21, 22, 23])

    panel, missing = load_panel(["AAA.SH", "BBB.SZ"], count=4, config=cfg)
    assert missing == []
    assert list(panel.columns) == ["AAA.SH", "BBB.SZ"]
    assert len(panel) == 4
    assert float(panel["AAA.SH"].iloc[-1]) == 13
    assert float(panel["BBB.SZ"].iloc[-1]) == 23


def test_load_panel_missing_symbols_listed(tmp_path):
    cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=True)
    _warm(cfg, "AAA.SH", [10, 11, 12])

    panel, missing = load_panel(["AAA.SH", "NOPE.SZ"], count=3, config=cfg)
    assert missing == ["NOPE.SZ"]
    assert list(panel.columns) == ["AAA.SH"]


def test_load_panel_strict_raises_on_missing(tmp_path):
    cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=True)
    with pytest.raises(RuntimeError, match="run_sync"):
        load_panel(["NOPE.SH"], config=cfg, strict=True)


def test_load_panel_field_selection(tmp_path):
    """field 参数可取 volume 等其他 OHLCV 列。"""
    cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=True)
    _warm(cfg, "AAA.SH", [10, 11, 12])

    panel, missing = load_panel(["AAA.SH"], count=3, field="volume", config=cfg)
    assert missing == []
    assert float(panel["AAA.SH"].iloc[0]) == 1_000_000.0


def test_load_panel_count_limits_tail(tmp_path):
    """count 只取尾部 N 行。"""
    cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=True)
    _warm(cfg, "AAA.SH", [10, 11, 12, 13, 14])

    panel, _ = load_panel(["AAA.SH"], count=2, config=cfg)
    assert len(panel) == 2
    assert float(panel["AAA.SH"].iloc[0]) == 13


def test_load_panel_all_missing_returns_empty(tmp_path):
    cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600, enabled=True)
    panel, missing = load_panel(["X.SH", "Y.SZ"], config=cfg)
    assert panel.empty
    assert missing == ["X.SH", "Y.SZ"]
