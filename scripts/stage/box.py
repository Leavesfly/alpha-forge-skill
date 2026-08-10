"""箱体（平台）识别原语。

「平台」是阶段定位的基本积木：股价在一段时间内被上下沿夹住来回震荡，
既不是趋势也不是随机游走。突破平台上沿是关键点（图中的买入点/加股），
跌破下沿是趋势破坏（图中的卖出点）。

箱体成立需同时满足四个条件（缺一不可，避免把趋势段误认成平台）：

1. **高度受限**：``(上沿 - 下沿) / 中轴 <= max_height``，太宽的区间不是平台；
2. **无单边趋势**：窗口内 Kaufman 效率比 ``ER <= max_er``，ER 高说明在走趋势；
3. **上沿触及 >= min_touches**：只碰一次的高点是偶然，不构成阻力；
4. **下沿触及 >= min_touches**：同理，需被反复确认才算支撑。

**为什么需要 exclude_tail**：突破发生后，最近 N 根 K 线的最高价就是突破
本身创出的新高，若用含这些 K 线的窗口算上沿，「收盘价上穿上沿」永远为假。
故判断突破/破位时必须用 ``exclude_tail=confirm_days`` 排除待检验的尾部 K 线，
拿「突破之前的箱体」做基准。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

#: 箱体高度上限：(上沿-下沿)/中轴，超过视为区间过宽不成箱体
MAX_BOX_HEIGHT = 0.15

#: 窗口效率比上限：超过说明在走单边趋势而非横盘整理
MAX_BOX_ER = 0.30

#: 上/下沿各自最少触及次数
MIN_TOUCHES = 2

#: 触及容差：距上下沿 2% 以内即算触及（收盘精确打在沿上是极小概率事件）
TOUCH_TOLERANCE = 0.02

#: 突破/破位价的缓冲：上沿上方 0.5% 才算有效突破，过滤贴沿假动作
BREAK_BUFFER = 0.005


@dataclass
class Box:
    """一个箱体（平台）的几何与成立性。

    Attributes:
        high: 上沿（窗口内最高价）。
        low: 下沿（窗口内最低价）。
        mid: 中轴 ``(high + low) / 2``。
        height_pct: 相对高度 ``(high - low) / mid``。
        er: 窗口内 Kaufman 效率比（越低越横盘）。
        touches_high: 上沿触及次数。
        touches_low: 下沿触及次数。
        valid: 四条件是否全部满足。
        window: 使用的窗口长度。
        exclude_tail: 排除的尾部 K 线数。
        reason: 不成立的原因（valid=True 时为空串）。
    """

    high: float
    low: float
    mid: float
    height_pct: float
    er: float
    touches_high: int
    touches_low: int
    valid: bool
    window: int
    exclude_tail: int
    reason: str = ""

    @property
    def breakout_price(self) -> float:
        """有效突破价：上沿 × (1 + 缓冲)。"""
        return self.high * (1.0 + BREAK_BUFFER)

    @property
    def breakdown_price(self) -> float:
        """有效破位价：下沿 × (1 - 缓冲)。"""
        return self.low * (1.0 - BREAK_BUFFER)

    def to_dict(self) -> dict:
        """JSON 友好的字典（价格保留 4 位，比例保留 4 位）。"""
        return {
            "valid": bool(self.valid),
            "high": round(self.high, 4),
            "low": round(self.low, 4),
            "mid": round(self.mid, 4),
            "height_pct": round(self.height_pct, 4),
            "er": round(self.er, 4),
            "touches_high": int(self.touches_high),
            "touches_low": int(self.touches_low),
            "window": int(self.window),
            "exclude_tail": int(self.exclude_tail),
            "reason": self.reason,
        }


def _window_slice(df: pd.DataFrame, window: int, exclude_tail: int) -> pd.DataFrame:
    """取用于箱体计算的窗口切片：排除尾部 exclude_tail 根后的最近 window 根。"""
    end = len(df) - exclude_tail
    start = max(0, end - window)
    return df.iloc[start:end]


def find_box(
    df: pd.DataFrame,
    window: int = 60,
    exclude_tail: int = 0,
    max_height: float = MAX_BOX_HEIGHT,
    max_er: float = MAX_BOX_ER,
    min_touches: int = MIN_TOUCHES,
    tolerance: float = TOUCH_TOLERANCE,
) -> Box:
    """在指定窗口内识别箱体。

    Args:
        df: 含 ``close``（可选 ``high``/``low``）列的 OHLCV，按时间升序。
        window: 箱体窗口长度（交易日）。
        exclude_tail: 排除尾部若干根 K 线（判断突破时传 confirm_days）。
        max_height: 箱体相对高度上限。
        max_er: 窗口效率比上限。
        min_touches: 上/下沿各自最少触及次数。
        tolerance: 触及容差比例。

    Returns:
        :class:`Box`；窗口内数据不足（< 10 根）时返回 ``valid=False`` 且
        上下沿取现有极值，``reason`` 说明原因。
    """
    seg = _window_slice(df, window, exclude_tail)
    n = len(seg)
    if n < 10:
        return Box(
            high=float("nan"), low=float("nan"), mid=float("nan"),
            height_pct=float("nan"), er=float("nan"),
            touches_high=0, touches_low=0, valid=False,
            window=window, exclude_tail=exclude_tail,
            reason=f"窗口内仅 {n} 根 K 线，不足 10 根",
        )

    close = seg["close"].astype(float)
    high_s = seg["high"].astype(float) if "high" in seg.columns else close
    low_s = seg["low"].astype(float) if "low" in seg.columns else close

    high = float(high_s.max())
    low = float(low_s.min())
    mid = (high + low) / 2.0
    height_pct = (high - low) / mid if mid > 0 else float("inf")

    # 窗口内效率比：|净变动| / Σ|逐日变动|，与 scoring.indicators 同公式
    net = abs(float(close.iloc[-1]) - float(close.iloc[0]))
    path = float(close.diff().abs().sum())
    er = net / path if path > 0 else 0.0

    touches_high = int((high_s >= high * (1.0 - tolerance)).sum())
    touches_low = int((low_s <= low * (1.0 + tolerance)).sum())

    reasons = []
    if not np.isfinite(height_pct) or height_pct > max_height:
        reasons.append(f"高度 {height_pct:.1%} > {max_height:.0%}")
    if er > max_er:
        reasons.append(f"效率比 {er:.2f} > {max_er:.2f}（在走趋势）")
    if touches_high < min_touches:
        reasons.append(f"上沿仅触及 {touches_high} 次")
    if touches_low < min_touches:
        reasons.append(f"下沿仅触及 {touches_low} 次")

    return Box(
        high=high, low=low, mid=mid, height_pct=height_pct, er=er,
        touches_high=touches_high, touches_low=touches_low,
        valid=not reasons, window=window, exclude_tail=exclude_tail,
        reason="；".join(reasons),
    )


def crossed_above(df: pd.DataFrame, box: Box, days: int) -> int | None:
    """最近 days 根 K 线内收盘价是否有效上穿箱体上沿。

    「有效」= 收盘价 >= 上沿 × (1 + 缓冲)，过滤贴沿的假突破。

    Args:
        df: OHLCV（升序）。
        box: 基准箱体（应由 ``exclude_tail=days`` 得到，否则上沿会被突破本身抬高）。
        days: 检验窗口（根）。

    Returns:
        距今多少根 K 线前发生突破（0 = 最新一根），未突破返回 None。
        多次突破时返回**最早**的那次（首次突破才是关键点）。
    """
    return _first_break(df, box.breakout_price, days, above=True)


def crossed_below(df: pd.DataFrame, box: Box, days: int) -> int | None:
    """最近 days 根 K 线内收盘价是否有效下破箱体下沿。

    Returns:
        距今多少根 K 线前发生破位（0 = 最新一根），未破位返回 None。
    """
    return _first_break(df, box.breakdown_price, days, above=False)


def _first_break(df: pd.DataFrame, level: float, days: int, above: bool) -> int | None:
    """在最近 days 根内找首次穿越 level 的位置，返回距今根数。"""
    if not np.isfinite(level) or days <= 0 or len(df) == 0:
        return None
    tail = df["close"].astype(float).iloc[-days:]
    hit = tail >= level if above else tail <= level
    if not bool(hit.any()):
        return None
    # 首次穿越在 tail 中的序号 -> 距今根数
    pos = int(np.argmax(hit.to_numpy()))
    return len(tail) - 1 - pos
