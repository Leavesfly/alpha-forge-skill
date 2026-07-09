"""机器学习特征工程。

从 OHLCV 构造一组「因果」特征（仅使用当期及历史数据，无前视），
供 LightGBM 预测未来收益方向使用。所有特征在时点 t 都只依赖 <= t 的数据。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder 平滑法 RSI（与策略库口径一致）。"""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi.where(avg_loss != 0, 100.0)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """从 OHLCV 构造特征矩阵。

    Args:
        df: 至少含 ``close`` 列的 DataFrame（时间升序）；``high/low/volume``
            存在时会启用额外特征。

    Returns:
        与 df 等长的特征 DataFrame（早期 warmup 行含 NaN，由调用方统一裁剪）。
    """
    close = df["close"].astype(float)
    feat = pd.DataFrame(index=df.index)

    # 多窗口动量（收益率）
    for w in (1, 5, 10, 20, 60):
        feat[f"roc_{w}"] = close.pct_change(w)

    # 滚动波动率（不同窗口）
    ret1 = close.pct_change()
    for w in (5, 10, 20):
        feat[f"vol_{w}"] = ret1.rolling(w).std()

    # 均线比（价格相对均线的偏离）
    for w in (5, 10, 20, 60):
        ma = close.rolling(w).mean()
        feat[f"ma_ratio_{w}"] = close / ma - 1.0

    # RSI
    for p in (6, 14):
        feat[f"rsi_{p}"] = _rsi(close, p) / 100.0

    # MACD 差值（DIF - DEA），做尺度归一
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    feat["macd_hist"] = (dif - dea) / close

    # 量能变化（若有成交量）
    if "volume" in df.columns:
        vol = df["volume"].astype(float)
        feat["vol_chg_5"] = vol / vol.rolling(5).mean() - 1.0
        feat["vol_chg_20"] = vol / vol.rolling(20).mean() - 1.0

    # 高低波幅（若有高低价）
    if "high" in df.columns and "low" in df.columns:
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        feat["hl_range"] = (high - low) / close
        feat["hl_range_ma5"] = ((high - low) / close).rolling(5).mean()

    return feat


def feature_columns(df: pd.DataFrame) -> list[str]:
    """返回当前数据可用的特征列名列表。"""
    return list(build_features(df).columns)
