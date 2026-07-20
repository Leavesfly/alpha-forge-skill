"""机器学习策略回归测试：特征因果性与走步训练形状。

使用 ridge 模型（纯 sklearn），避开 lgbm 对 libomp 的运行库依赖；
全部合成数据，不走网络。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ml.features import build_features, feature_columns
from ml.labeling import meta_labels, triple_barrier_labels
from ml.model import build_target, run_meta_strategy, run_ml_strategy
from tests.helpers import make_ohlcv


def test_build_features_causal(random_walk_df):
    """特征无前视：截断尾部数据不改变历史特征值。"""
    full = build_features(random_walk_df)
    trunc = build_features(random_walk_df.iloc[:-40])
    pd.testing.assert_frame_equal(full.iloc[: len(trunc)], trunc)


def test_build_features_shape(random_walk_df):
    """特征矩阵与输入等长，列名与 feature_columns 一致。"""
    feats = build_features(random_walk_df)
    assert len(feats) == len(random_walk_df)
    assert list(feats.columns) == feature_columns(random_walk_df)
    # warmup 之后不应再有 NaN（最长窗口 60）
    assert not feats.iloc[65:].isna().any().any()


def test_build_target_uses_future_prices(random_walk_df):
    """标签为未来 horizon 期方向：末尾 horizon 行应为 NaN（未实现）。"""
    y = build_target(random_walk_df["close"].astype(float), horizon=5)
    assert y.iloc[-5:].isna().all()
    assert set(y.dropna().unique()).issubset({0.0, 1.0})


def test_ridge_walk_forward_shapes(random_walk_df):
    """ridge 走步：OOS 段起点正确、之前信号为 0、净值覆盖全样本。"""
    result = run_ml_strategy(
        random_walk_df,
        model="ridge",
        horizon=5,
        train_window=120,
        test_window=20,
        warmup=60,
        threshold=0.02,
    )
    # 首个可预测位置 = warmup + train_window + horizon
    assert result.oos_start_pos == 60 + 120 + 5
    signals = result.backtest.signals
    assert len(signals) == len(random_walk_df)
    assert (signals.iloc[: result.oos_start_pos] == 0).all()
    assert result.n_models > 0
    assert result.n_features == len(feature_columns(random_walk_df))
    # 特征重要度覆盖全部特征且非负
    assert len(result.feature_importance) == result.n_features
    assert (result.feature_importance >= 0).all()


def test_ridge_long_only_by_default(random_walk_df):
    """默认不做空：离散信号只应出现 {0, 1}。"""
    result = run_ml_strategy(
        random_walk_df, model="ridge", train_window=120, warmup=60
    )
    assert set(np.unique(result.backtest.signals.to_numpy())).issubset({0.0, 1.0})


def test_insufficient_history_raises(random_walk_df):
    """历史长度不足以支撑一个训练窗时给出清晰报错。"""
    with pytest.raises(RuntimeError):
        run_ml_strategy(random_walk_df, model="ridge", train_window=400)


def test_unknown_model_raises(random_walk_df):
    with pytest.raises(ValueError):
        run_ml_strategy(
            random_walk_df, model="no_such_model", train_window=120, warmup=60
        )


# ------------------------------------------------------------ 三重障碍标注


def test_triple_barrier_hits_profit_first():
    """带噪声大涨行情：波动形成后标签应全为 1（先触止盈）。"""
    rng = np.random.default_rng(6)
    close = pd.Series(100.0 * np.exp(np.cumsum(0.03 + rng.normal(0, 0.003, 80))))
    y = triple_barrier_labels(close, horizon=5, pt_mult=1.0, sl_mult=1.0, vol_window=10)
    realized = y.dropna()
    assert len(realized) > 0
    assert (realized == 1.0).all()


def test_triple_barrier_hits_stop_first():
    """带噪声大跌行情：标签应全为 0（先触止损）。"""
    rng = np.random.default_rng(7)
    close = pd.Series(100.0 * np.exp(np.cumsum(-0.03 + rng.normal(0, 0.003, 80))))
    y = triple_barrier_labels(close, horizon=5, pt_mult=1.0, sl_mult=1.0, vol_window=10)
    realized = y.dropna()
    assert len(realized) > 0
    assert (realized == 0.0).all()


def test_triple_barrier_tail_unrealized_nan():
    """末尾垂直障碍未到期且未触障碍的 bar 应为 NaN；标签仅 {0,1}。"""
    rng = np.random.default_rng(1)
    close = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0, 0.001, 100))))
    y = triple_barrier_labels(close, horizon=10, pt_mult=50.0, sl_mult=50.0, vol_window=10)
    # 障碍极宽→只能靠垂直障碍；最后 10 根未到期应为 NaN
    assert y.iloc[-10:].isna().all()
    assert set(y.dropna().unique()).issubset({0.0, 1.0})


def test_triple_barrier_short_side_mirrors():
    """空头方向：大跌行情对空头是赚钱（标签 1）。"""
    rng = np.random.default_rng(8)
    close = pd.Series(100.0 * np.exp(np.cumsum(-0.03 + rng.normal(0, 0.003, 80))))
    side = -np.ones(80)
    y = triple_barrier_labels(close, horizon=5, pt_mult=1.0, sl_mult=1.0,
                              vol_window=10, side=side)
    realized = y.dropna()
    assert (realized == 1.0).all()


def test_meta_labels_only_on_signal_bars():
    """meta 标签仅在一级信号非零处有值。"""
    rng = np.random.default_rng(4)
    close = pd.Series(100.0 * np.exp(np.cumsum(0.02 + rng.normal(0, 0.005, 80))))
    sig = np.zeros(80)
    sig[20:40] = 1.0
    y = meta_labels(close, sig, horizon=5, vol_window=10)
    assert y.iloc[:20].isna().all()
    assert y.iloc[20:35].notna().all()
    assert y.iloc[45:70].isna().all()


def test_run_ml_strategy_triple_label(random_walk_df):
    """triple 标签模式端到端可跑，形状与 fixed 一致。"""
    result = run_ml_strategy(
        random_walk_df, model="ridge", train_window=120, warmup=60,
        label="triple", pt_mult=2.0, sl_mult=1.0,
    )
    assert result.label_mode == "triple"
    assert result.n_models > 0
    assert len(result.backtest.signals) == len(random_walk_df)


def test_run_ml_strategy_unknown_label_raises(random_walk_df):
    with pytest.raises(ValueError):
        run_ml_strategy(random_walk_df, model="ridge", label="nope",
                        train_window=120, warmup=60)


# ------------------------------------------------------------ meta-labeling


def test_meta_strategy_filters_or_keeps(random_walk_df):
    """meta 流程：过滤后信号是原始信号的子集，OOS 前信号为 0。"""
    from strategies import get_strategy

    base = get_strategy("ma_cross").generate_signals(
        random_walk_df.reset_index(drop=True)
    ).astype(float)
    result = run_meta_strategy(
        random_walk_df, base, model="ridge",
        train_window=120, warmup=60, test_window=20,
    )
    assert result.oos_start_pos == 60 + 120 + 5
    base_sig = result.base_backtest.signals.to_numpy()
    filt_sig = result.filtered_backtest.signals.to_numpy()
    assert (base_sig[: result.oos_start_pos] == 0).all()
    assert (filt_sig[: result.oos_start_pos] == 0).all()
    # 过滤只会把信号置 0，不会无中生有
    nonzero = filt_sig != 0
    assert np.array_equal(filt_sig[nonzero], base_sig[nonzero])
    assert result.n_filtered >= 0
    assert result.n_filtered <= result.n_signals


def test_meta_strategy_insufficient_history_raises():
    """历史不足时报可操作错误。"""
    rng = np.random.default_rng(2)
    df = make_ohlcv(100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, 100))))
    with pytest.raises(RuntimeError):
        run_meta_strategy(df, np.ones(100), model="ridge", train_window=400)

