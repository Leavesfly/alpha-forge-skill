"""因子库：计算价格类与基本面类因子，统一为「日期 × 标的」的因子值矩阵。

约定：所有因子按「数值越大越好」的方向输出（高分对应偏好方向）。
- 价格因子（动量、低波动）：从收盘价矩阵时序计算。
- 基本面因子（价值、质量、规模）：从财务指标截面按报告期滞后对齐到交易日，
  以规避前视；财务数据不可用时返回 None，由调用方跳过。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

#: 因子值矩阵类型：index=日期，columns=标的
FactorFrame = pd.DataFrame


@dataclass
class FactorDef:
    """因子定义。"""

    display_name: str
    category: str  # price / value / quality / size
    func: Callable[..., FactorFrame | None]


def _find_field(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """在 DataFrame 列中按候选名（忽略大小写）查找第一个匹配列。"""
    lower = {str(c).lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower:
            return lower[cand.lower()]
    return None


_DATE_CANDIDATES = ["period_end", "report_date", "end_date", "ann_date", "报告期"]


def _align_fundamental(
    fundamentals: pd.DataFrame,
    candidates: list[str],
    index: pd.Index,
    columns: list[str],
    lag_days: int,
) -> FactorFrame | None:
    """将某财务字段按报告期滞后对齐为「日期 × 标的」矩阵。"""
    field = _find_field(fundamentals, candidates)
    if field is None or "symbol" not in fundamentals.columns:
        return None
    date_col = _find_field(fundamentals, _DATE_CANDIDATES)

    if date_col is None:
        # 无报告期：退化为最新截面广播（存在前视风险，仅作降级）
        cross = fundamentals.groupby("symbol")[field].last()
        wide = pd.DataFrame(index=index, columns=columns, dtype=float)
        for sym in columns:
            if sym in cross.index:
                wide[sym] = float(cross[sym])
        return wide

    fund = fundamentals[["symbol", date_col, field]].copy()
    fund[date_col] = pd.to_datetime(fund[date_col]) + pd.Timedelta(days=lag_days)
    piv = fund.pivot_table(
        index=date_col, columns="symbol", values=field, aggfunc="last"
    ).sort_index()
    # 前向填充到交易日索引，滞后后的报告期生效
    wide = piv.reindex(index.union(piv.index)).ffill().reindex(index)
    return wide.reindex(columns=columns)


def _momentum(prices, fundamentals, lookback, lag_days) -> FactorFrame:
    """动量：过去 lookback 期收益率（越高越好）。"""
    return prices.pct_change(lookback)


def _low_vol(prices, fundamentals, lookback, lag_days) -> FactorFrame:
    """低波动异象：收益率滚动波动的相反数（越低波越好）。"""
    return -prices.pct_change().rolling(lookback).std()


def _reversal(prices, fundamentals, lookback, lag_days) -> FactorFrame:
    """短期反转：近 1 月（≤21 期）收益的相反数（超跌者得高分）。

    A 股短周期均值回归效应显著，与中期动量互补。窗口取
    min(21, lookback) 避免与动量窗口重叠。
    """
    window = min(21, lookback)
    return -prices.pct_change(window)


def _sharpe_mom(prices, fundamentals, lookback, lag_days) -> FactorFrame:
    """风险调整动量：滚动均值收益 / 滚动波动（涨得稳者得高分）。

    相比纯动量降低高波动个股的排名，择股更偏好趋势平滑的标的。"""
    ret = prices.pct_change()
    vol = ret.rolling(lookback).std()
    return ret.rolling(lookback).mean() / vol.where(vol > 0)


def _consistency(prices, fundamentals, lookback, lag_days) -> FactorFrame:
    """趋势一致性：滚动窗口内上涨天数占比（越稳定上行越好）。"""
    up = (prices.pct_change() > 0).astype(float)
    return up.rolling(lookback).mean()


def _make_fundamental(
    candidates: list[str],
    invert: bool = False,
    negate: bool = False,
    use_log: bool = False,
) -> Callable[..., FactorFrame | None]:
    """构造基本面因子计算函数。

    Args:
        candidates: 字段候选名（动态探测）。
        invert: 取倒数（如 PE->EP，仅对正值）。
        negate: 取相反数（如资产负债率、市值需小者得高分）。
        use_log: 先取对数（如市值）。
    """

    def _factor(prices, fundamentals, lookback, lag_days) -> FactorFrame | None:
        if fundamentals is None:
            return None
        raw = _align_fundamental(
            fundamentals, candidates, prices.index, list(prices.columns), lag_days
        )
        if raw is None:
            return None
        val = raw.astype(float)
        if invert:
            val = 1.0 / val.where(val > 0)
        if use_log:
            val = np.log(val.where(val > 0))
        if negate:
            val = -val
        return val

    return _factor


#: 因子注册表：名称 -> FactorDef
FACTORS: dict[str, FactorDef] = {
    "momentum": FactorDef("动量", "price", _momentum),
    "low_vol": FactorDef("低波动", "price", _low_vol),
    "reversal": FactorDef("短期反转", "price", _reversal),
    "sharpe_mom": FactorDef("风险调整动量", "price", _sharpe_mom),
    "consistency": FactorDef("趋势一致性", "price", _consistency),
    "value_ep": FactorDef(
        "价值(EP=1/PE)", "value",
        _make_fundamental(["pe", "pe_ttm", "pe_ratio", "市盈率"], invert=True),
    ),
    "value_bp": FactorDef(
        "价值(BP=1/PB)", "value",
        _make_fundamental(["pb", "pb_ratio", "市净率"], invert=True),
    ),
    "quality_roe": FactorDef(
        "质量(ROE)", "quality",
        _make_fundamental(["roe", "roe_ttm", "净资产收益率"]),
    ),
    "quality_debt": FactorDef(
        "质量(低负债率)", "quality",
        _make_fundamental(
            ["debt_to_assets", "debt_asset_ratio", "liability_ratio", "资产负债率"],
            negate=True,
        ),
    ),
    "size": FactorDef(
        "规模(小市值)", "size",
        _make_fundamental(
            ["total_mv", "market_cap", "total_market_value", "mkt_cap", "总市值"],
            use_log=True, negate=True,
        ),
    ),
}

#: 默认启用的价格类因子（无需财务权限）
PRICE_FACTORS = [name for name, d in FACTORS.items() if d.category == "price"]


def compute_factor(
    name: str,
    prices: pd.DataFrame,
    fundamentals: pd.DataFrame | None,
    lookback: int = 60,
    lag_days: int = 60,
) -> FactorFrame | None:
    """按名称计算单个因子矩阵；不可用时返回 None。"""
    if name not in FACTORS:
        raise KeyError(f"未知因子 '{name}'，可选：{', '.join(FACTORS)}")
    return FACTORS[name].func(prices, fundamentals, lookback, lag_days)
