"""市场状态识别（research/regime.py）回归测试：合成数据驱动，无网络。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.regime import (
    REVERSION_FAMILY,
    TREND_FAMILY,
    detect_regime,
    format_regime,
)


def _series(values) -> pd.Series:
    return pd.Series(np.asarray(values, dtype=float))


def test_insufficient_data_unknown():
    info = detect_regime(_series(np.linspace(10, 11, 30)))
    assert info["regime"] == "unknown"
    assert "无法判定" in format_regime(info)


def test_trend_up_detected():
    """单调上行且波动平稳 -> 趋势上行，建议趋势跟随族。"""
    rng = np.random.default_rng(3)
    close = 100.0 * np.exp(np.cumsum(0.004 + 0.003 * rng.standard_normal(200)))
    info = detect_regime(_series(close))
    assert info["regime"] == "trend_up"
    assert info["efficiency_ratio"] >= 0.25
    assert set(info["suited_strategies"]) == set(TREND_FAMILY)


def test_trend_down_detected():
    rng = np.random.default_rng(3)
    close = 100.0 * np.exp(np.cumsum(-0.004 + 0.003 * rng.standard_normal(200)))
    info = detect_regime(_series(close))
    assert info["regime"] == "trend_down"
    assert info["above_ma"] is False


def test_range_detected():
    """正弦震荡且尾段波动不高 -> 震荡，建议均值回归族。"""
    t = np.arange(300)
    close = 100.0 + 3.0 * np.sin(t / 8.0)
    info = detect_regime(_series(close))
    assert info["regime"] == "range"
    assert set(info["suited_strategies"]) == set(REVERSION_FAMILY)


def test_volatile_overrides_trend():
    """尾段波动率飙升时无论趋势与否都归为高波动。"""
    rng = np.random.default_rng(9)
    calm = 0.001 + 0.002 * rng.standard_normal(260)
    wild = 0.001 + 0.06 * rng.standard_normal(40)
    close = 100.0 * np.exp(np.cumsum(np.concatenate([calm, wild])))
    info = detect_regime(_series(close))
    assert info["regime"] == "volatile"
    assert info["vol_percentile"] >= 0.80
    assert info["suited_strategies"] == []


def test_format_regime_contains_key_info():
    t = np.arange(300)
    close = 100.0 + 3.0 * np.sin(t / 8.0)
    line = format_regime(detect_regime(_series(close)))
    assert "市场状态" in line
    assert "震荡" in line
