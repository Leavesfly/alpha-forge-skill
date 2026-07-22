"""自定义策略 DSL 引擎测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategies.custom import (
    CustomStrategy,
    DSLValidationError,
    compute_indicators,
    evaluate_condition,
    evaluate_conditions,
    load_rules,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_df():
    """500 根模拟 K 线。"""
    np.random.seed(42)
    n = 500
    dates = pd.date_range("2022-01-01", periods=n, freq="B")
    close = 10 + np.cumsum(np.random.randn(n) * 0.15)
    return pd.DataFrame(
        {
            "close": close,
            "high": close + abs(np.random.randn(n) * 0.1),
            "low": close - abs(np.random.randn(n) * 0.1),
            "open": close + np.random.randn(n) * 0.05,
            "volume": np.random.randint(1000, 5000, n).astype(float),
        },
        index=dates,
    )


@pytest.fixture
def sample_rules():
    """金叉 + RSI 过滤规则。"""
    return {
        "meta": {"name": "test_rule", "description": "测试规则"},
        "indicators": {
            "fast_ma": {"type": "sma", "period": 10, "source": "close"},
            "slow_ma": {"type": "sma", "period": 30, "source": "close"},
            "rsi14": {"type": "rsi", "period": 14},
        },
        "entry": {
            "logic": "and",
            "conditions": ["fast_ma crosses_above slow_ma", "rsi14 < 70"],
        },
        "exit": {
            "logic": "or",
            "conditions": ["fast_ma crosses_below slow_ma", "rsi14 > 80"],
        },
    }


# ---------------------------------------------------------------------------
# 规则加载与校验
# ---------------------------------------------------------------------------


class TestLoadRules:
    def test_load_valid_file(self, tmp_path):
        toml_content = """
[meta]
name = "test"
description = "test rule"

[indicators.ma]
type = "sma"
period = 20

[entry]
conditions = ["close > ma"]

[exit]
conditions = ["close < ma"]
"""
        f = tmp_path / "rule.toml"
        f.write_text(toml_content)
        rules = load_rules(f)
        assert rules["meta"]["name"] == "test"

    def test_missing_file(self):
        with pytest.raises(DSLValidationError, match="不存在"):
            load_rules("/nonexistent/rule.toml")

    def test_missing_meta_name(self):
        with pytest.raises(DSLValidationError, match="name"):
            from strategies.custom import _validate_rules

            _validate_rules({"indicators": {"x": {"type": "sma", "period": 5}},
                             "entry": {"conditions": ["x > 1"]},
                             "exit": {"conditions": ["x < 1"]}})

    def test_unknown_indicator_type(self):
        with pytest.raises(DSLValidationError, match="不支持"):
            from strategies.custom import _validate_rules

            _validate_rules({
                "meta": {"name": "x"},
                "indicators": {"bad": {"type": "unknown_ind"}},
                "entry": {"conditions": ["bad > 1"]},
                "exit": {"conditions": ["bad < 1"]},
            })

    def test_undefined_indicator_in_condition(self):
        with pytest.raises(DSLValidationError, match="未定义"):
            from strategies.custom import _validate_rules

            _validate_rules({
                "meta": {"name": "x"},
                "indicators": {"ma": {"type": "sma", "period": 5}},
                "entry": {"conditions": ["undefined_ref > 1"]},
                "exit": {"conditions": ["ma < 1"]},
            })


# ---------------------------------------------------------------------------
# 指标计算
# ---------------------------------------------------------------------------


class TestIndicators:
    def test_sma(self, sample_df):
        indicators = compute_indicators(sample_df, {"ma20": {"type": "sma", "period": 20}})
        ma = indicators["ma20"]
        assert len(ma) == len(sample_df)
        assert ma.iloc[:19].isna().all()
        assert not ma.iloc[19:].isna().any()

    def test_rsi_range(self, sample_df):
        indicators = compute_indicators(sample_df, {"rsi": {"type": "rsi", "period": 14}})
        rsi = indicators["rsi"].dropna()
        assert (rsi >= 0).all() and (rsi <= 100).all()

    def test_macd_components(self, sample_df):
        defs = {
            "dif": {"type": "macd_line"},
            "dea": {"type": "macd_signal"},
            "hist": {"type": "macd_hist"},
        }
        indicators = compute_indicators(sample_df, defs)
        # hist = (dif - dea) * 2
        diff = (indicators["dif"] - indicators["dea"]) * 2 - indicators["hist"]
        assert diff.dropna().abs().max() < 1e-10

    def test_bollinger_bands(self, sample_df):
        defs = {
            "upper": {"type": "bollinger_upper", "period": 20},
            "mid": {"type": "bollinger_mid", "period": 20},
            "lower": {"type": "bollinger_lower", "period": 20},
        }
        indicators = compute_indicators(sample_df, defs)
        valid = indicators["upper"].dropna().index
        assert (indicators["upper"][valid] >= indicators["mid"][valid]).all()
        assert (indicators["mid"][valid] >= indicators["lower"][valid]).all()

    def test_indicator_chaining(self, sample_df):
        """指标可引用其他已计算指标作为 source。"""
        defs = {
            "ma10": {"type": "sma", "period": 10},
            "ema_of_ma": {"type": "ema", "period": 5, "source": "ma10"},
        }
        indicators = compute_indicators(sample_df, defs)
        assert not indicators["ema_of_ma"].iloc[20:].isna().any()


# ---------------------------------------------------------------------------
# 条件求值
# ---------------------------------------------------------------------------


class TestConditions:
    def test_greater_than(self, sample_df):
        indicators = compute_indicators(sample_df, {"ma": {"type": "sma", "period": 20}})
        mask = evaluate_condition("close > ma", indicators, sample_df)
        assert mask.dtype == bool
        assert mask.sum() > 0

    def test_crosses_above(self, sample_df):
        defs = {
            "fast": {"type": "sma", "period": 5},
            "slow": {"type": "sma", "period": 20},
        }
        indicators = compute_indicators(sample_df, defs)
        mask = evaluate_condition("fast crosses_above slow", indicators)
        # 金叉是稀疏事件
        assert mask.sum() < len(sample_df) * 0.2
        assert mask.sum() > 0

    def test_numeric_comparison(self, sample_df):
        indicators = compute_indicators(sample_df, {"rsi": {"type": "rsi", "period": 14}})
        mask = evaluate_condition("rsi < 30", indicators)
        assert mask.dtype == bool

    def test_and_logic(self, sample_df):
        defs = {
            "fast": {"type": "sma", "period": 5},
            "slow": {"type": "sma", "period": 20},
            "rsi": {"type": "rsi", "period": 14},
        }
        indicators = compute_indicators(sample_df, defs)
        conds = ["fast > slow", "rsi < 70"]
        mask = evaluate_conditions(conds, indicators, logic="and")
        # AND 结果不超过任一单独条件
        m1 = evaluate_condition(conds[0], indicators)
        assert mask.sum() <= m1.sum()

    def test_or_logic(self, sample_df):
        defs = {"rsi": {"type": "rsi", "period": 14}}
        indicators = compute_indicators(sample_df, defs)
        conds = ["rsi > 70", "rsi < 30"]
        mask = evaluate_conditions(conds, indicators, logic="or")
        m1 = evaluate_condition(conds[0], indicators)
        assert mask.sum() >= m1.sum()


# ---------------------------------------------------------------------------
# CustomStrategy 集成
# ---------------------------------------------------------------------------


class TestCustomStrategy:
    def test_signal_shape(self, sample_df, sample_rules):
        strategy = CustomStrategy(sample_rules)
        signals = strategy.generate_signals(sample_df)
        assert len(signals) == len(sample_df)
        assert set(signals.unique()).issubset({-1, 0, 1})

    def test_warmup_zero(self, sample_df, sample_rules):
        """预热期内信号为 0。"""
        strategy = CustomStrategy(sample_rules)
        signals = strategy.generate_signals(sample_df)
        # 最大 period=30，预热期内应为 0
        assert (signals.iloc[:30] == 0).all()

    def test_from_file(self, tmp_path, sample_df):
        toml_content = """
[meta]
name = "file_test"
description = "from file"

[indicators.ma]
type = "sma"
period = 20

[entry]
conditions = ["close crosses_above ma"]

[exit]
conditions = ["close crosses_below ma"]
"""
        f = tmp_path / "test.toml"
        f.write_text(toml_content)
        strategy = CustomStrategy.from_file(f)
        signals = strategy.generate_signals(sample_df)
        assert len(signals) == len(sample_df)

    def test_rules_summary(self, sample_rules):
        strategy = CustomStrategy(sample_rules)
        summary = strategy.rules_summary()
        assert summary["name"] == "test_rule"
        assert "fast_ma" in summary["indicators"]
        assert len(summary["entry"]["conditions"]) == 2

    def test_backtest_integration(self, sample_df, sample_rules):
        """与回测引擎集成。"""
        from backtest.engine import run_backtest

        strategy = CustomStrategy(sample_rules)
        result = run_backtest(sample_df, strategy, symbol="TEST.SH")
        assert "total_return" in result.metrics
        assert "sharpe" in result.metrics
