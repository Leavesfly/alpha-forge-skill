"""量化策略包。

提供 STRATEGIES 注册表，CLI 与寻优模块通过名称获取策略类。
"""

from __future__ import annotations

from .base import Strategy
from .bollinger import BollingerStrategy
from .donchian import DonchianStrategy
from .grid_trading import GridStrategy
from .kdj import KDJStrategy
from .macd import MACDStrategy
from .ma_cross import MACrossStrategy
from .momentum import MomentumStrategy
from .rsi import RSIStrategy
from .turtle import TurtleStrategy

#: 策略名称 -> 策略类 的注册表
STRATEGIES: dict[str, type[Strategy]] = {
    MACrossStrategy.name: MACrossStrategy,
    MACDStrategy.name: MACDStrategy,
    RSIStrategy.name: RSIStrategy,
    BollingerStrategy.name: BollingerStrategy,
    MomentumStrategy.name: MomentumStrategy,
    DonchianStrategy.name: DonchianStrategy,
    KDJStrategy.name: KDJStrategy,
    GridStrategy.name: GridStrategy,
    TurtleStrategy.name: TurtleStrategy,
}


def get_strategy(name: str, **params) -> Strategy:
    """按名称实例化策略。"""
    if name not in STRATEGIES:
        available = ", ".join(STRATEGIES)
        raise KeyError(f"未知策略 '{name}'，可选：{available}")
    return STRATEGIES[name](**params)


__all__ = [
    "Strategy",
    "STRATEGIES",
    "get_strategy",
    "MACrossStrategy",
    "MACDStrategy",
    "RSIStrategy",
    "BollingerStrategy",
    "MomentumStrategy",
    "DonchianStrategy",
    "KDJStrategy",
    "GridStrategy",
    "TurtleStrategy",
]
