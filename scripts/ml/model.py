"""机器学习策略：技术指标特征 -> LightGBM 预测收益方向 -> 走步样本外信号。

设计要点（对齐项目「回测安全铁律」）：
- 走步（walk-forward）重训练：每个测试块只用其之前、且目标已实现的数据训练，
  严格杜绝前视与未来数据泄露。
- 净值只覆盖样本外（OOS）段：首个预测点之前信号一律置 0。
- 预测概率经「中性带阈值」转为 {-1, 0, 1} 信号，弱信号不入场，抑制过拟合噪声。
- 复用向量化回测引擎（run_backtest）计算净值、成本与绩效，保持与其他模块一致。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

try:
    import lightgbm as lgb
except ImportError as exc:  # pragma: no cover - 依赖缺失时给出清晰指引
    raise ImportError(
        "机器学习策略需要 lightgbm，请在 scripts/ 下执行 `uv sync` 安装依赖。"
    ) from exc
except OSError as exc:  # pragma: no cover - macOS 缺 OpenMP 运行库
    raise OSError(
        "LightGBM 加载失败（通常是 macOS 缺少 OpenMP 运行库 libomp）。\n"
        "请安装：brew install libomp（macOS）。详见 references/ml-strategy.md。"
    ) from exc

from backtest.engine import BacktestResult, run_backtest
from strategies.base import Strategy

from .features import build_features


class _PrecomputedSignalStrategy(Strategy):
    """承载预先算好的信号，供回测引擎复用。

    引擎会对 df 做 reset_index，再按位置调用 generate_signals，因此这里按
    位置返回信号数组即可（与原始 df 顺序一致）。
    """

    name = "ml"
    display_name = "机器学习"

    def __init__(self, signals: np.ndarray, **params):
        super().__init__(**params)
        self._signals = np.asarray(signals, dtype=float)

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        n = len(df)
        sig = self._signals
        if len(sig) != n:  # 稳健对齐（理论上等长）
            sig = np.resize(sig, n)
        return pd.Series(sig, index=df.index)


@dataclass
class MLResult:
    """机器学习策略结果容器。"""

    backtest: BacktestResult
    feature_importance: pd.Series
    oos_start_pos: int
    oos_start_label: object = None
    n_models: int = 0
    n_features: int = 0
    horizon: int = 5
    train_window: int = 250
    test_window: int = 20
    threshold: float = 0.05


def build_target(close: pd.Series, horizon: int) -> pd.Series:
    """未来 horizon 期收益方向（1=上涨，0=下跌/持平）作为二分类标签。

    标签使用未来价格，仅用于「训练」；走步逻辑保证训练标签在测试期开始前
    已完全实现，不会泄露到样本外净值。
    """
    fwd_ret = close.shift(-horizon) / close - 1.0
    return (fwd_ret > 0).astype(float).where(fwd_ret.notna())


#: 小容量 LightGBM 原生训练参数（降低过拟合；不依赖 scikit-learn）
_LGB_PARAMS = {
    "objective": "binary",
    "num_leaves": 15,
    "max_depth": 4,
    "learning_rate": 0.05,
    "min_data_in_leaf": 30,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "seed": 42,
    "verbose": -1,
}


def run_ml_strategy(
    df: pd.DataFrame,
    symbol: str = "",
    period: str = "1d",
    horizon: int = 5,
    train_window: int = 250,
    test_window: int = 20,
    threshold: float = 0.05,
    allow_short: bool = False,
    commission: float = 0.0005,
    slippage: float = 0.0005,
    warmup: int = 60,
) -> MLResult:
    """训练并回测机器学习方向预测策略。

    Args:
        df: OHLCV DataFrame（时间升序，至少含 close）。
        horizon: 预测的未来收益周期数（也是标签的前瞻步长）。
        train_window: 每次走步训练使用的样本数（滚动窗口）。
        test_window: 每次走步向前预测的周期数。
        threshold: 中性带宽度；proba_up 需偏离 0.5 超过该值才入场。
        allow_short: 是否允许做空（预测下跌时输出 -1）。
        warmup: 特征形成所需的最小预热周期（该段不参与训练/预测）。

    Returns:
        MLResult。回测净值仅覆盖样本外（OOS）段。
    """
    df = df.reset_index(drop=True)
    close = df["close"].astype(float)
    n = len(df)

    feats = build_features(df)
    cols = list(feats.columns)
    X = feats.to_numpy(dtype=float)
    y = build_target(close, horizon).to_numpy(dtype=float)

    valid_feat = ~np.isnan(X).any(axis=1)
    valid_y = ~np.isnan(y)

    # 首个可预测位置：预热 + 一个完整训练窗 + 目标实现所需的 horizon 滞后
    first_test = warmup + train_window + horizon
    if first_test >= n:
        raise RuntimeError(
            f"历史长度不足：需要至少 {first_test + test_window} 根 K 线"
            f"（warmup={warmup} + train_window={train_window} + horizon={horizon}），"
            f"当前仅 {n} 根。请增大 --count 或减小 --train-window。"
        )

    signals = np.zeros(n, dtype=float)
    importance_acc = np.zeros(len(cols), dtype=float)
    n_models = 0

    for test_start in range(first_test, n, test_window):
        test_end = min(test_start + test_window, n)

        # 训练区：测试期开始前、目标已实现（i + horizon < test_start）的滚动窗口
        train_hi = test_start - horizon  # 不含
        train_lo = max(warmup, train_hi - train_window)
        if train_hi - train_lo < 30:  # 训练样本过少则跳过（信号保持 0）
            continue

        tr_mask = np.zeros(n, dtype=bool)
        tr_mask[train_lo:train_hi] = True
        tr_mask &= valid_feat & valid_y

        y_tr = y[tr_mask]
        if len(y_tr) < 30 or len(np.unique(y_tr)) < 2:
            continue  # 样本不足或单一类别，无法稳定训练

        booster = lgb.train(
            _LGB_PARAMS,
            lgb.Dataset(X[tr_mask], label=y_tr),
            num_boost_round=200,
        )
        importance_acc += np.asarray(
            booster.feature_importance(importance_type="gain"), dtype=float
        )
        n_models += 1

        # 预测测试块（跳过特征缺失行）
        te_idx = np.arange(test_start, test_end)
        te_valid = te_idx[valid_feat[te_idx]]
        if len(te_valid) == 0:
            continue
        # 原生 API 二分类直接返回 P(y=1)
        proba_up = np.asarray(booster.predict(X[te_valid]), dtype=float)

        block_sig = np.where(
            proba_up > 0.5 + threshold, 1.0,
            np.where(proba_up < 0.5 - threshold, -1.0 if allow_short else 0.0, 0.0),
        )
        signals[te_valid] = block_sig

    if n_models == 0:
        raise RuntimeError(
            "走步训练未产出任何有效模型，请检查数据充足性与参数设置。"
        )

    strategy = _PrecomputedSignalStrategy(signals, allow_short=allow_short)
    backtest = run_backtest(
        df, strategy, symbol=symbol, period=period,
        commission=commission, slippage=slippage,
    )

    importance = pd.Series(
        importance_acc / n_models, index=cols
    ).sort_values(ascending=False)

    oos_label = None
    idx = backtest.equity.index
    if first_test < len(idx):
        oos_label = idx[first_test]

    return MLResult(
        backtest=backtest,
        feature_importance=importance,
        oos_start_pos=first_test,
        oos_start_label=oos_label,
        n_models=n_models,
        n_features=len(cols),
        horizon=horizon,
        train_window=train_window,
        test_window=test_window,
        threshold=threshold,
    )
