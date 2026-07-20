"""CAN SLIM 检查引擎与基本面解析的回归测试（合成数据，不依赖网络）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from canslim import canslim_check, load_fundamentals_csv, rs_weighted_return
from canslim.engine import MIN_BARS
from tests.helpers import make_ohlcv


def _uptrend_df(n: int = 320, daily: float = 0.003, start: str = "2024-01-02") -> pd.DataFrame:
    """单调上行价格（收盘即 52 周新高），volume 常数。"""
    close = 10.0 * (1.0 + daily) ** np.arange(n)
    return make_ohlcv(close, start=start)


def _bench_series(n: int = 320, daily: float = 0.001, start: str = "2024-01-02") -> pd.Series:
    close = 100.0 * (1.0 + daily) ** np.arange(n)
    return pd.Series(close, index=pd.date_range(start=start, periods=n, freq="B"))


def _fundamentals(
    years=(2020, 2021, 2022, 2023, 2024), base: float = 1.0, growth: float = 0.35, roe: float = 0.20
) -> dict:
    """合成累计（YTD）EPS：逐年增长 growth，季度线性累积；ROE 常数。"""
    eps = {}
    for i, y in enumerate(years):
        annual = base * (1.0 + growth) ** i
        for q, (m, d) in enumerate(((3, 31), (6, 30), (9, 30), (12, 31)), start=1):
            eps[pd.Timestamp(y, m, d)] = annual * q / 4.0
    idx = pd.DatetimeIndex(sorted(eps))
    return {
        "eps": pd.Series([eps[t] for t in idx], index=idx),
        "roe": pd.Series(roe, index=idx),
    }


def _by_letter(res) -> dict:
    return {c["letter"]: c for c in res.checks}


def _presplit_fundamentals(growth: float = 0.35) -> dict:
    """预拆分序列（yfinance 港美股口径）：单季 + 年度 EPS，财年末非 12 月。"""
    q_idx = pd.DatetimeIndex([
        pd.Timestamp(y, m, 28)
        for y in (2023, 2024)
        for m in (1, 4, 7, 10)
    ])
    q_vals = [0.25 * (1.0 + growth) ** (i // 4) for i in range(len(q_idx))]
    a_idx = pd.DatetimeIndex([pd.Timestamp(y, 9, 30) for y in (2021, 2022, 2023, 2024)])
    a_vals = [1.0 * (1.0 + growth) ** i for i in range(4)]
    return {
        "eps_quarterly": pd.Series(q_vals, index=q_idx),
        "eps_annual": pd.Series(a_vals, index=a_idx),
        "roe": pd.Series([0.30], index=pd.DatetimeIndex([a_idx[-1]])),
        "source": "yfinance",
    }


class TestCanSlimCheck:
    def test_all_pass_yields_yes(self):
        res = canslim_check(
            _uptrend_df(),
            symbol="600519.SH",
            benchmark_close=_bench_series(),
            fundamentals=_fundamentals(),
        )
        letters = _by_letter(res)
        assert letters["C"]["status"] == "pass"
        assert letters["A"]["status"] == "pass"
        assert letters["N"]["status"] == "pass"
        assert letters["S"]["status"] == "pass"  # 全为上涨日，量能无派发
        assert letters["L"]["status"] == "pass"  # 跑赢弱基准
        assert letters["I"]["status"] == "unavailable"  # 机构数据诚实标注
        assert letters["M"]["status"] == "pass"
        assert res.verdict == "yes"
        assert res.failed == 0

    def test_market_downtrend_vetoes_to_no(self):
        bench = pd.Series(
            100.0 * (1.0 - 0.002) ** np.arange(320),
            index=pd.date_range("2024-01-02", periods=320, freq="B"),
        )
        res = canslim_check(
            _uptrend_df(), benchmark_close=bench, fundamentals=_fundamentals()
        )
        assert _by_letter(res)["M"]["status"] == "fail"
        assert res.verdict == "no"  # M 是否决项：大势不对不买

    def test_missing_fundamentals_caps_watch(self):
        res = canslim_check(
            _uptrend_df(), benchmark_close=_bench_series(), fundamentals=None
        )
        letters = _by_letter(res)
        assert letters["C"]["status"] == "unavailable"
        assert letters["A"]["status"] == "unavailable"
        assert res.failed == 0
        assert res.verdict == "watch"  # 基本面盲区不给「是」

    def test_insufficient_bars_unrated(self):
        res = canslim_check(_uptrend_df(n=100))
        assert res.verdict == "unrated"
        assert res.n_bars < MIN_BARS

    def test_two_failures_yield_no(self):
        # 前 260 根上行、后 60 根下跌且下跌段放量：N（远离高点）与 S（派发）双失败
        up = 10.0 * (1.0 + 0.003) ** np.arange(260)
        down = up[-1] * (1.0 - 0.005) ** np.arange(1, 61)
        close = np.concatenate([up, down])
        volume = np.concatenate([np.full(260, 1e6), np.full(60, 3e6)])
        df = make_ohlcv(close, start="2024-01-02", volume=volume)
        res = canslim_check(df, benchmark_close=_bench_series(), fundamentals=_fundamentals())
        letters = _by_letter(res)
        assert letters["N"]["status"] == "fail"
        assert letters["S"]["status"] == "fail"
        assert res.verdict == "no"

    def test_c_turnaround_counts_as_pass(self):
        fund = _fundamentals()
        eps = fund["eps"].copy()
        # 2023Q4 单季转负（YTD 回落），2024Q4 单季转正 → 扭亏为盈通过
        eps[pd.Timestamp(2023, 12, 31)] = eps[pd.Timestamp(2023, 9, 30)] - 0.1
        fund["eps"] = eps
        res = canslim_check(_uptrend_df(), benchmark_close=_bench_series(), fundamentals=fund)
        c = _by_letter(res)["C"]
        assert c["status"] == "pass"
        assert "扭亏" in c["reasons"][0]

    def test_a_low_growth_fails(self):
        # 当季加速（C 过）但年度复合增速不足（A 败）：仅 1 项失败 →「观察」
        fund = _fundamentals(growth=0.05)
        eps = fund["eps"].copy()
        eps[pd.Timestamp(2024, 12, 31)] += 0.5  # 末季单季利润跳增
        fund["eps"] = eps
        res = canslim_check(
            _uptrend_df(), benchmark_close=_bench_series(), fundamentals=fund
        )
        letters = _by_letter(res)
        assert letters["C"]["status"] == "pass"
        assert letters["A"]["status"] == "fail"
        assert res.verdict == "watch"  # 仅 1 项失败

    def test_rs_percentile_mode(self):
        strong = canslim_check(_uptrend_df(), fundamentals=_fundamentals(), rs_percentile=0.9)
        weak = canslim_check(_uptrend_df(), fundamentals=_fundamentals(), rs_percentile=0.5)
        assert _by_letter(strong)["L"]["status"] == "pass"
        assert _by_letter(weak)["L"]["status"] == "fail"

    def test_no_lookahead_on_future_reports(self):
        # 报告期晚于最后一根 K 线的财报不得参与检查
        fund = _fundamentals(years=(2026, 2027, 2028, 2029, 2030))
        res = canslim_check(_uptrend_df(), benchmark_close=_bench_series(), fundamentals=fund)
        letters = _by_letter(res)
        assert letters["C"]["status"] == "unavailable"
        assert letters["A"]["status"] == "unavailable"

    def test_rs_weighted_return_direction(self):
        up = pd.Series(10.0 * (1.0 + 0.003) ** np.arange(300))
        down = pd.Series(10.0 * (1.0 - 0.003) ** np.arange(300))
        assert rs_weighted_return(up) > 0 > rs_weighted_return(down)
        assert rs_weighted_return(up.head(100)) is None  # 样本不足


class TestPreSplitFundamentals:
    """预拆分 eps_quarterly/eps_annual（yfinance 港美股口径）被 C/A 正确消费。"""

    def test_presplit_c_and_a_pass(self):
        res = canslim_check(
            _uptrend_df(),
            symbol="AAPL.US",
            benchmark_close=_bench_series(),
            fundamentals=_presplit_fundamentals(),
        )
        letters = _by_letter(res)
        assert letters["C"]["status"] == "pass"  # 同财季同比 +35%
        assert letters["A"]["status"] == "pass"  # 财年 CAGR +35%（非 12 月财年末也能识别）
        assert res.verdict == "yes"

    def test_presplit_low_growth_fails_a(self):
        res = canslim_check(
            _uptrend_df(),
            benchmark_close=_bench_series(),
            fundamentals=_presplit_fundamentals(growth=0.05),
        )
        letters = _by_letter(res)
        assert letters["A"]["status"] == "fail"

    def test_yf_extract_eps_parses_stmt(self):
        """_extract_eps：从 yfinance 利润表形状（行=科目，列=报告期）提取升序 EPS。"""
        from canslim.fundamentals import _extract_eps

        stmt = pd.DataFrame(
            {
                pd.Timestamp("2024-09-30"): {"Diluted EPS": 6.1, "Total Revenue": 1e9},
                pd.Timestamp("2023-09-30"): {"Diluted EPS": 5.0, "Total Revenue": 9e8},
            }
        )
        eps = _extract_eps(stmt)
        assert list(eps.index) == [pd.Timestamp("2023-09-30"), pd.Timestamp("2024-09-30")]
        assert eps.iloc[-1] == pytest.approx(6.1)

    def test_yf_extract_eps_missing_rows_returns_none(self):
        from canslim.fundamentals import _extract_eps

        stmt = pd.DataFrame({pd.Timestamp("2024-09-30"): {"Total Revenue": 1e9}})
        assert _extract_eps(stmt) is None
        assert _extract_eps(None) is None


class TestFundamentalsCsv:
    def test_load_csv_with_percent_roe(self, tmp_path):
        path = tmp_path / "fund.csv"
        path.write_text(
            "period_end,eps,roe\n2023-12-31,1.0,18.5\n2024-12-31,1.4,20.1\n",
            encoding="utf-8",
        )
        fund = load_fundamentals_csv(str(path))
        assert len(fund["eps"]) == 2
        assert fund["roe"].iloc[-1] == pytest.approx(0.201)  # 百分数自动转小数

    def test_load_csv_missing_columns_raises(self, tmp_path):
        path = tmp_path / "bad.csv"
        path.write_text("date,value\n2024-12-31,1.0\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="period_end"):
            load_fundamentals_csv(str(path))
