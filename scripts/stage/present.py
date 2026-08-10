"""阶段定位的终端渲染。

只负责把 :class:`~stage.engine.StageResult` 转成人类可读的文本行，
不依赖 CLI 层、不做任何计算，便于被其他入口复用。
"""

from __future__ import annotations

from typing import Callable

from .engine import POSITION_WINDOW, STAGE_CN

#: 免责声明（阶段是描述性统计，不是预测，也不等于买卖建议）
STAGE_DISCLAIMER = (
    "阶段定位是对已发生价量结构的描述性统计，存在滞后（状态只能事后确认），"
    "不预测涨跌、不构成投资建议；「能不能买、买多少」请用 run_score.py 的三灯裁决。"
)

#: 置信度中文
_CONFIDENCE_CN = {"high": "高", "medium": "中", "low": "低"}

#: 阶段的可视标记（终端无颜色依赖，用符号区分强弱）
_STAGE_MARK = {
    "base": "○",
    "breakout": "▲",
    "advance": "↑",
    "top": "◇",
    "breakdown": "▼",
    "decline": "↓",
    "unknown": "?",
}


def print_stage_report(
    result,
    log: Callable[..., None],
    brief: bool = False,
    history: dict | None = None,
) -> None:
    """打印阶段定位报告。

    Args:
        result: :class:`~stage.engine.StageResult`。
        log: 输出函数（``--json`` 时由 CLI 转向 stderr）。
        brief: 简短模式，只输出结论与关键价位。
        history: :func:`~stage.engine.stage_history` 的返回值，提供时附迁移轨迹。
    """
    mark = _STAGE_MARK.get(result.stage, "?")
    conf = _CONFIDENCE_CN.get(result.confidence, result.confidence)
    log("")
    log(f"=== {result.symbol} 阶段定位（截至 {result.asof}，{result.n_bars} 根 K 线） ===")
    log(f"  当前阶段: {mark} {result.stage_cn}（{result.stage}），置信度 {conf}")
    log(f"  判定依据: {result.rule}")
    if result.price_position is not None:
        log(f"  位置分位: {result.price_position:.0%}（近 {POSITION_WINDOW} 日区间，"
            f"越高越接近区间顶部）")

    _print_trigger(result, log)

    if brief:
        log(f"  应对姿态: {result.posture.get('posture', '')}")
        return

    log("")
    log("--- 结构证据 ---")
    for ev in result.evidence:
        log(f"  · [{ev['kind']}] {ev['text']}")

    s = result.structure
    if s:
        log("")
        log("--- 结构指标 ---")
        log(f"  收盘 {_num(s.get('close'))} | MA20 {_num(s.get('ma20'))} | "
            f"MA60 {_num(s.get('ma60'))} | MA200 {_num(s.get('ma200'))}")
        log(f"  MA60 斜率 {_pct(s.get('ma60_slope'))}（{s.get('slope_window')} 日）| "
            f"效率比 {_num(s.get('er'), 2)}（{s.get('er_window')} 日）")

    log("")
    log("--- 应对姿态 ---")
    log(f"  {result.posture.get('posture', '')}：{result.posture.get('note', '')}")

    if history:
        _print_history(history, log)


def _print_trigger(result, log: Callable[..., None]) -> None:
    """打印箱体上下沿派生的关键价位。"""
    t = result.trigger or {}
    if not t.get("breakout_price"):
        log("  关键价位: 箱体不成立，暂无有效上下沿（结构不清时不编造价位）")
        return
    up_d = t.get("distance_to_breakout_pct")
    dn_d = t.get("distance_to_breakdown_pct")
    valid = "成立" if t.get("box_valid") else "不成立（价位仅供参考）"
    log(f"  关键价位: 突破价 {t['breakout_price']}"
        f"{f'（距今 {up_d:+.1%}）' if up_d is not None else ''} / "
        f"破位价 {t['breakdown_price']}"
        f"{f'（距今 {dn_d:+.1%}）' if dn_d is not None else ''}；箱体{valid}")


def _print_history(history: dict, log: Callable[..., None]) -> None:
    """打印阶段迁移轨迹（一个完整循环的演进过程）。"""
    log("")
    log(f"--- 阶段迁移（最近 {history.get('days', 0)} 个交易日，逐日重算无前视） ---")
    transitions = history.get("transitions") or []
    if not transitions:
        log("  区间内阶段未发生变化（结构稳定）")
        return
    for tr in transitions:
        log(f"  {tr['date']}  {tr['from_cn']} → {tr['to_cn']}")
    counts: dict[str, int] = {}
    for item in history.get("series") or []:
        counts[item["stage"]] = counts.get(item["stage"], 0) + 1
    dist = " / ".join(
        f"{STAGE_CN[k]} {v} 日" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
    )
    log(f"  区间分布: {dist}")


def _num(value, digits: int = 2) -> str:
    """数值格式化（None -> 「不可用」）。"""
    return "不可用" if value is None else f"{value:.{digits}f}"


def _pct(value) -> str:
    """百分比格式化（None -> 「不可用」）。"""
    return "不可用" if value is None else f"{value:+.2%}"
