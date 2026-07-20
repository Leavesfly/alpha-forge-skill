"""交易成本模型。

将回测中的交易成本从「单一比例」升级为可组合的成本项，贴近真实市场：
- commission 佣金（双边比例）
- slippage   滑点（双边比例）
- stamp_duty 印花税（A 股仅卖出单边征收）
- transfer_fee 过户费（双边比例，A 股很小）

成本按「持仓变动（换手）」计提，与收益率复利式引擎一致：
    cost[t] = 换手[t] × 费率
其中买入换手与卖出换手分开计量，以支持卖出单边印花税。

为保持向后兼容：当 stamp_duty=0 且 transfer_fee=0 时，
总成本 = 总换手 × (commission + slippage)，与旧引擎完全一致。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

#: A 股印花税率（2023-08 起由 0.1% 下调至 0.05%，仅卖出征收）
ASTOCK_STAMP_DUTY = 0.0005
#: A 股过户费率（沪深统一约 0.001%，双边）
ASTOCK_TRANSFER_FEE = 0.00001


@dataclass
class CostModel:
    """可组合的交易成本模型。

    Attributes:
        commission: 佣金率（双边，按换手计提）。
        slippage: 滑点率（双边，按换手计提）。
        stamp_duty: 印花税率（仅卖出换手计提）。
        transfer_fee: 过户费率（双边，按换手计提）。
    """

    commission: float = 0.0005
    slippage: float = 0.0005
    stamp_duty: float = 0.0
    transfer_fee: float = 0.0

    @classmethod
    def preset(
        cls,
        market: str = "generic",
        commission: float | None = None,
        slippage: float | None = None,
    ) -> "CostModel":
        """按市场预设构造成本模型。

        Args:
            market: ``generic``（默认，无印花税/过户费）或 ``astock``
                （A 股：卖出印花税 + 双边过户费）。
            commission: 覆盖默认佣金率。
            slippage: 覆盖默认滑点率。
        """
        market = (market or "generic").lower()
        comm = 0.0005 if commission is None else commission
        slip = 0.0005 if slippage is None else slippage
        if market in ("astock", "a", "cn", "ashare"):
            return cls(
                commission=comm,
                slippage=slip,
                stamp_duty=ASTOCK_STAMP_DUTY,
                transfer_fee=ASTOCK_TRANSFER_FEE,
            )
        if market in ("generic", "none", "us", "hk"):
            return cls(commission=comm, slippage=slip)
        raise ValueError(f"未知市场预设 '{market}'，可选：generic / astock")

    def costs(
        self, buy_turnover: pd.Series, sell_turnover: pd.Series
    ) -> pd.Series:
        """按买入/卖出换手计提逐周期成本。

        Args:
            buy_turnover: 买入方向换手（>=0）。
            sell_turnover: 卖出方向换手（>=0）。

        Returns:
            与输入等长的逐周期成本比率序列。
        """
        both = buy_turnover + sell_turnover
        return (
            both * (self.commission + self.slippage)
            + both * self.transfer_fee
            + sell_turnover * self.stamp_duty
        )

    def describe(self) -> str:
        """一行文本描述，便于报告展示。"""
        return (
            f"佣金 {self.commission * 1e4:.1f}bp / 滑点 {self.slippage * 1e4:.1f}bp"
            f" / 印花税 {self.stamp_duty * 1e4:.1f}bp(卖)"
            f" / 过户费 {self.transfer_fee * 1e4:.2f}bp"
        )
