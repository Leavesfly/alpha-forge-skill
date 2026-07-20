"""市场状态识别（Regime Detection）。

用两个免模型的稳健指标把市场分为四种状态：

- **趋势强度**：Kaufman 效率比 ER = |净涨跌| / Σ|日涨跌|（0~1，越高越单边）；
- **波动水平**：近 20 日年化波动率在自身历史中的分位数。

分类规则（简单可解释，优先级：波动 > 趋势）::

    vol 分位 >= 0.80 且 vol >= 1.5×中位 -> volatile   高波动（先降仓）
    ER >= 0.25 且收盘 > MA(window)       -> trend_up   趋势上行
    ER >= 0.25 且收盘 < MA(window)       -> trend_down 趋势下行
    其余                                  -> range      震荡

用途：
- run_score.py 输出当前市场状态作为决策上下文；
- run_compare.py 据此提示「当前状态更适合哪一族策略」——
  趋势市适合趋势跟随族，震荡市适合均值回归族（样本内冠军若与状态不符需警惕）。

状态识别是描述性统计而非预测：状态切换只能事后确认，存在滞后。
"""

from __future__ import annotations

import math

import pandas as pd

#: 效率比高于该值视为趋势市
ER_TREND_THRESHOLD = 0.25

#: 波动率分位高于该值视为高波动状态
VOL_PERCENTILE_THRESHOLD = 0.80

#: 高波动还需满足：当前波动 >= 该倍数 × 历史中位波动（避免低波动标的的小波动被分位放大）
VOL_RATIO_THRESHOLD = 1.5

#: 状态中文名
REGIME_CN = {
    "trend_up": "趋势上行",
    "trend_down": "趋势下行",
    "range": "震荡",
    "volatile": "高波动",
    "unknown": "无法判定",
}

#: 策略族：趋势跟随 vs 均值回归（与 strategies/ 注册名对应）
TREND_FAMILY = (
    "ma_cross", "macd", "momentum", "donchian", "turtle",
    "keltner", "supertrend", "dual_thrust",
)
REVERSION_FAMILY = ("rsi", "bollinger", "kdj", "grid", "cci", "williams_r")

#: 各状态更适合的策略族与提示
_REGIME_ADVICE = {
    "trend_up": ("趋势跟随族", TREND_FAMILY, "顺势持有，避免过早均值回归抄顶"),
    "trend_down": ("趋势跟随族（做空）或空仓", TREND_FAMILY, "多头策略胜率下降，考虑 --allow-short 或观望"),
    "range": ("均值回归族", REVERSION_FAMILY, "趋势策略易被反复止损，网格/超买超卖类更适合"),
    "volatile": ("低仓位/观望", (), "高波动下任何策略滑点与止损成本都被放大，优先降仓"),
    "unknown": ("——", (), "数据不足，无法判定状态"),
}


def detect_regime(close: pd.Series, window: int = 60, vol_window: int = 20) -> dict:
    """识别当前市场状态。

    Args:
        close: 收盘价序列（升序）。
        window: 趋势窗口（效率比与均线基准），默认 60。
        vol_window: 波动率滚动窗口，默认 20。

    Returns:
        dict，含 ``regime``/``regime_cn``/``efficiency_ratio``/
        ``vol_percentile``/``above_ma``/``advice`` 等键；
        数据不足（< window + vol_window）时 ``regime="unknown"``。
    """
    close = pd.Series(close, dtype=float).dropna().reset_index(drop=True)
    if len(close) < window + vol_window:
        return _build("unknown", None, None, None, window)

    seg = close.iloc[-window:]
    net = abs(float(seg.iloc[-1]) - float(seg.iloc[0]))
    path = float(seg.diff().abs().sum())
    er = net / path if path > 0 else 0.0

    ma = float(seg.mean())
    above_ma = float(seg.iloc[-1]) > ma

    # 近 vol_window 日年化波动率在全样本滚动波动率中的分位（中位秩：
    # 并列值各计一半，避免波动恒定时 <= 分位恒为 1.0 被误判高波动）
    ret = close.pct_change()
    roll_vol = ret.rolling(vol_window).std() * math.sqrt(252)
    vol_hist = roll_vol.dropna()
    cur_vol = float(vol_hist.iloc[-1])
    vol_pct = float(
        ((vol_hist < cur_vol).mean() + 0.5 * (vol_hist == cur_vol).mean())
    )
    median_vol = float(vol_hist.median())
    vol_elevated = median_vol > 0 and cur_vol >= VOL_RATIO_THRESHOLD * median_vol

    if vol_pct >= VOL_PERCENTILE_THRESHOLD and vol_elevated:
        regime = "volatile"
    elif er >= ER_TREND_THRESHOLD:
        regime = "trend_up" if above_ma else "trend_down"
    else:
        regime = "range"
    return _build(regime, er, vol_pct, above_ma, window)


def _build(regime: str, er, vol_pct, above_ma, window: int) -> dict:
    family_name, family, note = _REGIME_ADVICE[regime]
    return {
        "regime": regime,
        "regime_cn": REGIME_CN[regime],
        "efficiency_ratio": round(er, 4) if er is not None else None,
        "vol_percentile": round(vol_pct, 4) if vol_pct is not None else None,
        "above_ma": above_ma,
        "window": window,
        "suited_family": family_name,
        "suited_strategies": list(family),
        "advice": note,
    }


def format_regime(info: dict) -> str:
    """单行文字描述，供 CLI 输出。"""
    if info["regime"] == "unknown":
        return f"市场状态：{info['regime_cn']}（K 线不足 {info['window']} + 波动窗口）"
    er = info["efficiency_ratio"]
    vp = info["vol_percentile"]
    return (
        f"市场状态：{info['regime_cn']}（趋势效率 {er:.2f}，波动率分位 {vp:.0%}）"
        f"→ 更适合：{info['suited_family']}。{info['advice']}"
    )
