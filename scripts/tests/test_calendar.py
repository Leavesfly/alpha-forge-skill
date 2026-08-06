"""交易日历测试（离线，注入固定 now 并 monkeypatch 掉 akshare 权威列表）。"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from data import calendar as cal


@pytest.fixture(autouse=True)
def _no_authoritative_calendar(monkeypatch):
    """默认屏蔽 akshare 权威列表，强制走内置静态表（不触网）。"""
    monkeypatch.setattr(cal, "_cn_trading_days", lambda: None)


def _session(market: str, now: str):
    return cal.last_closed_session(market, pd.Timestamp(now))


class TestMarketOf:
    @pytest.mark.parametrize(
        "symbol,expected",
        [
            ("600000.SH", "CN"),
            ("000001.SZ", "CN"),
            ("430047.BJ", "CN"),
            ("00700.HK", "HK"),
            ("AAPL.US", "US"),
            ("cu2501.SHF", None),
            ("", None),
        ],
    )
    def test_market_of(self, symbol, expected):
        assert cal.market_of(symbol) == expected


class TestCloseBoundary:
    def test_before_close_returns_previous_day(self):
        """周二 10:00（未收盘）→ 基准日应是周一。"""
        info = _session("CN", "2025-06-10 10:00")  # 周二
        assert info.last_closed == pd.Timestamp("2025-06-09")

    def test_after_close_returns_same_day(self):
        """周二 15:31（收盘 + 30 分钟缓冲后）→ 基准日是当天。"""
        info = _session("CN", "2025-06-10 15:31")
        assert info.last_closed == pd.Timestamp("2025-06-10")

    def test_exactly_at_close_still_previous(self):
        """收盘瞬间数据往往未落库，缓冲内仍算前一交易日。"""
        info = _session("CN", "2025-06-10 15:00")
        assert info.last_closed == pd.Timestamp("2025-06-09")


class TestWeekendAndHoliday:
    def test_saturday_returns_friday(self):
        info = _session("CN", "2025-06-14 12:00")  # 周六
        assert info.last_closed == pd.Timestamp("2025-06-13")

    def test_sunday_returns_friday(self):
        info = _session("CN", "2025-06-15 20:00")  # 周日
        assert info.last_closed == pd.Timestamp("2025-06-13")

    def test_monday_morning_returns_friday(self):
        """这正是墙钟 TTL 出错的场景：周一盘前应回到上周五。"""
        info = _session("CN", "2025-06-16 09:00")
        assert info.last_closed == pd.Timestamp("2025-06-13")

    def test_cn_national_holiday_skipped(self):
        """2025 国庆 10/1-10/8 休市，10/9 盘前应回到 9/30。"""
        info = _session("CN", "2025-10-09 09:00")
        assert info.last_closed == pd.Timestamp("2025-09-30")

    def test_cn_spring_festival_skipped(self):
        """2025 春节 1/28-2/4 休市，2/5 盘前应回到 1/27。"""
        info = _session("CN", "2025-02-05 09:00")
        assert info.last_closed == pd.Timestamp("2025-01-27")

    def test_hk_christmas_skipped(self):
        """港股 12/25-12/26 休市，12/26 收盘后应回到 12/24。"""
        info = _session("HK", "2025-12-26 20:00")
        assert info.last_closed == pd.Timestamp("2025-12-24")


class TestUsHolidays:
    def test_us_thanksgiving(self):
        """2025 感恩节 11/27（11 月第 4 个周四），当日收盘后回到 11/26。"""
        info = _session("US", "2025-11-27 20:00")
        assert info.last_closed == pd.Timestamp("2025-11-26")

    def test_us_independence_day_observed(self):
        """2026 独立日 7/4 是周六，顺延到 7/3（周五）休市。"""
        info = _session("US", "2026-07-03 20:00")
        assert info.last_closed == pd.Timestamp("2026-07-02")

    def test_us_good_friday(self):
        """2025 复活节 4/20，Good Friday 为 4/18 休市。"""
        assert "2025-04-18" in cal._us_holidays(2025)

    def test_us_mlk_and_memorial(self):
        holidays = cal._us_holidays(2025)
        assert "2025-01-20" in holidays  # MLK：1 月第 3 个周一
        assert "2025-05-26" in holidays  # Memorial：5 月最后一个周一

    def test_us_new_year_observed_forward(self):
        """2022 元旦是周六，NYSE 提前到 2021-12-31 休市。"""
        assert "2021-12-31" in cal._us_holidays(2022)


class TestAuthoritative:
    def test_static_table_is_not_authoritative(self):
        info = _session("CN", "2025-06-10 16:00")
        assert info.authoritative is False
        assert info.source == "builtin"

    def test_akshare_list_is_authoritative(self, monkeypatch):
        monkeypatch.setattr(
            cal, "_cn_trading_days", lambda: {"2025-06-09", "2025-06-10"}
        )
        info = _session("CN", "2025-06-10 16:00")
        assert info.authoritative is True
        assert info.source == "akshare"
        assert info.last_closed == pd.Timestamp("2025-06-10")

    def test_authoritative_list_skips_unknown_days(self, monkeypatch):
        """权威列表里没有的日期一律跳过，无需内置假日表。"""
        monkeypatch.setattr(cal, "_cn_trading_days", lambda: {"2025-06-03"})
        info = _session("CN", "2025-06-10 16:00")
        assert info.last_closed == pd.Timestamp("2025-06-03")


class TestRobustness:
    def test_unknown_market_returns_none(self):
        assert cal.last_closed_session("SHF", pd.Timestamp("2025-06-10 16:00")) is None

    def test_exhausted_lookback_returns_none(self, monkeypatch):
        """权威列表为空集时回溯耗尽返回 None，调用方回退 TTL。"""
        monkeypatch.setattr(cal, "_cn_trading_days", lambda: {"1990-01-01"})
        assert cal.last_closed_session("CN", pd.Timestamp("2025-06-10 16:00")) is None

    def test_tz_aware_input_converted(self):
        """带时区入参转换到市场时区：UTC 08:00 = 北京 16:00，已收盘。"""
        info = cal.last_closed_session("CN", pd.Timestamp("2025-06-10 08:00", tz="UTC"))
        assert info.last_closed == pd.Timestamp("2025-06-10")

    def test_result_is_tz_naive(self):
        info = _session("CN", "2025-06-10 16:00")
        assert info.last_closed.tzinfo is None

    def test_now_defaults_to_current_time(self):
        """不传 now 时用当前时间，结果必须是过去的某个交易日。"""
        info = cal.last_closed_session("CN")
        assert info is not None
        assert info.last_closed <= pd.Timestamp.now().normalize()


class TestDateHelpers:
    def test_nth_weekday(self):
        assert cal._nth_weekday(2025, 1, 0, 3) == dt.date(2025, 1, 20)

    def test_last_weekday(self):
        assert cal._last_weekday(2025, 5, 0) == dt.date(2025, 5, 26)

    def test_last_weekday_december(self):
        """12 月要跨年取次月 1 日，验证不越界。"""
        assert cal._last_weekday(2025, 12, 0) == dt.date(2025, 12, 29)

    def test_easter(self):
        assert cal._easter(2025) == dt.date(2025, 4, 20)
        assert cal._easter(2024) == dt.date(2024, 3, 31)

    def test_observed(self):
        assert cal._observed(dt.date(2026, 7, 4)) == dt.date(2026, 7, 3)  # 周六→周五
        assert cal._observed(dt.date(2027, 7, 4)) == dt.date(2027, 7, 5)  # 周日→周一
        assert cal._observed(dt.date(2025, 7, 4)) == dt.date(2025, 7, 4)  # 周五不变
