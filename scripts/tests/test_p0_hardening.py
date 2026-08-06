"""Phase 1 P0 修复测试：质量校验接入、日历新鲜度判定、baostock 并发锁。"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pandas as pd
import pytest

from data import calendar as cal
from data.cache import CacheConfig, _is_fresh, load_klines
from data.sources import _validate_and_sort
from envconfig import reset_env_config
from errors import DataFetchError, DataQualityError
from tests.helpers import make_ohlcv


def _dirty_df() -> pd.DataFrame:
    """含 error 级问题（负价）的数据。"""
    df = make_ohlcv([100.0, 101.0, 102.0, 103.0, 104.0])
    df.loc[2, "close"] = -1.0
    return df


def _cfg(ttl: int) -> CacheConfig:
    """不落盘的配置替身（_is_fresh 不碰磁盘，目录仅占位）。"""
    return CacheConfig(cache_dir=Path("/nonexistent"), ttl_seconds=ttl)


def _fake_sessions(then_date: str, now_date: str, authoritative: bool = False):
    """构造 last_closed_session 替身。

    不带 now 参数（当下）返回 now_date，带 now 参数（抓取时刻）返回 then_date。
    """

    def _fake(market, now=None):
        date = now_date if now is None else then_date
        return cal.SessionInfo(pd.Timestamp(date), authoritative, "test")

    return _fake


class TestQualityHookInSources:
    """质量校验在 _validate_and_sort 单点接入，五个源共用。"""

    def test_clean_data_attaches_report(self):
        df = _validate_and_sort(make_ohlcv([100.0, 101.0, 102.0]), "600000.SH", "1d")
        assert df.attrs["quality"].passed

    def test_dirty_data_warns_but_passes_through(self, capsys):
        """默认只告警放行——单只标的脏数据不该中断全市场扫描。"""
        out = _validate_and_sort(_dirty_df(), "600000.SH", "1d")
        assert len(out) == 5
        assert out.attrs["quality"].passed is False
        err = capsys.readouterr().err
        assert "nonpositive_price" in err

    def test_strict_mode_raises(self, monkeypatch):
        monkeypatch.setenv("ALPHA_FORGE_STRICT_DATA", "1")
        reset_env_config()
        with pytest.raises(DataQualityError) as exc:
            _validate_and_sort(_dirty_df(), "600000.SH", "1d")
        # 报错必须给出可操作的下一步
        assert "ALPHA_FORGE_STRICT_DATA" in str(exc.value)
        assert "run_verify.py" in str(exc.value)

    def test_strict_mode_allows_warn_level(self, monkeypatch):
        """warn 级（如极端跳空）即使严格模式也不中断。"""
        monkeypatch.setenv("ALPHA_FORGE_STRICT_DATA", "1")
        reset_env_config()
        df = make_ohlcv([100.0, 101.0, 102.0, 103.0, 104.0])
        for col in ("open", "high", "low", "close"):
            df.loc[3:, col] = df.loc[3:, col] * 1.5
        out = _validate_and_sort(df, "600000.SH", "1d")
        assert "extreme_jump" in {i.code for i in out.attrs["quality"].issues}

    def test_no_quality_check_skips(self, monkeypatch, capsys):
        monkeypatch.setenv("ALPHA_FORGE_NO_QUALITY_CHECK", "1")
        reset_env_config()
        out = _validate_and_sort(_dirty_df(), "600000.SH", "1d")
        assert "quality" not in out.attrs
        assert capsys.readouterr().err == ""

    def test_empty_still_raises_datafetch_error(self):
        """空数据仍走原有 DataFetchError，不被质量校验改写。"""
        with pytest.raises(DataFetchError):
            _validate_and_sort(pd.DataFrame(), "600000.SH", "1d")

    def test_missing_close_still_raises_datafetch_error(self):
        with pytest.raises(DataFetchError):
            _validate_and_sort(pd.DataFrame({"open": [1.0]}), "600000.SH", "1d")


class TestQualityScope:
    """质量校验只针对实际返回的行（tail 参数）。

    真实质量事故：yfinance/openbb 整段拉 period="max" 再截尾，若先校验
    全历史，Yahoo 上世纪的陈年数据会把当下干净的 60 根 K 线误判为有问题
    （且严格模式下直接报错、源被弃用）。
    """

    def test_tail_truncates_before_check(self):
        df = _dirty_df()  # 前段含负价（第 3 行）
        out = _validate_and_sort(df, "600000.SH", "1d", tail=2)
        assert len(out) == 2
        assert out.attrs["quality"].passed  # 返回的两行是干净的

    def test_dirty_row_inside_tail_still_caught(self):
        out = _validate_and_sort(_dirty_df(), "600000.SH", "1d", tail=4)
        assert out.attrs["quality"].passed is False

    def test_tail_larger_than_data_keeps_all(self):
        out = _validate_and_sort(make_ohlcv([1.0, 2.0, 3.0]), "600000.SH", "1d", tail=99)
        assert len(out) == 3

    def test_strict_mode_not_tripped_by_history_outside_window(self, monkeypatch):
        monkeypatch.setenv("ALPHA_FORGE_STRICT_DATA", "1")
        reset_env_config()
        out = _validate_and_sort(_dirty_df(), "600000.SH", "1d", tail=2)
        assert len(out) == 2  # 不该因窗口外的历史脏数据报错

    def test_index_reset_after_tail(self):
        out = _validate_and_sort(make_ohlcv([1.0, 2.0, 3.0, 4.0]), "600000.SH", "1d", tail=2)
        assert list(out.index) == [0, 1]


def _meta(fetched_at: float, **extra) -> dict:
    base = {"fetched_at": fetched_at, "rows": 100, "format": "pickle"}
    base.update(extra)
    return base


class TestCalendarFreshness:
    """新鲜度判定：口径是「上次抓取后是否又有交易日收盘」。"""

    def test_monday_intraday_with_sunday_cache_is_stale(self, monkeypatch):
        """核心 bug 场景：周日 16:00 抓的缓存，周一 15:31 必须判陈旧。

        墙钟只过了 23.5 小时（TTL 内），但周一已收盘 → 缺当日 K 线。
        """
        sunday = pd.Timestamp("2025-06-15 16:00", tz="Asia/Shanghai").timestamp()
        monkeypatch.setattr(
            cal, "last_closed_session", _fake_sessions("2025-06-13", "2025-06-16")
        )
        assert _is_fresh(_meta(sunday), _cfg(24 * 3600), "600000.SH", "1d") is False

    def test_same_session_is_fresh(self, monkeypatch):
        """同一交易日内重复调用 → 命中缓存。"""
        now = time.time()
        monkeypatch.setattr(
            cal, "last_closed_session", _fake_sessions("2025-06-16", "2025-06-16")
        )
        assert _is_fresh(_meta(now), _cfg(3600), "600000.SH", "1d") is True

    def test_authoritative_calendar_ignores_ttl(self, monkeypatch):
        """权威日历下，TTL 早已过期但无新交易日收盘 → 仍算新鲜（周末不重拉）。"""
        monkeypatch.setattr(
            cal,
            "last_closed_session",
            _fake_sessions("2025-06-13", "2025-06-13", authoritative=True),
        )
        assert _is_fresh(_meta(time.time() - 999999), _cfg(0), "600000.SH", "1d") is True

    def test_heuristic_calendar_requires_ttl_too(self, monkeypatch):
        """启发式日历与 TTL 取「与」：日历判错时最多退回原 TTL 行为。"""
        monkeypatch.setattr(
            cal,
            "last_closed_session",
            _fake_sessions("2025-06-13", "2025-06-13", authoritative=False),
        )
        assert (
            _is_fresh(_meta(time.time() - 999999), _cfg(0), "600000.SH", "1d") is False
        )

    def test_legacy_meta_without_fetched_at_falls_back(self):
        """旧缓存 meta 缺字段时回退 TTL，零迁移共存。"""
        assert _is_fresh({"rows": 10}, _cfg(3600), "600000.SH", "1d") is False

    def test_unsupported_market_falls_back_to_ttl(self):
        """期货无日历支持 → 纯 TTL。"""
        assert _is_fresh(_meta(time.time()), _cfg(3600), "cu2501.SHF", "1d") is True

    def test_minute_period_uses_ttl_only(self, monkeypatch):
        """分钟级周期不适用日历（盘中持续更新），走墙钟 TTL。"""
        called = []
        monkeypatch.setattr(
            cal, "last_closed_session", lambda *a, **k: called.append(1)
        )
        assert _is_fresh(_meta(time.time()), _cfg(1800), "600000.SH", "5m") is True
        assert called == []

    def test_halted_symbol_still_cacheable(self, tmp_path):
        """停牌/退市标的（数据停在很久以前）仍应命中缓存，不能每次重拉。

        这是「按会话口径」而非「数据到没到最新交易日」的关键收益。
        """
        old = make_ohlcv([10.0] * 20, start="2015-01-01")
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600)
        calls: list[int] = []

        def fetch():
            calls.append(1)
            return old

        load_klines(fetch, "600001.SH", "1d", count=20, adjust="forward", config=cfg)
        load_klines(fetch, "600001.SH", "1d", count=20, adjust="forward", config=cfg)
        assert len(calls) == 1



class TestMetaLastBarDate:
    def test_last_bar_date_written(self, tmp_path):
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600)
        df = make_ohlcv([100.0, 101.0, 102.0], start="2024-03-01")
        load_klines(lambda: df, "600000.SH", "1d", count=3, adjust="forward", config=cfg)
        meta_files = list(tmp_path.glob("*.meta.json"))
        assert len(meta_files) == 1
        meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
        assert meta["last_bar_date"] == "2024-03-05"

    def test_last_bar_date_none_without_date_column(self, tmp_path):
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600)
        df = pd.DataFrame({"close": [1.0, 2.0]})
        load_klines(lambda: df, "600000.SH", "1d", count=2, adjust="forward", config=cfg)
        meta = json.loads(
            next(tmp_path.glob("*.meta.json")).read_text(encoding="utf-8")
        )
        assert meta["last_bar_date"] is None


class TestBaostockLock:
    def test_login_logout_serialized(self, monkeypatch):
        """并发调用时 login/query/logout 必须整段串行，否则会互相掐断。

        用计数器检测重入：任意时刻只允许一个线程处于 login 与 logout 之间。
        """
        from data import sources

        state = {"inside": 0, "max_inside": 0}
        state_lock = threading.Lock()

        class _FakeResult:
            error_code = "0"
            error_msg = ""

            def __init__(self):
                self._left = 2

            def next(self):
                self._left -= 1
                return self._left > 0

            def get_row_data(self):
                return ["2024-01-02", "1", "2", "0.5", "1.5", "100"]

        class _FakeBs:
            def login(self):
                with state_lock:
                    state["inside"] += 1
                    state["max_inside"] = max(state["max_inside"], state["inside"])
                time.sleep(0.01)  # 放大竞态窗口
                return _FakeResult()

            def logout(self):
                with state_lock:
                    state["inside"] -= 1
                return _FakeResult()

            def query_history_k_data_plus(self, *a, **k):
                time.sleep(0.01)
                return _FakeResult()

        monkeypatch.setitem(sys.modules, "baostock", _FakeBs())
        source = sources.BaostockSource()

        threads = [
            threading.Thread(
                target=lambda: source.fetch("600000.SH", "1d", 1, "forward")
            )
            for _ in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert state["max_inside"] == 1, "baostock 全局连接被并发重入"
