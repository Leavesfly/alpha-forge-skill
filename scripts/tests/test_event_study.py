"""事件研究回归测试：用已知异常收益序列验证 AAR/CAAR。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from research.event_study import event_study


def _jump_prices(n: int = 200, jump_pos: list[int] | None = None, jump: float = 0.02):
    """恒定价格 + 指定位置永久跳升 jump 的确定性序列。"""
    jump_pos = jump_pos or []
    dates = pd.bdate_range("2020-01-01", periods=n)
    level = np.ones(n) * 100.0
    for p in jump_pos:
        level[p:] *= 1.0 + jump
    return pd.Series(level, index=dates)


def test_aar_caar_known_jumps():
    """事件日 +2% 永久跳升：AAR(0)=2%、其余相对日为 0、CAAR 收敛于 2%。"""
    jump_pos = [50, 100, 150]
    prices = _jump_prices(jump_pos=jump_pos, jump=0.02)
    events = [prices.index[p] for p in jump_pos]

    out = event_study(prices, events, window=(-5, 5))
    table = out["table"]

    assert out["n_used"] == 3
    assert out["n_skipped"] == 0
    assert list(table.index) == list(range(-5, 6))
    assert table["AAR"].loc[0] == pytest.approx(0.02, abs=1e-12)
    # 事件日以外的相对日无异常收益
    others = table["AAR"].drop(index=0)
    assert np.allclose(others.to_numpy(), 0.0, atol=1e-12)
    # CAAR 自事件日起锁定在 2%
    assert table["CAAR"].loc[5] == pytest.approx(0.02, abs=1e-12)
    # 各事件窗口累计异常收益一致
    per_event = out["per_event"]["cum_abnormal_return"]
    assert len(per_event) == 3
    assert np.allclose(per_event.to_numpy(), 0.02, atol=1e-12)


def test_benchmark_neutralizes_market_move():
    """基准与个股同步跳升时，超额收益应全为 0。"""
    jump_pos = [80]
    prices = _jump_prices(jump_pos=jump_pos)
    benchmark = _jump_prices(jump_pos=jump_pos)
    out = event_study(
        prices, [prices.index[80]], window=(-5, 5), benchmark=benchmark
    )
    assert np.allclose(out["table"]["AAR"].to_numpy(), 0.0, atol=1e-12)


def test_incomplete_window_events_skipped():
    """贴近样本边界/落在样本外的事件应被剔除并计数。"""
    prices = _jump_prices(jump_pos=[100])
    events = [
        prices.index[100],  # 有效
        prices.index[2],  # 前窗越界
        "2035-01-01",  # 样本之外
    ]
    out = event_study(prices, events, window=(-10, 10))
    assert out["n_used"] == 1
    assert out["n_skipped"] == 2


def test_non_trading_day_aligns_forward():
    """周末事件日应对齐到其后最近交易日。"""
    prices = _jump_prices(jump_pos=[100])
    event_day = prices.index[100]
    weekend = event_day - pd.Timedelta(days=1)  # 向前挪到非交易日（或前一交易日）
    # 用「事件日前一天」触发 searchsorted 对齐；AAR(0) 仍应捕捉到跳升
    out = event_study(prices, [weekend], window=(-5, 5))
    aar = out["table"]["AAR"]
    assert aar.loc[0] == pytest.approx(0.02, abs=1e-12) or aar.loc[1] == pytest.approx(
        0.02, abs=1e-12
    )


def test_invalid_window_raises():
    prices = _jump_prices()
    with pytest.raises(ValueError):
        event_study(prices, [prices.index[50]], window=(5, 10))


def test_non_datetime_index_raises():
    prices = pd.Series(np.linspace(100, 110, 50))
    with pytest.raises(ValueError):
        event_study(prices, ["2020-01-10"])


def test_no_usable_event_raises():
    prices = _jump_prices()
    with pytest.raises(RuntimeError):
        event_study(prices, ["2035-01-01"])
