"""纪律评分引擎（scoring/）回归测试。

覆盖：合成趋势的结论方向、否决层单向性、交易计划算术、
回放无前视、事件风险降级与持仓联动。全部用确定性合成数据，不依赖网络。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scoring import (
    MIN_BARS,
    build_trade_plan,
    default_benchmark,
    replay_study,
    replay_verdicts,
    score_symbol,
)
from scoring.replay import calibrate_threshold
from tests.helpers import make_ohlcv


def _uptrend_df(n: int = 400, daily: float = 0.0015) -> pd.DataFrame:
    """温和健康的上行趋势（确定性）：能通过确认层（不过热）得到「是」。

    参数经过校准：直线拉升会触发 RSI 过热/KDJ 拦截（那是引擎的正确行为），
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


class TestVerdicts:
    def test_uptrend_yes(self):
        """强上行趋势 + 平坦基准 → 结论「是」。"""
        res = score_symbol(_uptrend_df(), symbol="TEST.SH", benchmark_close=_flat_benchmark())
        assert res.verdict == "yes"
        assert res.alpha_score is not None and res.alpha_score >= 60
        assert res.plan is not None

    def test_downtrend_no(self):
        """下行趋势跌破 MA200 → 结论「否」，且无交易计划。"""
        res = score_symbol(_downtrend_df(), symbol="TEST.SH")
        assert res.verdict == "no"
        assert res.plan is None

    def test_insufficient_data_unrated(self):
        """有效 K 线不足 MIN_BARS → 「无法评分」，不用猜测补齐。"""
        res = score_symbol(_uptrend_df(n=100), symbol="TEST.SH")
        assert res.verdict == "unrated"
        assert res.alpha_score is None

    def test_veto_one_way(self):
        """否决层单向性：动能再强，只要收盘低于 MA200 就必须「否」。

        构造先暴跌后 V 型反弹但仍低于 MA200 的价格：近端动量强、
        alpha 分高，但长期趋势逆势。
        """
        n = 400
        close = np.concatenate(
            [
                np.full(200, 100.0),  # 高位平台，抬高 MA200
                100.0 * np.exp(np.cumsum(np.full(120, -0.02))),  # 暴跌
                100.0 * np.exp(-0.02 * 120) * np.exp(np.cumsum(np.full(80, 0.008))),  # 反弹但远低于 MA200
            ]
        )
        df = make_ohlcv(close[:n])
        res = score_symbol(df, symbol="TEST.SH")
        veto = next(l for l in res.layers if l["name"] == "veto")
        assert veto["status"] == "veto"
        assert res.verdict == "no"

    def test_layers_recorded(self):
        """四层记录齐全，理由非空（可解释性）。"""
        res = score_symbol(_uptrend_df(), symbol="TEST.SH", benchmark_close=_flat_benchmark())
        names = [l["name"] for l in res.layers]
        assert names == ["alpha", "veto", "confirm", "timing"]
        assert all(l["reasons"] for l in res.layers)

    def test_no_benchmark_degraded(self):
        """无基准：相对强度权重并入动量并在理由中标注降级。"""
        res = score_symbol(_uptrend_df(), symbol="cu2501.SHF")
        assert res.components["weights"]["rel_strength"] == 0.0
        alpha = next(l for l in res.layers if l["name"] == "alpha")
        assert any("无可用基准" in r for r in alpha["reasons"])

    def test_default_benchmark_mapping(self):
        assert default_benchmark("600000.SH") == "510300.SH"
        assert default_benchmark("00700.HK") == "02800.HK"
        assert default_benchmark("AAPL.US") == "SPY.US"
        assert default_benchmark("cu2501.SHF") is None


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
        # 任取一个回放日，用同样前缀独立评分应得到相同结论
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
        # 上行趋势里应出现过「是」；样本少必须诚实标注 inconclusive
        if study["n_yes_entries"] and study["horizons"]["21"]["n_nonoverlap"] < 10:
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
    def test_high_risk_event_downgrades_yes(self):
        """近 30 天 high 风险事件：「是」降「观察」；利好不加分。"""
        df = _uptrend_df()
        last_date = str(pd.to_datetime(df["trade_date"]).iloc[-1])[:10]
        events = [{"date": last_date, "risk": "high", "note": "重大诉讼"}]
        res = score_symbol(df, symbol="TEST.SH", benchmark_close=_flat_benchmark(), risk_events=events)
        assert res.verdict == "watch"
        layer = next(l for l in res.layers if l["name"] == "event_risk")
        assert layer["status"] == "downgrade"

    def test_old_risk_event_ignored(self):
        """30 天前的事件不触发降级。"""
        df = _uptrend_df()
        old = str(pd.to_datetime(df["trade_date"]).iloc[0])[:10]
        events = [{"date": old, "risk": "high", "note": "旧事件"}]
        res = score_symbol(df, symbol="TEST.SH", benchmark_close=_flat_benchmark(), risk_events=events)
        assert res.verdict == "yes"

    def test_position_reduce_risk(self):
        """持仓 + 结论「否」→「持仓需减风险」，并给出建议。"""
        res = score_symbol(_downtrend_df(), symbol="TEST.SH", position={"cost": 100.0, "shares": 100})
        assert res.verdict == "reduce_risk"
        assert res.position is not None
        assert "减仓" in res.position["advice"]

    def test_position_hold_when_yes(self):
        """持仓 + 结论「是」→ 结论不变，建议持有。"""
        res = score_symbol(
            _uptrend_df(),
            symbol="TEST.SH",
            benchmark_close=_flat_benchmark(),
            position={"cost": 100.0},
        )
        assert res.verdict == "yes"
        assert "持有" in res.position["advice"]

    def test_position_does_not_change_alpha(self):
        """持仓只改操作建议，不改排名分。"""
        base = score_symbol(_downtrend_df(), symbol="TEST.SH")
        held = score_symbol(_downtrend_df(), symbol="TEST.SH", position={"cost": 100.0})
        assert base.alpha_score == held.alpha_score


class TestSerialization:
    def test_to_dict_json_friendly(self):
        import json

        from report import to_json

        res = score_symbol(_uptrend_df(), symbol="TEST.SH", benchmark_close=_flat_benchmark())
        text = to_json(res.to_dict())
        payload = json.loads(text)
        assert payload["verdict"] in ("yes", "watch", "no", "reduce_risk", "unrated")
        assert payload["verdict_cn"]
        assert isinstance(payload["layers"], list)
