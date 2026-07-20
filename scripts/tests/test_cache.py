"""数据缓存层测试：复权归一化与带缓存的 K 线读取。"""

from __future__ import annotations

import time

import pytest

from data.cache import CacheConfig, load_klines, normalize_adjust

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
