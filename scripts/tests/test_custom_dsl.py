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


# ---------------------------------------------------------------------------
# 金字塔分批加仓
# ---------------------------------------------------------------------------


def _pyramid_rules(units: int = 4, step: float = 0.03) -> dict:
    """构造带 [pyramid] 的最小规则。"""
    return {
        "meta": {"name": "pyramid_test"},
        "indicators": {"ma": {"type": "sma", "period": 5}},
        "entry": {"conditions": ["close crosses_above ma"]},
        "exit": {"conditions": ["close crosses_below ma"]},
        "pyramid": {"units": units, "step": step},
    }


class TestPyramid:
    def test_validation_bad_units(self):
        from strategies.custom import _validate_rules

        with pytest.raises(DSLValidationError, match="units"):
            _validate_rules(_pyramid_rules(units=1))
        with pytest.raises(DSLValidationError, match="units"):
            _validate_rules(_pyramid_rules(units=11))

    def test_validation_bad_step(self):
        from strategies.custom import _validate_rules

        with pytest.raises(DSLValidationError, match="step"):
            _validate_rules(_pyramid_rules(step=-0.01))

    def test_fractional_signals(self):
        """入场后逐批加仓：0.25 → 0.5 → 0.75 → 1.0，离场一次性清仓。"""
        # 构造确定性行情：盘整后突破上行，每根 +4%（超过 step=3%），末段崩盘
        flat = [10.0] * 10
        up = [10.0 * 1.04**k for k in range(1, 7)]
        crash = [8.0, 7.5, 7.0]
        close = np.array(flat + up + crash)
        df = pd.DataFrame(
            {
                "close": close,
                "high": close,
                "low": close,
                "open": close,
                "volume": np.full(len(close), 1000.0),
            }
        )
        strategy = CustomStrategy(_pyramid_rules(units=4, step=0.03))
        signals = strategy.generate_signals(df)

        held = signals[signals > 0]
        # 首批为试探仓 1/4，逐批加至满仓
        assert held.iloc[0] == pytest.approx(0.25)
        assert held.max() == pytest.approx(1.0)
        # 仓位单调递增（持仓期内只加不减），且每次只加一批
        diffs = held.diff().dropna()
        assert (diffs >= 0).all()
        assert diffs.max() == pytest.approx(0.25)
        # 崩盘后离场：尾部归零
        assert signals.iloc[-1] == 0.0

    def test_rules_summary_contains_pyramid(self):
        strategy = CustomStrategy(_pyramid_rules())
        summary = strategy.rules_summary()
        assert summary["pyramid"] == {"units": 4, "step": 0.03}

    def test_no_pyramid_signals_stay_integer(self, sample_df, sample_rules):
        """未定义 [pyramid] 时信号仍为 {-1, 0, 1}（向后兼容）。"""
        strategy = CustomStrategy(sample_rules)
        signals = strategy.generate_signals(sample_df)
        assert set(signals.unique()).issubset({-1, 0, 1})

    def test_backtest_integration_with_stop_loss(self, sample_df):
        """金字塔信号 + 止损与引擎集成：持仓应出现分数仓位。"""
        from backtest.engine import run_backtest

        strategy = CustomStrategy(_pyramid_rules())
        result = run_backtest(
            sample_df, strategy, symbol="TEST.SH", stop_loss=0.05
        )
        nonzero = result.positions[result.positions != 0.0]
        assert (nonzero <= 1.0 + 1e-9).all()
        assert (nonzero.round(2).isin([0.25, 0.5, 0.75, 1.0])).all()


# ---------------------------------------------------------------------------
# 当前状态判定（run_custom.describe_current_state）
# ---------------------------------------------------------------------------


def _state_rules(pyramid: bool = False) -> dict:
    """构造最简均线穿越规则（用于状态判定测试）。"""
    rules = {
        "meta": {"name": "state_test"},
        "indicators": {"ma": {"type": "sma", "period": 5}},
        "entry": {"conditions": ["close crosses_above ma"]},
        "exit": {"conditions": ["close crosses_below ma"]},
    }
    if pyramid:
        rules["pyramid"] = {"units": 4, "step": 0.03}
    return rules


class TestCurrentState:
    def test_holding_when_entry_active(self):
        """突破后一路持有至今 → 「持仓」。"""
        from run_custom import describe_current_state
        from tests.helpers import make_ohlcv

        close = np.concatenate([np.full(60, 100.0), np.linspace(100.0, 120.0, 20)])
        state = describe_current_state(CustomStrategy(_state_rules()), make_ohlcv(close))
        assert state["state"] == "持仓"
        assert state["signal"] == 1.0
        assert {"signal", "state", "note"} <= set(state)

    def test_pyramid_building_partial_position(self):
        """入场后浮盈不足以触发加仓 → 「建仓中」（分数仓位）。"""
        from run_custom import describe_current_state
        from tests.helpers import make_ohlcv

        close = np.concatenate([np.full(30, 100.0), np.linspace(100.0, 101.0, 5)])
        state = describe_current_state(
            CustomStrategy(_state_rules(pyramid=True)), make_ohlcv(close)
        )
        assert state["state"] == "建仓中"
        assert 0.0 < state["signal"] < 1.0

    def test_just_exited_after_trend_break(self):
        """刚跌破均线离场 → 「刚离场观察」。"""
        from run_custom import describe_current_state
        from tests.helpers import make_ohlcv

        close = np.concatenate([
            np.full(40, 100.0),
            np.linspace(100.0, 110.0, 10),  # 突破并持有
            np.linspace(108.0, 100.0, 4),   # 跌破离场（发生在最后几根）
        ])
        state = describe_current_state(CustomStrategy(_state_rules()), make_ohlcv(close))
        assert state["state"] == "刚离场观察"
        assert state["signal"] == 0.0

    def test_waiting_when_never_triggered(self):
        """从未触发入场 → 「空仓等待」。"""
        from run_custom import describe_current_state
        from tests.helpers import make_ohlcv

        state = describe_current_state(
            CustomStrategy(_state_rules()), make_ohlcv(np.full(60, 100.0))
        )
        assert state["state"] == "空仓等待"

    def test_waiting_when_exited_long_ago(self):
        """离场已久且无新信号 → 归为常规「空仓等待」。"""
        from run_custom import _RECENT_EXIT_BARS, describe_current_state
        from tests.helpers import make_ohlcv

        close = np.concatenate([
            np.full(40, 100.0),
            np.linspace(100.0, 110.0, 10),
            np.linspace(108.0, 100.0, 4),
            np.full(_RECENT_EXIT_BARS + 10, 100.0),  # 离场后长期横盘
        ])
        state = describe_current_state(CustomStrategy(_state_rules()), make_ohlcv(close))
        assert state["state"] == "空仓等待"


def test_wisdom_rule_file_is_valid_and_runnable():
    """《炒股的智慧》规则文件必须可加载、可校验、能出信号（防改坏回归）。"""
    from pathlib import Path

    from tests.helpers import make_ohlcv

    path = Path(__file__).resolve().parent.parent / "examples" / "wisdom_rule.toml"
    rules = load_rules(path)
    assert rules["meta"]["name"] == "wisdom"
    entry_conds = rules["entry"]["conditions"]
    assert "close > ma200" in entry_conds, "大势过滤（200 日线）不能丢"
    assert "ma20 > ma60" in entry_conds, "多头排列条件不能丢"

    strategy = CustomStrategy(rules)
    n = 400  # 需超过 ma200 预热期
    close = np.concatenate([np.full(250, 100.0), np.linspace(100.0, 130.0, 150)])
    volume = np.linspace(1000.0, 3000.0, n)  # 升势中递增量能，满足放量条件
    signals = strategy.generate_signals(make_ohlcv(close, volume=volume))
    assert len(signals) == n
    assert signals.iloc[-1] >= 0.0
