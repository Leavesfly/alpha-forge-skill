"""pytest 公共夹具（fixtures）。

提供确定性的合成 OHLCV 数据，避免测试依赖网络与 TickFlow。
所有随机过程使用固定 seed，保证回归测试可复现。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from data import calendar as _calendar
from envconfig import reset_env_config
from tests.helpers import make_ohlcv


@pytest.fixture(autouse=True)
def _reset_env_config():
    """每个测试前重置环境变量配置缓存，确保 monkeypatch.setenv 生效。"""
    reset_env_config()
    yield
    reset_env_config()


@pytest.fixture(autouse=True)
def _isolate_cache_dir(tmp_path, monkeypatch):
    """每个测试用独立的临时缓存目录。

    数据层的 ``load_table`` / ``load_json_obj``（宏观、估值分位、分红、财务、
    screener 快照等）默认写 ``resolve_cache_dir()``——也就是用户真实缓存目录。
    不隔离会有两个后果：① 测试的 mock 假数据落进真存，污染后续真实分析；
    ② 测试间互相干扰（前一个测试写的缓存被后一个测试命中）。
    """
    monkeypatch.setenv("ALPHA_FORGE_CACHE_DIR", str(tmp_path / "_cache"))


@pytest.fixture(autouse=True)
def _offline_calendar(request, monkeypatch):
    """屏蔽 akshare 权威交易日列表，保证离线套件不因日历触网。

    缓存新鲜度判定（cache._is_fresh）会调交易日历，A 股路径下会尝试
    拉 akshare。默认返回 None 强制走内置静态表（authoritative=False）；
    需要验证权威日历分支的测试自行 monkeypatch 该函数。

    标了 ``live`` 的实测用例不屏蔽——它们要验证的正是真实日历。
    """
    _calendar.reset_calendar_cache()
    if "live" not in request.keywords:
        monkeypatch.setattr(_calendar, "_cn_trading_days", lambda: None)
    yield
    _calendar.reset_calendar_cache()


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
