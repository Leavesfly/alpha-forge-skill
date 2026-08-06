"""OHLCV 数据质量校验测试（离线，用合成数据构造各类脏数据）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.quality import QualityReport, validate_ohlcv
from tests.helpers import make_ohlcv


def _clean(n: int = 60) -> pd.DataFrame:
    """干净的日 K 数据：小幅随机游走，无任何质量问题。"""
    rng = np.random.default_rng(7)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, size=n)))
    return make_ohlcv(close)


def _codes(report: QualityReport) -> set[str]:
    return {i.code for i in report.issues}


class TestCleanData:
    def test_clean_data_passes(self):
        report = validate_ohlcv(_clean(), "600000.SH", "1d")
        assert report.passed
        assert report.issues == []
        assert report.rows == 60
        assert "通过" in report.summary()

    def test_empty_data_is_error(self):
        report = validate_ohlcv(pd.DataFrame(), "600000.SH", "1d")
        assert not report.passed
        assert _codes(report) == {"empty"}

    def test_to_dict_shape(self):
        report = validate_ohlcv(_clean(), "600000.SH", "1d")
        payload = report.to_dict()
        assert payload["symbol"] == "600000.SH"
        assert payload["passed"] is True
        # 无问题时 issues 为 None，便于 JSON 消费方直接判空
        assert payload["issues"] is None


class TestErrorLevelIssues:
    def test_duplicate_dates(self):
        df = _clean(20)
        df = pd.concat([df, df.iloc[[10]]], ignore_index=True).sort_values("trade_date")
        report = validate_ohlcv(df, "600000.SH", "1d")
        assert not report.passed
        assert "duplicate_dates" in _codes(report)

    def test_nan_in_ohlc(self):
        df = _clean(20)
        df.loc[5, "high"] = np.nan
        report = validate_ohlcv(df, "600000.SH", "1d")
        assert not report.passed
        issue = next(i for i in report.issues if i.code == "nan_values")
        assert issue.count == 1

    def test_nonpositive_price(self):
        df = _clean(20)
        df.loc[7, "low"] = 0.0
        report = validate_ohlcv(df, "600000.SH", "1d")
        assert not report.passed
        assert "nonpositive_price" in _codes(report)

    def test_negative_price(self):
        df = _clean(20)
        df.loc[3, "close"] = -1.0
        report = validate_ohlcv(df, "600000.SH", "1d")
        assert "nonpositive_price" in _codes(report)

    def test_high_below_low(self):
        df = _clean(20)
        df.loc[9, "high"] = df.loc[9, "low"] * 0.5
        report = validate_ohlcv(df, "600000.SH", "1d")
        assert not report.passed
        assert "ohlc_inconsistent" in _codes(report)

    def test_high_not_covering_close(self):
        df = _clean(20)
        df.loc[4, "high"] = df.loc[4, "close"] * 0.9
        report = validate_ohlcv(df, "600000.SH", "1d")
        assert "ohlc_inconsistent" in _codes(report)

    def test_float_rounding_not_reported(self):
        """数据源常见的末位舍入（万分之一内）不应误报为 OHLC 不一致。"""
        df = _clean(20)
        df.loc[6, "high"] = df.loc[6, "close"] * (1 - 1e-6)
        report = validate_ohlcv(df, "600000.SH", "1d")
        assert "ohlc_inconsistent" not in _codes(report)


class TestWarnLevelIssues:
    def test_extreme_jump_astock_main_board(self):
        """A 股主板单日 +50% 超涨跌停，必属数据问题。"""
        df = _clean(20)
        # 整段价格等比放大，保持 OHLC 关系成立，只制造跳空
        for col in ("open", "high", "low", "close"):
            df.loc[10:, col] = df.loc[10:, col] * 1.5
        report = validate_ohlcv(df, "600000.SH", "1d")
        assert "extreme_jump" in _codes(report)
        # warn 级不影响 passed 判定
        assert report.passed

    def test_star_board_allows_20pct(self):
        """科创板 ±20% 涨跌停：15% 波动不应告警，主板同样波动则应告警。"""
        rng = np.random.default_rng(3)
        close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.005, size=30)))
        close[15] = close[14] * 1.15
        df = make_ohlcv(close)
        assert "extreme_jump" not in _codes(validate_ohlcv(df, "688001.SH", "1d"))
        assert "extreme_jump" in _codes(validate_ohlcv(df, "600000.SH", "1d"))

    def test_us_stock_tolerates_large_move(self):
        """美股无涨跌停：30% 单日波动不告警，60%（疑似未复权拆股）才告警。"""
        rng = np.random.default_rng(5)
        close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.005, size=30)))
        close[10:] = close[10:] * 1.3
        assert "extreme_jump" not in _codes(
            validate_ohlcv(make_ohlcv(close), "AAPL.US", "1d")
        )
        close[20:] = close[20:] * 2.0
        assert "extreme_jump" in _codes(
            validate_ohlcv(make_ohlcv(close), "AAPL.US", "1d")
        )

    def test_weekly_period_uses_loose_threshold(self):
        """周 K 无单周期涨跌幅约束，15% 波动不应告警。"""
        rng = np.random.default_rng(11)
        close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.005, size=30)))
        close[15] = close[14] * 1.15
        df = make_ohlcv(close, freq="W")
        assert "extreme_jump" not in _codes(validate_ohlcv(df, "600000.SH", "1w"))

    def test_date_gap(self):
        df = _clean(20)
        # 把后半段整体推后 60 天，制造一个大缺口
        df.loc[10:, "trade_date"] = df.loc[10:, "trade_date"] + pd.Timedelta(days=60)
        report = validate_ohlcv(df, "600000.SH", "1d")
        assert "date_gap" in _codes(report)
        assert report.passed  # warn 不影响判定

    def test_long_holiday_not_reported(self):
        """国庆 8 天长假在 10 天阈值内，不应误报缺口。"""
        df = _clean(20)
        df.loc[10:, "trade_date"] = df.loc[10:, "trade_date"] + pd.Timedelta(days=8)
        assert "date_gap" not in _codes(validate_ohlcv(df, "600000.SH", "1d"))


class TestRobustness:
    def test_missing_date_column_skips_time_checks(self):
        """无时间列时跳过时序类校验，但价格类校验仍生效。"""
        df = _clean(20).drop(columns=["trade_date"])
        df.loc[3, "close"] = -5.0
        report = validate_ohlcv(df, "600000.SH", "1d")
        assert "nonpositive_price" in _codes(report)
        assert "duplicate_dates" not in _codes(report)

    @pytest.mark.filterwarnings("ignore::UserWarning")
    def test_unparsable_date_column(self):
        df = _clean(20)
        df["trade_date"] = "not-a-date"
        report = validate_ohlcv(df, "600000.SH", "1d")
        assert "bad_date_column" in _codes(report)

    def test_single_row_no_jump_check(self):
        df = _clean(1)
        report = validate_ohlcv(df, "600000.SH", "1d")
        assert report.passed

    def test_partial_columns(self):
        """仅有 close 列（部分兜底源的极端情况）不应崩溃。"""
        df = _clean(20)[["trade_date", "close"]]
        report = validate_ohlcv(df, "600000.SH", "1d")
        assert report.passed


@pytest.mark.parametrize(
    "symbol,expected",
    [
        ("600000.SH", 11.0),
        ("688001.SH", 21.0),
        ("300750.SZ", 21.0),
        ("000001.SZ", 11.0),
        ("430047.BJ", 31.0),
        ("00700.HK", 50.0),
        ("AAPL.US", 50.0),
    ],
)
def test_jump_threshold_by_board(symbol, expected):
    from data.quality import _jump_threshold_pct

    assert _jump_threshold_pct(symbol, "1d") == expected
