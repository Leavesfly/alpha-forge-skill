"""多源交叉验证测试（全程 mock，不走网络）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data.verify import (
    ColumnDiff,
    VerifyResult,
    _align_frames,
    _compare_column,
    verify_symbol,
)
from tests.helpers import make_ohlcv


# ─── 辅助 ───────────────────────────────────────────────────────────────────────


class _MockSource:
    """可配置的 mock 数据源。"""

    def __init__(self, name: str, df: pd.DataFrame | None = None, error: str | None = None):
        self.name = name
        self._df = df
        self._error = error

    def supports(self, symbol: str, period: str) -> bool:
        return True

    def fetch(self, symbol: str, period: str, count: int, adjust: str) -> pd.DataFrame:
        if self._error:
            raise RuntimeError(self._error)
        return self._df


# ─── _align_frames ──────────────────────────────────────────────────────────────


def test_align_frames_basic():
    """共同交易日对齐：内连接只保留重叠日期。"""
    dates_a = pd.date_range("2024-01-01", periods=5, freq="B")
    dates_b = pd.date_range("2024-01-03", periods=5, freq="B")  # 偏移 2 天
    df_a = pd.DataFrame({"trade_date": dates_a, "close": range(5), "volume": range(5)})
    df_b = pd.DataFrame({"trade_date": dates_b, "close": range(5), "volume": range(5)})

    aligned_a, aligned_b = _align_frames(df_a, df_b)
    # 重叠：1/3, 1/4, 1/5 共 3 天
    assert len(aligned_a) == 3
    assert len(aligned_b) == 3


def test_align_frames_no_overlap():
    """无共同交易日时返回空帧。"""
    df_a = pd.DataFrame({"trade_date": pd.date_range("2024-01-01", periods=3), "close": [1, 2, 3]})
    df_b = pd.DataFrame({"trade_date": pd.date_range("2025-01-01", periods=3), "close": [4, 5, 6]})

    aligned_a, aligned_b = _align_frames(df_a, df_b)
    assert len(aligned_a) == 0


# ─── _compare_column ────────────────────────────────────────────────────────────


def test_compare_column_identical():
    """完全一致的数据：偏差为 0，通过。"""
    s = pd.Series([100.0, 101.0, 102.0])
    diff = _compare_column(s, s, "close", threshold_pct=0.5)
    assert diff.passed
    assert diff.max_rel_pct == 0.0
    assert diff.mismatch_count == 0


def test_compare_column_within_threshold():
    """偏差在阈值内：通过。"""
    a = pd.Series([100.0, 200.0])
    b = pd.Series([100.3, 200.5])  # 0.3%, 0.25%
    diff = _compare_column(a, b, "close", threshold_pct=0.5)
    assert diff.passed
    assert diff.mismatch_count == 0


def test_compare_column_exceeds_threshold():
    """偏差超阈值：FAIL。"""
    a = pd.Series([100.0, 200.0])
    b = pd.Series([102.0, 200.0])  # 第一行偏差 ~1.96%（分母取 max(100,102)=102）
    diff = _compare_column(a, b, "close", threshold_pct=0.5)
    assert not diff.passed
    assert diff.mismatch_count == 1
    assert diff.max_rel_pct == pytest.approx(1.96, abs=0.01)


# ─── verify_symbol（mock 两个源）────────────────────────────────────────────────


@pytest.fixture
def base_df():
    close = 100.0 * (1.0 + 0.001) ** np.arange(60)
    return make_ohlcv(close)


def test_verify_symbol_pass(monkeypatch, base_df):
    """两源数据一致时验证通过。"""
    import data.verify as vmod

    monkeypatch.setattr(vmod, "TickFlowSource", lambda: _MockSource("tickflow", base_df))
    monkeypatch.setitem(vmod.VERIFY_SOURCES, "akshare", lambda: _MockSource("akshare", base_df))

    result = verify_symbol("600000.SH", period="1d", count=60, source_b_name="akshare")
    assert result.passed
    assert result.aligned_rows == 60
    assert all(c.passed for c in result.columns)


def test_verify_symbol_fail_on_price_diff(monkeypatch, base_df):
    """价格偏差超阈值时验证失败。"""
    import data.verify as vmod

    # 对照源 close 偏移 1%
    df_b = base_df.copy()
    df_b["close"] = df_b["close"] * 1.01

    monkeypatch.setattr(vmod, "TickFlowSource", lambda: _MockSource("tickflow", base_df))
    monkeypatch.setitem(vmod.VERIFY_SOURCES, "akshare", lambda: _MockSource("akshare", df_b))

    result = verify_symbol("600000.SH", period="1d", count=60, source_b_name="akshare")
    assert not result.passed
    close_diff = next(c for c in result.columns if c.column == "close")
    assert close_diff.mismatch_count > 0


def test_verify_symbol_source_b_not_supported(monkeypatch):
    """对照源不支持的标的/周期：抛出明确错误。"""
    import data.verify as vmod

    class _NoSupport:
        name = "baostock"
        def supports(self, symbol, period):
            return False
        def fetch(self, *a):
            raise RuntimeError("not supported")

    monkeypatch.setattr(vmod, "TickFlowSource", lambda: _MockSource("tickflow", make_ohlcv(np.ones(10))))
    monkeypatch.setitem(vmod.VERIFY_SOURCES, "baostock", _NoSupport)

    with pytest.raises(RuntimeError, match="对照源 baostock 不支持"):
        verify_symbol("AAPL.US", period="1d", source_b_name="baostock")


def test_verify_symbol_both_fail(monkeypatch):
    """两源均失败时抛出汇总错误。"""
    import data.verify as vmod

    monkeypatch.setattr(vmod, "TickFlowSource", lambda: _MockSource("tickflow", error="timeout"))
    monkeypatch.setitem(vmod.VERIFY_SOURCES, "baostock", lambda: _MockSource("baostock", error="blocked"))

    with pytest.raises(RuntimeError, match="两个数据源均无法拉取"):
        verify_symbol("600000.SH", period="1d", source_b_name="baostock")


def test_verify_symbol_row_diff_warning(monkeypatch, base_df):
    """行数差异较大时产生告警。"""
    import data.verify as vmod

    close_short = 100.0 * (1.0 + 0.001) ** np.arange(30)
    df_short = make_ohlcv(close_short)

    df_a = base_df  # 60 行
    monkeypatch.setattr(vmod, "TickFlowSource", lambda: _MockSource("tickflow", df_a))
    monkeypatch.setitem(vmod.VERIFY_SOURCES, "baostock", lambda: _MockSource("baostock", df_short))

    result = verify_symbol("600000.SH", period="1d", count=60, source_b_name="baostock")
    assert any("行数差异" in w for w in result.warnings)


def test_verify_symbol_unknown_source_b(monkeypatch):
    """未知对照源名称：抛出明确错误。"""
    with pytest.raises(RuntimeError, match="未知对照源"):
        verify_symbol("600000.SH", period="1d", source_b_name="nonexist")


# ─── VerifyResult.summary ───────────────────────────────────────────────────────


def test_summary_no_aligned_rows():
    """对齐行数为 0 时摘要明确提示。"""
    r = VerifyResult(
        symbol="600000.SH", period="1d",
        source_a="tickflow", source_b="akshare",
        rows_a=100, rows_b=100, aligned_rows=0,
    )
    assert "无法比对" in r.summary()


def test_summary_pass():
    """通过时摘要含 PASS。"""
    r = VerifyResult(
        symbol="600000.SH", period="1d",
        source_a="tickflow", source_b="akshare",
        rows_a=100, rows_b=100, aligned_rows=100,
        columns=[ColumnDiff("close", 0.01, 0.005, 0, 0.5)],
    )
    assert "PASS" in r.summary()
