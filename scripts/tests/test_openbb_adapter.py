"""OpenBB 基本面适配层测试（全程 mock openbb 模块，不走网络）。"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from data.openbb import (
    fetch_obb_dividends,
    fetch_obb_eps,
    fetch_obb_metrics,
    fetch_obb_rd_ratio,
    supports_hkus,
)
from errors import DataFetchError


class _FakeOut:
    def __init__(self, df):
        self._df = df

    def to_dataframe(self):
        return self._df


def _install_fake_obb(monkeypatch, *, metrics=None, income=None, dividends=None):
    """安装假 openbb 模块：income 为 (period -> DataFrame) 映射。"""

    def _metrics(ticker, provider):
        return _FakeOut(metrics)

    def _income(ticker, period, provider, limit):
        return _FakeOut(income[period])

    def _dividends(ticker, provider):
        return _FakeOut(dividends)

    fundamental = SimpleNamespace(
        metrics=_metrics, income=_income, dividends=_dividends
    )
    fake_obb = SimpleNamespace(equity=SimpleNamespace(fundamental=fundamental))
    monkeypatch.setitem(sys.modules, "openbb", SimpleNamespace(obb=fake_obb))


def test_supports_hkus_scope():
    """仅覆盖港美股，港股代码需纯数字。"""
    assert supports_hkus("AAPL.US")
    assert supports_hkus("00700.HK")
    assert not supports_hkus("600000.SH")
    assert not supports_hkus("ABCD.HK")


def test_metrics_normalization(monkeypatch):
    """小数比率转百分数、市值转亿、NaN 转 None。"""
    metrics_df = pd.DataFrame(
        [
            {
                "pe_ratio": 25.0,
                "price_to_book": 8.0,
                "return_on_equity": 0.168,
                "dividend_yield": 0.042,
                "debt_to_equity": 85.3,
                "earnings_growth": 0.12,
                "revenue_growth": 0.15,
                "gross_margin": 0.62,
                "market_cap": 3.0e12,
                "book_value": 4.5,
            }
        ]
    )
    _install_fake_obb(monkeypatch, metrics=metrics_df)
    m = fetch_obb_metrics("AAPL.US")
    assert m["pe"] == 25.0
    assert m["roe"] == pytest.approx(16.8)
    assert m["div_yield"] == pytest.approx(4.2)
    assert m["debt_ratio"] == pytest.approx(85.3)  # 已是百分数，不重复转换
    assert m["profit_growth"] == pytest.approx(12.0)
    assert m["gross_margin"] == pytest.approx(62.0)
    assert m["total_mv"] == pytest.approx(3.0e12 / 1e8)
    assert m["bvps"] == pytest.approx(4.5)


def test_metrics_empty_raises(monkeypatch):
    _install_fake_obb(monkeypatch, metrics=pd.DataFrame())
    with pytest.raises(DataFetchError, match="关键指标"):
        fetch_obb_metrics("AAPL.US")


def _income_df(periods, eps):
    return pd.DataFrame(
        {
            "period_ending": periods,
            "diluted_earnings_per_share": eps,
            "total_revenue": np.full(len(eps), 1e9),
        }
    )


def test_eps_series(monkeypatch):
    """季度/年度 EPS 序列升序返回。"""
    income = {
        "quarter": _income_df(
            ["2026-03-31", "2025-12-31", "2025-09-30"], [2.0, 2.8, 1.9]
        ),
        "annual": _income_df(["2025-09-30", "2024-09-30"], [7.5, 6.1]),
    }
    _install_fake_obb(monkeypatch, income=income)
    eps_q, eps_a = fetch_obb_eps("AAPL.US")
    assert len(eps_q) == 3 and eps_q.index.is_monotonic_increasing
    assert float(eps_q.iloc[-1]) == pytest.approx(2.0)  # 2026-03-31 最新
    assert len(eps_a) == 2 and float(eps_a.iloc[-1]) == pytest.approx(7.5)


def test_rd_ratio(monkeypatch):
    """研发强度 = 最新财年 R&D / 营收 × 100。"""
    annual = _income_df(["2025-09-30", "2024-09-30"], [7.5, 6.1])
    annual["research_and_development_expense"] = [3.0e7, 2.5e7]
    _install_fake_obb(monkeypatch, income={"annual": annual})
    assert fetch_obb_rd_ratio("MSFT.US") == pytest.approx(3.0)  # 3e7/1e9*100


def test_rd_ratio_no_rd_column(monkeypatch):
    """无研发科目返回 None（未披露，非异常）。"""
    _install_fake_obb(
        monkeypatch, income={"annual": _income_df(["2025-09-30"], [7.5])}
    )
    assert fetch_obb_rd_ratio("KO.US") is None


def test_dividends_series(monkeypatch):
    """分红序列：除息日索引、升序、过滤非正值。"""
    div_df = pd.DataFrame(
        {
            "ex_dividend_date": ["2025-05-16", "2024-05-17", "2026-05-15"],
            "amount": [4.5, 3.4, 5.3],
        }
    )
    _install_fake_obb(monkeypatch, dividends=div_df)
    s = fetch_obb_dividends("00700.HK")
    assert len(s) == 3
    assert s.index.is_monotonic_increasing
    assert float(s.iloc[-1]) == pytest.approx(5.3)


def test_dividends_empty_raises(monkeypatch):
    _install_fake_obb(monkeypatch, dividends=pd.DataFrame())
    with pytest.raises(DataFetchError, match="分红"):
        fetch_obb_dividends("00700.HK")


# ---------------------------------------------------------------- 调用点接线


def test_fetch_dividends_routes_hkus(monkeypatch):
    """data.dividends 对港美股走 openbb 路径。"""
    import data.dividends as dividends_mod
    import data.openbb as openbb_mod

    fake = pd.Series(
        [3.4, 4.5], index=pd.DatetimeIndex(["2024-05-17", "2025-05-16"])
    )
    monkeypatch.setattr(openbb_mod, "fetch_obb_dividends", lambda s: fake)
    out = dividends_mod.fetch_dividends("00700.HK")
    assert len(out) == 2


def test_fetch_dividends_hkus_error_wrapped(monkeypatch):
    """openbb 异常归一为 RuntimeError（带 CSV 提示），与 A 股路径一致。"""
    import data.dividends as dividends_mod
    import data.openbb as openbb_mod

    def _boom(s):
        raise DataFetchError("simulated")

    monkeypatch.setattr(openbb_mod, "fetch_obb_dividends", _boom)
    with pytest.raises(RuntimeError, match="CSV"):
        dividends_mod.fetch_dividends("AAPL.US")


def test_canslim_openbb_primary(monkeypatch):
    """canslim 港美股基本面：openbb 主力成功时 source=openbb。"""
    import canslim.fundamentals as cf
    import data.openbb as openbb_mod

    eps_q = pd.Series([1.9, 2.8, 2.0], index=pd.date_range("2025-09-30", periods=3, freq="QE"))
    eps_a = pd.Series([6.1, 7.5], index=pd.date_range("2024-09-30", periods=2, freq="YE"))
    monkeypatch.setattr(openbb_mod, "fetch_obb_eps", lambda s: (eps_q, eps_a))
    monkeypatch.setattr(openbb_mod, "fetch_obb_metrics", lambda s: {"roe": 16.8})
    out = cf.fetch_fundamentals("AAPL.US")
    assert out is not None and out["source"] == "openbb"
    assert out["roe"] is not None
    assert float(out["roe"].iloc[0]) == pytest.approx(0.168)  # 百分数转回小数


def test_canslim_openbb_fallback_to_yfinance(monkeypatch):
    """openbb 失败时降级 yfinance 直连路径。"""
    import canslim.fundamentals as cf
    import data.openbb as openbb_mod

    def _boom(s):
        raise DataFetchError("simulated outage")

    monkeypatch.setattr(openbb_mod, "fetch_obb_eps", _boom)
    sentinel = {"eps_quarterly": None, "eps_annual": None, "roe": None, "source": "yfinance"}
    monkeypatch.setattr(cf, "_fetch_yfinance", lambda s: sentinel)
    out = cf.fetch_fundamentals("AAPL.US")
    assert out is sentinel
