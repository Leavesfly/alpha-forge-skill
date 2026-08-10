"""阶段定位（stage/）回归测试。

核心用例是**用合成数据复刻「台阶式循环」**：低位平台 → 突破 → 上升推进 →
高位平台 → 跌破 → 下降趋势，逐段切片断言阶段判定正确。
全部确定性数据，不依赖网络。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stage import (
    MIN_BARS,
    POSITION_LOW,
    STAGE_CN,
    STAGES,
    crossed_above,
    detect_stage,
    find_box,
    stage_history,
)
from stage.engine import _STAGE_POSTURE, _price_position
from tests.helpers import make_ohlcv

# ---------------------------------------------------------------------------
# 合成「一个完整循环」：段长与价位经过校准，使每段唯一命中目标阶段
# ---------------------------------------------------------------------------

#: 各段长度
LEN_PRE_DECLINE = 150   # 前置下跌（让低位平台的位置分位真的处于低位）
LEN_BASE = 110          # 低位平台
LEN_BREAKOUT = 5        # 突破
LEN_ADVANCE = 60        # 上升推进
LEN_TOP = 60            # 高位平台
LEN_BREAKDOWN = 5       # 跌破
LEN_DECLINE = 60        # 下降趋势

#: 各阶段的切片终点（累积长度）
CUT = {
    "base": LEN_PRE_DECLINE + LEN_BASE,
    "breakout": LEN_PRE_DECLINE + LEN_BASE + LEN_BREAKOUT,
    "advance": LEN_PRE_DECLINE + LEN_BASE + LEN_BREAKOUT + LEN_ADVANCE,
    "top": LEN_PRE_DECLINE + LEN_BASE + LEN_BREAKOUT + LEN_ADVANCE + LEN_TOP,
    "breakdown": (LEN_PRE_DECLINE + LEN_BASE + LEN_BREAKOUT + LEN_ADVANCE
                  + LEN_TOP + LEN_BREAKDOWN),
    "decline": (LEN_PRE_DECLINE + LEN_BASE + LEN_BREAKOUT + LEN_ADVANCE
                + LEN_TOP + LEN_BREAKDOWN + LEN_DECLINE),
}


def _oscillate(n: int, center: float, amp: float, period: int = 15) -> np.ndarray:
    """确定性平台震荡：正弦往复，保证上下沿被反复触及。"""
    return center + amp * np.sin(np.arange(n) * 2.0 * np.pi / period)


@pytest.fixture(scope="module")
def cycle() -> pd.DataFrame:
    """图中一个完整循环的合成 OHLCV（450 根）。"""
    close = np.concatenate([
        np.linspace(140.0, 100.0, LEN_PRE_DECLINE),        # 前置下跌
        _oscillate(LEN_BASE, 100.0, 4.0),                  # 低位平台 96~104
        np.array([106.0, 108.0, 110.0, 111.0, 112.0]),     # 放量突破
        np.linspace(113.0, 150.0, LEN_ADVANCE),            # 上升推进
        _oscillate(LEN_TOP, 150.0, 6.0),                   # 高位平台 144~156
        np.array([142.0, 140.0, 138.0, 136.0, 134.0]),     # 跌破下沿
        np.linspace(133.0, 100.0, LEN_DECLINE),            # 下降趋势
    ])
    # 突破段放量（3× 基准），其余常量量能
    volume = np.full(len(close), 1_000_000.0)
    start = LEN_PRE_DECLINE + LEN_BASE
    volume[start:start + LEN_BREAKOUT] = 3_000_000.0
    return make_ohlcv(close, volume=volume)


def _at(cycle: pd.DataFrame, stage: str) -> pd.DataFrame:
    """取「该阶段刚形成」时点的切片。"""
    return cycle.iloc[: CUT[stage]]


# ---------------------------------------------------------------------------
# 七态判定
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("expected", ["base", "breakout", "advance", "top", "breakdown", "decline"])
def test_cycle_stages(cycle, expected):
    """一个完整循环的六段应被逐段正确定位。"""
    result = detect_stage(_at(cycle, expected), symbol="TEST.SH")
    assert result.stage == expected, (
        f"期望 {expected}（{STAGE_CN[expected]}），实得 {result.stage}"
        f"（{result.stage_cn}），规则：{result.rule}"
    )


def test_base_is_low_position(cycle):
    """低位平台的位置分位应落在低位区间，且置信度为 high。"""
    result = detect_stage(_at(cycle, "base"))
    assert result.price_position <= POSITION_LOW
    assert result.confidence == "high"
    assert "低位" in result.rule


def test_breakout_volume_confirmed(cycle):
    """放量突破 -> 置信度 high，且证据链记录量能倍数。"""
    result = detect_stage(_at(cycle, "breakout"))
    assert result.stage == "breakout"
    assert result.confidence == "high"
    assert result.structure["breakout_vol_ratio"] >= 1.5
    assert any(ev["kind"] == "volume" for ev in result.evidence)


def test_breakout_without_volume_downgrades_confidence(cycle):
    """同样的突破形态、去掉放量 -> 阶段不变但置信度降为 medium。"""
    df = _at(cycle, "breakout").copy()
    df["volume"] = 1_000_000.0  # 抹平放量
    result = detect_stage(df)
    assert result.stage == "breakout"
    assert result.confidence == "medium"


def test_top_requires_prior_gain(cycle):
    """高位平台需前置升幅达标；证据链应包含 prior_gain。"""
    result = detect_stage(_at(cycle, "top"))
    assert result.stage == "top"
    assert result.price_position >= 0.6
    gains = [ev for ev in result.evidence if ev["kind"] == "prior_gain"]
    assert gains and gains[0]["value"] >= 0.30


def test_insufficient_bars_is_unknown():
    """K 线不足 MIN_BARS -> unknown，且诚实标注原因而非猜测。"""
    df = make_ohlcv(np.linspace(100.0, 110.0, MIN_BARS - 1))
    result = detect_stage(df, symbol="SHORT.SH")
    assert result.stage == "unknown"
    assert result.price_position is None
    assert result.trigger["breakout_price"] is None
    assert str(MIN_BARS) in result.evidence[0]["text"]


def test_wide_choppy_range_is_structureless_base():
    """宽幅震荡（高度超限 + 无单边趋势）-> base + confidence=low，诚实标注结构不清。

    振幅 15%、周期 16 的往复：箱体高度 32% 超过 15% 上限不成立，而 60 日
    效率比仅 0.07 远低于趋势门槛，两边都不沾——这时不得编造状态。
    末根落在波峰（站在 MA60 上方），确保不会误入 MA60 破位兜底分支。
    """
    df = make_ohlcv(_oscillate(261, 100.0, 15.0, period=16))
    result = detect_stage(df)
    assert result.stage == "base"
    assert result.confidence == "low"
    assert "结构不清" in result.rule
    assert not result.box["valid"]
    assert result.trigger["box_valid"] is False


def test_flat_line_is_degenerate_box():
    """绝对平直的价格是一个退化的有效平台（高度极小、效率比为 0）。

    位置分位落在区间中部，因此是「中位箱体」而非低位筑底。
    """
    result = detect_stage(make_ohlcv(np.full(300, 100.0)))
    assert result.stage == "base"
    assert result.box["valid"]
    assert result.confidence == "medium"
    assert "中位箱体" in result.rule


# ---------------------------------------------------------------------------
# 箱体原语
# ---------------------------------------------------------------------------


def test_find_box_on_platform():
    """平台上的箱体应成立，几何与触及次数可解释。"""
    df = make_ohlcv(_oscillate(80, 100.0, 4.0))
    box = find_box(df, window=60)
    assert box.valid, box.reason
    assert box.low < box.mid < box.high
    assert box.height_pct <= 0.15
    assert box.touches_high >= 2 and box.touches_low >= 2
    assert box.breakout_price > box.high > box.low > box.breakdown_price


def test_find_box_rejects_trend():
    """单边趋势不构成箱体，且给出可读的不成立原因。"""
    df = make_ohlcv(np.linspace(100.0, 200.0, 80))
    box = find_box(df, window=60)
    assert not box.valid
    assert box.reason


def test_find_box_needs_enough_bars():
    """窗口内 K 线不足 10 根 -> 不成立且说明原因。"""
    df = make_ohlcv(np.linspace(100.0, 105.0, 6))
    box = find_box(df, window=60)
    assert not box.valid
    assert "不足 10 根" in box.reason


def test_exclude_tail_is_required_for_breakout(cycle):
    """回归：箱体窗口必须排除待检验尾部，否则突破永远判不出。

    突破本身会把「近 N 日最高价」抬到新高，用含突破 K 线的箱体做基准时
    「收盘上穿上沿」恒为假——这是本模块最容易踩的坑。
    """
    df = _at(cycle, "breakout")
    box_with_tail = find_box(df, window=60, exclude_tail=0)
    box_prior = find_box(df, window=60, exclude_tail=5)

    assert crossed_above(df, box_with_tail, 5) is None, "含突破 K 线的箱体不应判出突破"
    assert crossed_above(df, box_prior, 5) is not None, "排除尾部后应判出突破"
    assert box_prior.high < box_with_tail.high


def test_price_position_bounds(cycle):
    """位置分位恒在 [0,1]：底部趋近 0，顶部趋近 1。"""
    for stage in CUT:
        df = _at(cycle, stage)
        pos = _price_position(df, df["close"].astype(float))
        assert 0.0 <= pos <= 1.0
    top_df = _at(cycle, "top")
    base_df = _at(cycle, "base")
    assert _price_position(top_df, top_df["close"].astype(float)) > _price_position(
        base_df, base_df["close"].astype(float)
    )


# ---------------------------------------------------------------------------
# 历史回放与输出契约
# ---------------------------------------------------------------------------


def test_stage_history_no_lookahead(cycle):
    """逐日重算的末值必须等于对全量数据的一次性判定（无前视）。"""
    history = stage_history(cycle, days=60)
    assert history["days"] == 60
    assert history["current"] == detect_stage(cycle).stage
    assert history["series"][-1]["stage"] == history["current"]


def test_stage_history_captures_transitions(cycle):
    """跨越突破点的回放应捕获阶段迁移，且迁移记录字段完整。"""
    df = _at(cycle, "advance")
    history = stage_history(df, days=80)
    assert history["transitions"], "跨越突破/推进的区间应有阶段迁移"
    for tr in history["transitions"]:
        assert set(tr) == {"date", "from", "from_cn", "to", "to_cn"}
        assert tr["from"] in STAGES and tr["to"] in STAGES
        assert tr["from"] != tr["to"]


def test_stage_history_insufficient_bars_is_empty():
    """K 线不足时回放返回空序列而非报错。"""
    history = stage_history(make_ohlcv(np.full(100, 100.0)), days=30)
    assert history["series"] == []
    assert history["current"] == "unknown"


def test_to_dict_contract(cycle):
    """--json 输出依赖的字段必须齐全（对外契约）。"""
    payload = detect_stage(_at(cycle, "top"), symbol="TEST.SH").to_dict()
    for key in (
        "symbol", "stage", "stage_cn", "confidence", "price_position",
        "box", "trigger", "structure", "evidence", "posture", "rule",
        "asof", "n_bars",
    ):
        assert key in payload, f"缺少字段 {key}"
    assert payload["stage"] in STAGES
    assert payload["confidence"] in ("high", "medium", "low")
    for key in ("valid", "high", "low", "mid", "height_pct", "touches_high", "touches_low"):
        assert key in payload["box"]
    for key in ("breakout_price", "breakdown_price", "distance_to_breakout_pct",
                "distance_to_breakdown_pct", "box_valid"):
        assert key in payload["trigger"]


def test_every_stage_has_cn_and_posture():
    """七态必须各有中文名与应对姿态（防止新增状态漏配文案）。"""
    assert set(STAGES) == set(STAGE_CN) == set(_STAGE_POSTURE)
    for stage in STAGES:
        posture, note = _STAGE_POSTURE[stage]
        assert posture and note


def test_window_override_changes_box(cycle):
    """--window 覆盖应真实作用到箱体窗口。"""
    df = _at(cycle, "top")
    assert detect_stage(df, window=60).box["window"] == 60
    assert detect_stage(df, window=90).box["window"] == 90


# ---------------------------------------------------------------------------
# CLI 层：summary 措辞与 next_steps 契约
# ---------------------------------------------------------------------------


def test_summary_covers_every_stage(cycle):
    """七态均能生成 summary（base 走子情况分支，其余走模板，不得 KeyError）。"""
    import run_stage

    for stage in STAGES:
        if stage in CUT:
            result = detect_stage(_at(cycle, stage), symbol="TEST.SH")
        elif stage == "unknown":
            result = detect_stage(make_ohlcv(np.full(50, 100.0)), symbol="TEST.SH")
        else:  # base 已在 CUT 中
            continue
        text = run_stage._build_summary("TEST.SH", result)
        assert result.stage_cn in text
        assert "不预测涨跌" in text, "summary 必须带免责声明"


def test_summary_does_not_overclaim_low_position():
    """诚实性回归：位置分位未达低位时，summary 不得声称「低位筑底」。

    ``base`` 是伞形态（低位箱体/中位箱体/结构不清），若一律措辞为「低位筑底」，
    位置分位 50% 的标的会被误读为底部区域。
    """
    import run_stage

    mid = detect_stage(make_ohlcv(np.full(300, 100.0)), symbol="MID.SH")
    assert mid.confidence == "medium"
    assert "中位整理" in run_stage._build_summary("MID.SH", mid)

    unclear = detect_stage(make_ohlcv(_oscillate(261, 100.0, 15.0, period=16)), symbol="CHOP.SH")
    text = run_stage._build_summary("CHOP.SH", unclear)
    assert "结构不清" in text
    assert "箱体不成立，价位仅供参考" in text, "箱体不成立时关键价位必须带限定"


def test_next_steps_always_hands_back_to_scoring(cycle):
    """next_steps 必须恒含无条件的 score 一项（买卖裁决不归阶段模块）。"""
    import run_stage

    for stage in CUT:
        result = detect_stage(_at(cycle, stage), symbol="TEST.SH")
        steps = run_stage._build_next_steps("TEST.SH", result)
        score_steps = [s for s in steps if s["action"] == "score"]
        assert score_steps, "缺少交回三灯的 score 步骤"
        assert "condition" not in score_steps[0], "score 应为无条件推荐"
        for s in steps:
            assert {"action", "reason", "command"} <= set(s)


def test_next_steps_conditions_are_evaluable(cycle):
    """next_steps 的 condition 必须能被 cli_common.eval_condition 求值。

    条件引用同一 JSON 输出中的字段，若字段名写错会恒假，Agent 就永远不会采纳。
    """
    import run_stage
    from cli_common import eval_condition

    result = detect_stage(_at(cycle, "breakout"), symbol="TEST.SH")
    payload = result.to_dict()
    steps = run_stage._build_next_steps("TEST.SH", result)
    conditional = [s for s in steps if "condition" in s]
    assert conditional
    hit = [s for s in conditional if eval_condition(s["condition"], payload)]
    assert [s["action"] for s in hit] == ["backtest_wisdom"], (
        "突破态下应恰好命中 wisdom 回测建议"
    )
