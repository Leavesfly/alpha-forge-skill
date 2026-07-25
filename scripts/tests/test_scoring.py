"""买点三灯引擎（scoring/）回归测试。

覆盖：合成趋势的三灯颜色与矩阵结论、价灯（硬伤/估值分位/灰灯降级）、
交易计划算术、回放无前视、事件风险降级与持仓联动、动态阈值。
全部用确定性合成数据，不依赖网络。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scoring import (
    MIN_BARS,
    TREND_GREEN,
    VERDICTS,
    VOL_K_MAX,
    VOL_K_MIN,
    build_trade_plan,
    default_benchmark,
    replay_study,
    replay_verdicts,
    score_symbol,
)
from scoring.engine import _value_light, _vol_regime
from scoring.replay import calibrate_threshold
from tests.helpers import make_ohlcv


def _uptrend_df(n: int = 400, daily: float = 0.0015) -> pd.DataFrame:
    """温和健康的上行趋势（确定性）：时灯不过热，可得「趋势买点」。

    参数经过校准：直线拉升会触发 RSI 过热（那是时灯的正确行为），
    故用带适度噪声的温和趋势。
    """
    rng = np.random.default_rng(11)
    steps = daily + rng.normal(0.0, 0.006, size=n)
    close = 100.0 * np.exp(np.cumsum(steps))
    return make_ohlcv(close)


def _downtrend_df(n: int = 400, daily: float = -0.004) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    steps = daily + rng.normal(0.0, 0.004, size=n)
    close = 100.0 * np.exp(np.cumsum(steps))
    return make_ohlcv(close)


def _flat_benchmark(n: int = 400) -> pd.Series:
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.Series(100.0 + 0.01 * np.arange(n), index=dates)


#: 注入用估值分位（价灯绿/红）
LOW_VAL = {"pe_percentile": 0.15, "pb_percentile": 0.20, "source": "test"}
HIGH_VAL = {"pe_percentile": 0.90, "pb_percentile": 0.85, "source": "test"}


class TestVerdictMatrix:
    def test_uptrend_trend_entry(self):
        """上行趋势 + 平坦基准 → 势绿+时绿 →「趋势买点」（价灰不阻断）。"""
        res = score_symbol(_uptrend_df(), symbol="TEST.SH", benchmark_close=_flat_benchmark())
        assert res.verdict == "trend_entry"
        assert res.lights["trend"]["color"] == "green"
        assert res.lights["timing"]["color"] == "green"
        assert res.lights["value"]["color"] == "gray"
        assert res.trend_score is not None and res.trend_score >= TREND_GREEN
        assert res.plan is not None

    def test_cheap_uptrend_trend_entry(self):
        """低估值 + 势绿时绿 → 价绿，仍是「趋势买点」。"""
        res = score_symbol(
            _uptrend_df(), symbol="TEST.SH",
            benchmark_close=_flat_benchmark(), valuation=LOW_VAL,
        )
        assert res.lights["value"]["color"] == "green"
        assert res.verdict == "trend_entry"

    def test_expensive_uptrend_trend_only(self):
        """高估值 + 势绿时绿 → 价红（非硬伤）→「纯趋势仓」，带估值警示。"""
        res = score_symbol(
            _uptrend_df(), symbol="TEST.SH",
            benchmark_close=_flat_benchmark(), valuation=HIGH_VAL,
        )
        assert res.lights["value"]["color"] == "red"
        assert res.lights["value"]["detail"]["hard_flaw"] is False
        assert res.verdict == "trend_only"
        assert res.plan is not None
        assert "估值" in res.decision["rule"]

    def test_downtrend_avoid(self):
        """下行趋势 → 势红 →「回避」，无交易计划，给趋势修复触发条件。"""
        res = score_symbol(_downtrend_df(), symbol="TEST.SH")
        assert res.lights["trend"]["color"] == "red"
        assert res.verdict == "avoid"
        assert res.plan is None
        assert res.decision["triggers"]

    def test_cheap_downtrend_left_watch(self):
        """低估值 + 势红 →「左侧观察」：进观察名单不抄底，给触发条件。"""
        res = score_symbol(_downtrend_df(), symbol="TEST.SH", valuation=LOW_VAL)
        assert res.lights["value"]["color"] == "green"
        assert res.verdict == "left_watch"
        assert res.decision["triggers"]

    def test_overheated_wait_pullback(self):
        """势绿但末端暴力拉升偏离 MA20 → 时红 →「等回踩」，给回踩触发条件。"""
        n = 400
        rng = np.random.default_rng(11)
        calm = 0.0012 + rng.normal(0.0, 0.004, size=n - 12)
        spike = np.full(12, 0.035)  # 12 天连拉 +3.5%，远超 MA20 偏离阈值
        close = 100.0 * np.exp(np.cumsum(np.concatenate([calm, spike])))
        res = score_symbol(make_ohlcv(close), symbol="TEST.SH")
        assert res.lights["trend"]["color"] == "green"
        assert res.lights["timing"]["color"] == "red"
        assert res.verdict == "wait_pullback"
        assert any("回踩" in t or "RSI" in t for t in res.decision["triggers"])

    def test_hard_flaw_vetoes_strong_trend(self):
        """价硬伤（ST）一票否决：即使势绿时绿价便宜也「回避」。"""
        res = score_symbol(
            _uptrend_df(), symbol="TEST.SH",
            benchmark_close=_flat_benchmark(),
            fundamentals={"is_st": True, "source": "test"},
            valuation=LOW_VAL,
        )
        assert res.lights["value"]["color"] == "red"
        assert res.lights["value"]["detail"]["hard_flaw"] is True
        assert res.verdict == "avoid"

    def test_insufficient_data_unrated(self):
        """有效 K 线不足 MIN_BARS →「无法评分」，三灯全灰不猜测。"""
        res = score_symbol(_uptrend_df(n=100), symbol="TEST.SH")
        assert res.verdict == "unrated"
        assert res.trend_score is None
        assert all(res.lights[name]["color"] == "gray" for name in ("value", "trend", "timing"))

    def test_trend_red_one_way(self):
        """势灯单向性：近端动量再强，收盘低于 MA200 势灯必红。

        构造先暴跌后 V 型反弹但仍低于 MA200 的价格。
        """
        n = 400
        close = np.concatenate(
            [
                np.full(200, 100.0),  # 高位平台，抬高 MA200
                100.0 * np.exp(np.cumsum(np.full(120, -0.02))),  # 暴跌
                100.0 * np.exp(-0.02 * 120) * np.exp(np.cumsum(np.full(80, 0.008))),  # 反弹但远低于 MA200
            ]
        )
        res = score_symbol(make_ohlcv(close[:n]), symbol="TEST.SH")
        assert res.lights["trend"]["color"] == "red"
        assert res.lights["trend"]["detail"]["below_ma200"] is True
        assert res.verdict == "avoid"

    def test_lights_reasons_recorded(self):
        """三灯理由齐全非空（可解释性），决策矩阵有裁决规则。"""
        res = score_symbol(_uptrend_df(), symbol="TEST.SH", benchmark_close=_flat_benchmark())
        for name in ("value", "trend", "timing"):
            assert res.lights[name]["reasons"]
        assert res.decision["rule"]
        assert res.lights_summary.count("·") == 2

    def test_no_benchmark_degraded(self):
        """无基准：相对强度权重并入动量并在势灯理由中标注降级。"""
        res = score_symbol(_uptrend_df(), symbol="cu2501.SHF")
        assert res.components["weights"]["rel_strength"] == 0.0
        assert any("无可用基准" in r for r in res.lights["trend"]["reasons"])

    def test_default_benchmark_mapping(self):
        assert default_benchmark("600000.SH") == "510300.SH"
        assert default_benchmark("00700.HK") == "02800.HK"
        assert default_benchmark("AAPL.US") == "SPY.US"
        assert default_benchmark("cu2501.SHF") is None


class TestValueLight:
    """价灯单元测试：硬伤/由盈转亏/估值分位/灰灯。"""

    def test_st_hard_flaw(self):
        light = _value_light({"is_st": True, "source": "test"}, None)
        assert light["color"] == "red"
        assert light["detail"]["hard_flaw"] is True
        assert any("ST" in r for r in light["reasons"])

    def test_negative_net_asset_hard_flaw(self):
        light = _value_light({"is_st": False, "net_asset_per_share": -0.5, "source": "test"}, None)
        assert light["color"] == "red"
        assert light["detail"]["hard_flaw"] is True
        assert any("资不抵债" in r for r in light["reasons"])

    def test_consecutive_losses_hard_flaw(self):
        light = _value_light(
            {"is_st": False, "eps_recent": [-0.1, -0.2, -0.15, -0.3], "source": "test"}, None
        )
        assert light["color"] == "red"
        assert light["detail"]["hard_flaw"] is True
        assert any("连续亏损" in r for r in light["reasons"])

    def test_profit_to_loss_caps_yellow(self):
        """由盈转亏非硬伤，但价灯封顶黄（即使估值便宜）。"""
        fund = {"is_st": False, "eps_recent": [0.5, 0.3, -0.1, -0.2], "source": "test"}
        light = _value_light(fund, LOW_VAL)
        assert light["color"] == "yellow"
        assert light["detail"]["hard_flaw"] is False
        assert any("由盈转亏" in r for r in light["reasons"])

    def test_healthy_cheap_green(self):
        fund = {"is_st": False, "eps_recent": [0.5, 0.6, 0.7, 0.8], "source": "test"}
        light = _value_light(fund, LOW_VAL)
        assert light["color"] == "green"

    def test_healthy_expensive_red_not_hard(self):
        fund = {"is_st": False, "eps_recent": [0.5, 0.6, 0.7, 0.8], "source": "test"}
        light = _value_light(fund, HIGH_VAL)
        assert light["color"] == "red"
        assert light["detail"]["hard_flaw"] is False

    def test_no_data_gray(self):
        light = _value_light(None, None)
        assert light["color"] == "gray"
        assert any("无估值分位数据" in r for r in light["reasons"])


class TestTradePlan:
    def test_plan_arithmetic(self):
        """止损 < 入场 < 2R < 3R，R 与 2×ATR 一致。"""
        plan = build_trade_plan(close=100.0, ma20=97.0, atr14=2.0)
        assert plan["stop"] == pytest.approx(96.0)
        assert plan["r"] == pytest.approx(4.0)
        assert plan["target_2r"] == pytest.approx(108.0)
        assert plan["target_3r"] == pytest.approx(112.0)
        assert plan["chase_limit"] == pytest.approx(101.0)
        assert plan["stop"] < plan["entry"] < plan["target_2r"] < plan["target_3r"]

    def test_plan_invalid_atr(self):
        assert build_trade_plan(100.0, 97.0, float("nan")) is None
        assert build_trade_plan(100.0, 97.0, 0.0) is None


class TestReplay:
    def test_replay_no_lookahead(self):
        """回放无前视：截尾数据独立重算的结论与回放序列一致。"""
        df = _uptrend_df(n=MIN_BARS + 30)
        verdicts = replay_verdicts(df, days=20, symbol="TEST.SH")
        # 任取一个回放日，用同样前缀独立评估应得到相同结论
        check_i = len(df) - 10
        idx = pd.DatetimeIndex(pd.to_datetime(df["trade_date"]))
        independent = score_symbol(df.iloc[: check_i + 1], symbol="TEST.SH")
        assert verdicts.loc[idx[check_i]] == independent.verdict

    def test_replay_study_structure(self):
        df = _uptrend_df(n=MIN_BARS + 120)
        verdicts = replay_verdicts(df, days=100, symbol="TEST.SH")
        study = replay_study(df, verdicts)
        assert study["days"] == 100
        assert set(study["horizons"]) == {"21", "63"}
        assert "inconclusive" in study
        # 上行趋势里应出现过买点信号；样本少必须诚实标注 inconclusive
        if study["n_entry_signals"] and study["horizons"]["21"]["n_nonoverlap"] < 10:
            assert study["inconclusive"] is True

    def test_replay_insufficient_history(self):
        with pytest.raises(ValueError):
            replay_verdicts(_uptrend_df(n=100), days=50)


class TestCalibrate:
    def test_calibrate_returns_grid(self):
        """上行趋势数据：校准应返回网格与最优阈值。"""
        df = _uptrend_df(n=MIN_BARS + 150)
        result = calibrate_threshold(df, days=120, horizon=10, symbol="TEST.SH", min_samples=5)
        assert result["total_days"] > 0
        if result["best_threshold"] is not None:
            assert result["best_hit_rate"] is not None
            assert result["best_n"] >= 5
            assert len(result["grid"]) > 0
            # 上行趋势中胜率应较高
            assert result["best_hit_rate"] > 0.5

    def test_calibrate_insufficient_history(self):
        """历史不足时报错。"""
        with pytest.raises(ValueError):
            calibrate_threshold(_uptrend_df(n=100), days=50)

    def test_calibrate_too_few_samples(self):
        """样本不足时返回 note 而非崩溃。"""
        df = _uptrend_df(n=MIN_BARS + 5)
        result = calibrate_threshold(df, days=5, horizon=3, min_samples=100)
        assert result["best_threshold"] is None
        assert "note" in result


class TestOverlays:
    def test_high_risk_event_blocks_entry(self):
        """近 30 天 high 风险事件：时灯红 → 趋势买点降「等回踩」；利好不加分。"""
        df = _uptrend_df()
        last_date = str(pd.to_datetime(df["trade_date"]).iloc[-1])[:10]
        events = [{"date": last_date, "risk": "high", "note": "重大诉讼"}]
        res = score_symbol(df, symbol="TEST.SH", benchmark_close=_flat_benchmark(), risk_events=events)
        assert res.lights["timing"]["color"] == "red"
        assert res.verdict == "wait_pullback"
        assert res.risk_events and res.risk_events[0]["risk"] == "high"

    def test_old_risk_event_ignored(self):
        """30 天前的事件不触发降级。"""
        df = _uptrend_df()
        old = str(pd.to_datetime(df["trade_date"]).iloc[0])[:10]
        events = [{"date": old, "risk": "high", "note": "旧事件"}]
        res = score_symbol(df, symbol="TEST.SH", benchmark_close=_flat_benchmark(), risk_events=events)
        assert res.verdict == "trend_entry"

    def test_position_reduce_risk(self):
        """持仓 + 势红 →「持仓需减风险」，并给出建议。"""
        res = score_symbol(_downtrend_df(), symbol="TEST.SH", position={"cost": 100.0, "shares": 100})
        assert res.verdict == "reduce_risk"
        assert res.position is not None
        assert "减仓" in res.position["advice"]

    def test_position_hold_when_trend_entry(self):
        """持仓 + 结论「趋势买点」→ 结论不变，建议持有。"""
        res = score_symbol(
            _uptrend_df(),
            symbol="TEST.SH",
            benchmark_close=_flat_benchmark(),
            position={"cost": 100.0},
        )
        assert res.verdict == "trend_entry"
        assert "持有" in res.position["advice"]

    def test_position_does_not_change_trend_score(self):
        """持仓只改操作建议，不改趋势分与灯色。"""
        base = score_symbol(_downtrend_df(), symbol="TEST.SH")
        held = score_symbol(_downtrend_df(), symbol="TEST.SH", position={"cost": 100.0})
        assert base.trend_score == held.trend_score
        assert base.lights["trend"]["color"] == held.lights["trend"]["color"]


class TestSerialization:
    def test_to_dict_json_friendly(self):
        import json

        from report import to_json

        res = score_symbol(_uptrend_df(), symbol="TEST.SH", benchmark_close=_flat_benchmark())
        text = to_json(res.to_dict())
        payload = json.loads(text)
        assert payload["verdict"] in VERDICTS
        assert payload["verdict_cn"]
        assert set(payload["lights"]) == {"value", "trend", "timing"}
        assert payload["lights_summary"]
        assert payload["decision"]["rule"]

    def test_evidence_chain(self):
        """证据链：编号连续、字段齐全、含趋势分与 MA200 证据。"""
        res = score_symbol(_uptrend_df(), symbol="TEST.SH", benchmark_close=_flat_benchmark())
        assert res.evidence
        for i, ev in enumerate(res.evidence, 1):
            assert ev["id"] == f"E{i:02d}"
            assert ev["light"] in ("value", "trend", "timing")
            assert ev["claim"]
            assert "triggered" in ev and "impact" in ev
        indicators = {ev["indicator"] for ev in res.evidence}
        assert {"trend_score", "close_vs_ma200"} <= indicators


class TestDynamicThresholds:
    """动态自适应阈值：波动率缩放因子 vol_k 测试。"""

    def test_high_volatility_widens_threshold(self):
        """高波动环境：vol_k > 1.0，阈值放宽。"""
        # 前 300 根低波动 + 后 100 根高波动 → 当前波动率远高于中位数
        rng = np.random.default_rng(42)
        calm = rng.normal(0.001, 0.003, size=300)
        wild = rng.normal(0.001, 0.025, size=100)
        close = 100.0 * np.exp(np.cumsum(np.concatenate([calm, wild])))
        vol_k = _vol_regime(pd.Series(close))
        assert vol_k > 1.0
        assert vol_k <= VOL_K_MAX

    def test_low_volatility_tightens_threshold(self):
        """低波动环境：vol_k < 1.0，阈值收紧。"""
        # 前 300 根高波动 + 后 100 根极低波动
        rng = np.random.default_rng(42)
        wild = rng.normal(0.001, 0.025, size=300)
        calm = rng.normal(0.001, 0.002, size=100)
        close = 100.0 * np.exp(np.cumsum(np.concatenate([wild, calm])))
        vol_k = _vol_regime(pd.Series(close))
        assert vol_k < 1.0
        assert vol_k >= VOL_K_MIN

    def test_insufficient_data_returns_one(self):
        """数据不足时 vol_k = 1.0（退化为固定阈值）。"""
        close = pd.Series(np.linspace(100, 110, 30))
        assert _vol_regime(close) == 1.0

    def test_vol_k_in_snapshot(self):
        """评估结果 snapshot 中包含 vol_k 字段。"""
        res = score_symbol(_uptrend_df(), symbol="TEST.SH")
        assert "vol_k" in res.snapshot
        assert VOL_K_MIN <= res.snapshot["vol_k"] <= VOL_K_MAX
