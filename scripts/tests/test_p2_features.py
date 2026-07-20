"""P2 阶段回归测试：信号服务、ML 可插拔模型、事件研究。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ml.model import MODELS, _proba_to_signal, _train_predict
from research.event_study import event_study
from run_signal import _action, latest_signal
from strategies import get_strategy
from tests.helpers import make_ohlcv


# ---------- 信号服务 ----------

def test_latest_signal_fields():
    close = 100.0 * (1.0 + 0.01) ** np.arange(120)
    df = make_ohlcv(close)
    strat = get_strategy("ma_cross", fast=5, slow=20)
    sig = latest_signal(df, strat)
    assert set(sig) == {"date", "close", "current_position", "target_position", "action"}
    assert sig["target_position"] == 1.0  # 单边上行应满仓
    assert sig["action"] in ("持有", "买入/加仓")


def test_action_mapping():
    assert _action(0.0, 1.0) == "买入/加仓"
    assert _action(1.0, 0.0) == "卖出/减仓"
    assert _action(1.0, 1.0) == "持有"
    assert _action(0.0, 0.0) == "观望"


# ---------- ML 可插拔模型 ----------

@pytest.fixture
def xy():
    rng = np.random.default_rng(5)
    X = rng.normal(size=(200, 6))
    # 线性可分目标 + 噪声
    y = (X[:, 0] + 0.5 * X[:, 1] + rng.normal(scale=0.5, size=200) > 0).astype(float)
    return X, y


@pytest.mark.parametrize("model", list(MODELS))
def test_train_predict_all_models(model, xy):
    if model == "lgbm":
        try:
            import lightgbm  # noqa: F401
        except Exception:
            pytest.skip("lightgbm 不可用（如 macOS 缺 libomp），跳过 lgbm 用例")
    X, y = xy
    proba, imp = _train_predict(model, X[:150], y[:150], X[150:])
    assert proba.shape == (50,)
    assert np.all((proba >= 0.0) & (proba <= 1.0))
    assert imp.shape == (6,)
    if model in ("ridge", "logistic"):
        # 线性模型应识别出前两个特征最重要
        assert np.argmax(imp) in (0, 1)


def test_unknown_model_raises(xy):
    X, y = xy
    with pytest.raises(ValueError, match="未知模型"):
        _train_predict("transformer", X, y, X)


def test_proba_to_signal_discrete():
    proba = np.array([0.40, 0.50, 0.56, 0.70])
    sig = _proba_to_signal(proba, threshold=0.05, allow_short=False, prob_sizing=False)
    assert list(sig) == [0.0, 0.0, 1.0, 1.0]
    sig_short = _proba_to_signal(proba, threshold=0.05, allow_short=True, prob_sizing=False)
    assert sig_short[0] == -1.0


def test_proba_to_signal_sizing():
    proba = np.array([0.55, 0.775, 1.0, 0.40])
    sig = _proba_to_signal(proba, threshold=0.05, allow_short=False, prob_sizing=True)
    assert sig[0] == pytest.approx(0.0)      # 刚到起步线
    assert sig[1] == pytest.approx(0.5)      # 中点半仓
    assert sig[2] == pytest.approx(1.0)      # 满置信满仓
    assert sig[3] == pytest.approx(0.0)      # 未开做空则不出负仓


# ---------- 事件研究 ----------

@pytest.fixture
def event_prices():
    idx = pd.bdate_range("2024-01-01", periods=250)
    rng = np.random.default_rng(9)
    ret = rng.normal(0.0, 0.005, 250)
    # 在两个事件日后注入连续正收益
    for d in ("2024-05-06", "2024-08-05"):
        pos = idx.searchsorted(pd.Timestamp(d))
        ret[pos : pos + 5] += 0.01
    return pd.Series(100.0 * np.cumprod(1.0 + ret), index=idx)


def test_event_study_positive_caar(event_prices):
    out = event_study(
        event_prices, ["2024-05-06", "2024-08-05"], window=(-5, 10)
    )
    table = out["table"]
    assert out["n_used"] == 2
    assert list(table.index) == list(range(-5, 11))
    # 事件后注入正收益，CAAR 终值应为正
    assert float(table["CAAR"].iloc[-1]) > 0.02


def test_event_study_skips_out_of_range(event_prices):
    out = event_study(
        event_prices, ["2024-05-06", "2030-01-01"], window=(-5, 10)
    )
    assert out["n_used"] == 1
    assert out["n_skipped"] == 1


def test_event_study_benchmark_neutralizes(event_prices):
    """基准与标的相同，超额收益应几乎为 0。"""
    out = event_study(
        event_prices, ["2024-05-06"], window=(-5, 10), benchmark=event_prices
    )
    assert abs(float(out["table"]["CAAR"].iloc[-1])) < 1e-12


def test_event_study_invalid_window(event_prices):
    with pytest.raises(ValueError):
        event_study(event_prices, ["2024-05-06"], window=(5, 10))
