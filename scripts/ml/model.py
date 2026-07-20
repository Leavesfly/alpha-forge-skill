"""机器学习策略：技术指标特征 -> 方向预测（可插拔模型）-> 走步样本外信号。

设计要点（对齐项目「回测安全铁律」）：
- 走步（walk-forward）重训练：每个测试块只用其之前、且目标已实现的数据训练，
  严格杜绝前视与未来数据泄露。
- 净值只覆盖样本外（OOS）段：首个预测点之前信号一律置 0。
- 可插拔模型：lgbm（默认）/ ridge / logistic。线性基线很重要——
  LightGBM 若跑不赢 Ridge，本身就是过拟合警报。
- 预测概率经「中性带阈值」转为 {-1, 0, 1} 信号；或开启 prob_sizing
  按置信度线性映射为连续仓位（弱信号轻仓、强信号重仓）。
- 复用向量化回测引擎（run_backtest）计算净值、成本与绩效，保持与其他模块一致。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtest.engine import BacktestResult, run_backtest
from strategies.base import Strategy

from .features import build_features

#: 支持的模型名
MODELS = ("lgbm", "ridge", "logistic")


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
    model_name: str = "lgbm"


def build_target(close: pd.Series, horizon: int) -> pd.Series:
    """未来 horizon 期收益方向（1=上涨，0=下跌/持平）作为二分类标签。

    标签使用未来价格，仅用于「训练」；走步逻辑保证训练标签在测试期开始前
    已完全实现，不会泄露到样本外净值。
    """
    fwd_ret = close.shift(-horizon) / close - 1.0
    return (fwd_ret > 0).astype(float).where(fwd_ret.notna())


#: 小容量 LightGBM 原生训练参数（降低过拟合）
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


def _import_lightgbm():
    """懒加载 lightgbm，缺失/加载失败时给出清晰指引。"""
    try:
        import lightgbm as lgb
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "机器学习策略（lgbm）需要 lightgbm，请在 scripts/ 下执行 `uv sync` 安装依赖；"
            "或改用 --model ridge/logistic。"
        ) from exc
    except OSError as exc:  # pragma: no cover - macOS 缺 OpenMP 运行库
        raise OSError(
            "LightGBM 加载失败（通常是 macOS 缺少 OpenMP 运行库 libomp）。\n"
            "请安装：brew install libomp；或改用 --model ridge/logistic。"
        ) from exc
    return lgb


def _train_predict(
    model_name: str, X_tr: np.ndarray, y_tr: np.ndarray, X_te: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """训练单个走步模型并预测测试块。

    Args:
        model_name: lgbm / ridge / logistic。
        X_tr, y_tr: 训练特征与二分类标签。
        X_te: 测试特征。

    Returns:
        (proba_up, importance)：上涨概率与特征重要度（线性模型取 |coef|）。
    """
    if model_name == "lgbm":
        lgb = _import_lightgbm()
        booster = lgb.train(
            _LGB_PARAMS, lgb.Dataset(X_tr, label=y_tr), num_boost_round=200
        )
        proba = np.asarray(booster.predict(X_te), dtype=float)
        imp = np.asarray(
            booster.feature_importance(importance_type="gain"), dtype=float
        )
        return proba, imp

    from sklearn.linear_model import LogisticRegression, RidgeClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if model_name == "ridge":
        clf = make_pipeline(StandardScaler(), RidgeClassifier(alpha=1.0))
        clf.fit(X_tr, y_tr)
        # RidgeClassifier 无 predict_proba，用决策函数过 sigmoid 近似概率
        d = np.asarray(clf.decision_function(X_te), dtype=float)
        proba = 1.0 / (1.0 + np.exp(-d))
        imp = np.abs(clf[-1].coef_).ravel()
        return proba, imp

    if model_name == "logistic":
        clf = make_pipeline(
            StandardScaler(), LogisticRegression(max_iter=1000, C=1.0)
        )
        clf.fit(X_tr, y_tr)
        proba = np.asarray(clf.predict_proba(X_te)[:, 1], dtype=float)
        imp = np.abs(clf[-1].coef_).ravel()
        return proba, imp

    raise ValueError(f"未知模型 '{model_name}'，可选：{MODELS}")


def _proba_to_signal(
    proba_up: np.ndarray, threshold: float, allow_short: bool, prob_sizing: bool
) -> np.ndarray:
    """预测概率 -> 目标仓位。

    离散模式：超过中性带即满仓 {-1,0,1}；
    prob_sizing：从 0.5+threshold 起步线性放大至 1.0（置信度越高仓位越重）。
    """
    if not prob_sizing:
        return np.where(
            proba_up > 0.5 + threshold, 1.0,
            np.where(proba_up < 0.5 - threshold, -1.0 if allow_short else 0.0, 0.0),
        )
    span = max(0.5 - threshold, 1e-9)
    long_size = np.clip((proba_up - (0.5 + threshold)) / span, 0.0, 1.0)
    short_size = np.clip(((0.5 - threshold) - proba_up) / span, 0.0, 1.0)
    return long_size - (short_size if allow_short else 0.0)


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
    model: str = "lgbm",
    prob_sizing: bool = False,
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
        model: 模型名（lgbm / ridge / logistic）。
        prob_sizing: True 时按预测置信度线性映射为连续仓位。

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

        # 预测测试块（跳过特征缺失行）
        te_idx = np.arange(test_start, test_end)
        te_valid = te_idx[valid_feat[te_idx]]
        if len(te_valid) == 0:
            continue

        proba_up, imp = _train_predict(model, X[tr_mask], y_tr, X[te_valid])
        importance_acc += imp
        n_models += 1

        signals[te_valid] = _proba_to_signal(
            proba_up, threshold, allow_short, prob_sizing
        )

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
        model_name=model,
    )
