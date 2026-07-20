"""pytest 公共夹具（fixtures）。

提供确定性的合成 OHLCV 数据，避免测试依赖网络与 TickFlow。
所有随机过程使用固定 seed，保证回归测试可复现。
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.helpers import make_ohlcv


@pytest.fixture
def random_walk_df() -> pd.DataFrame:
    """确定性随机游走价格（seed 固定），约 300 根日 K。"""
    rng = np.random.default_rng(42)
    steps = rng.normal(loc=0.0005, scale=0.02, size=300)
    close = 100.0 * np.exp(np.cumsum(steps))
    return make_ohlcv(close)


@pytest.fixture
def trending_up_df() -> pd.DataFrame:
    """单调上行价格：用于验证多头/买入持有一致性。"""
    close = 100.0 * (1.0 + 0.01) ** np.arange(120)
    return make_ohlcv(close)


@pytest.fixture
def trending_down_df() -> pd.DataFrame:
    """单调下行价格：用于验证做空盈亏与止损。"""
    close = 100.0 * (1.0 - 0.01) ** np.arange(120)
    return make_ohlcv(close)
