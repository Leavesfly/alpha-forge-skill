"""策略共享技术指标工具。

提取各策略文件中重复出现的指标计算逻辑，消除 DRY 违反：
- ``compute_atr``：平均真实波幅（ATR），turtle / keltner / supertrend 共用；
- ``extract_ohlcv``：OHLCV 列安全提取，缺失列退化为 close，8+ 个策略共用。

所有函数遵循引擎 shift(1) 防前视约定：指标默认使用历史窗口（不含当前 bar），
策略内部可直接使用返回值而无需额外 shift。
"""

from __future__ import annotations

import pandas as pd


def extract_ohlcv(
    df: pd.DataFrame,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """从 OHLCV DataFrame 安全提取 open/high/low/close 四列。

    当数据缺少 open/high/low 列时（如仅有 close 的简化数据源），
    自动退化为 close 序列，保证策略逻辑不因列缺失而崩溃。

    Args:
        df: 至少包含 ``close`` 列的 DataFrame。

    Returns:
        (open, high, low, close) 四元组，每列均为 float 类型的 Series。
    """
    close = df["close"].astype(float)
    open_ = df["open"].astype(float) if "open" in df.columns else close
    high = df["high"].astype(float) if "high" in df.columns else close
    low = df["low"].astype(float) if "low" in df.columns else close
    return open_, high, low, close


def compute_atr(df: pd.DataFrame, window: int, shift: int = 1) -> pd.Series:
    """计算平均真实波幅（Average True Range, ATR）。

    真实波幅 TR = max(H-L, |H-prevC|, |L-prevC|)，
    ATR 为 TR 的简单移动平均，默认 shift(1) 使用历史窗口防止前视偏差。

    Args:
        df: 至少包含 ``close`` 列的 DataFrame；有 high/low 时计算更精确，
            缺失时退化为 close（此时 TR ≡ |close - prev_close|）。
        window: 移动平均窗口（周期数），建议 >= 2。
        shift: 前移 period 数，默认 1（不含当前 bar，防前视）；
            设为 0 则包含当前 bar（用于需要实时 ATR 的场景）。

    Returns:
        ATR 序列（与 df 等长），初始窗口期为 NaN。
    """
    close = df["close"].astype(float)
    high = df["high"].astype(float) if "high" in df.columns else close
    low = df["low"].astype(float) if "low" in df.columns else close

    prev_close = close.shift(1)
    # 真实波幅：当前高低价差、跳空高开幅度、跳空低开幅度三者取大
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    atr = tr.rolling(window).mean()
    if shift > 0:
        atr = atr.shift(shift)
    return atr
