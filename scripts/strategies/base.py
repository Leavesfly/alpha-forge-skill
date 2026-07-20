"""策略基类。

所有策略接收标准化的 OHLCV DataFrame，输出目标持仓信号 Series：
     1 = 满仓多头
     0 = 空仓
    -1 = 满仓空头（仅在策略开启 allow_short 时输出）

引擎会对信号做 shift(1) 处理以避免前视偏差（未来函数），
因此策略内部可以直接使用当日收盘价计算指标。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    """量化策略抽象基类。"""

    #: 策略在注册表与 CLI 中使用的唯一名称
    name: str = "base"

    #: 策略中文显示名
    display_name: str = "基础策略"

    #: 参数寻优时使用的默认参数网格：{参数名: [候选值, ...]}
    param_grid: dict[str, list] = {}

    def __init__(self, **params):
        # 用默认参数占位，再用传入参数覆盖
        self.params = {**self.default_params(), **params}
        self.validate_params()

    @classmethod
    def default_params(cls) -> dict:
        """返回策略默认参数。子类应覆盖。"""
        return {}

    def validate_params(self) -> None:
        """校验参数组合合法性，非法时抛 ValueError（含怎么改的提示）。

        子类按需覆盖（如双均线要求 fast < slow）；CLI 层由 run_cli
        统一转为友好错误，寻优网格中的非法组合会被自动跳过。
        """

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """根据 OHLCV 数据生成目标持仓信号。

        Args:
            df: 至少包含 ``close`` 列的 DataFrame，索引为时间顺序。

        Returns:
            与 ``df`` 等长、取值 {-1, 0, 1} 的持仓信号 Series。
        """
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - 便于调试
        param_str = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.__class__.__name__}({param_str})"
