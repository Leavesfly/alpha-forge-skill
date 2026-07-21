"""量化策略包。

提供 STRATEGIES 注册表，CLI 与寻优模块通过名称获取策略类。
策略子类定义 ``name`` 后自动注册（见 base.py 的 __init_subclass__），
本模块只需 import 各策略文件触发注册即可。
"""

from __future__ import annotations

from .base import STRATEGIES, Strategy

# 导入各策略文件触发自动注册（无需手动维护 STRATEGIES dict）
from .bollinger import BollingerStrategy
from .cci import CCIStrategy
from .donchian import DonchianStrategy
from .dual_thrust import DualThrustStrategy
from .grid_trading import GridStrategy
from .kdj import KDJStrategy
from .keltner import KeltnerStrategy
from .macd import MACDStrategy
from .ma_cross import MACrossStrategy
from .momentum import MomentumStrategy
from .rsi import RSIStrategy
from .supertrend import SuperTrendStrategy
from .turtle import TurtleStrategy
from .williams_r import WilliamsRStrategy


def get_strategy(name: str, **params) -> Strategy:
    """按名称实例化策略。"""
    if name not in STRATEGIES:
        available = ", ".join(STRATEGIES)
        raise ValueError(f"未知策略 '{name}'，可选：{available}")
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
    "KeltnerStrategy",
    "SuperTrendStrategy",
    "DualThrustStrategy",
    "CCIStrategy",
    "WilliamsRStrategy",
]
