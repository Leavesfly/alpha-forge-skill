"""压力测试：历史情景重放 + 蒙特卡洛冲击。

回答两个问题：
1. 策略在历史极端行情窗口（股灾、熊市、流动性危机）中表现如何？
   —— 若回测区间覆盖预置情景，报告该窗口的收益/最大回撤/恢复天数。
2. 如果未来出现「单日暴跌」或「波动翻倍」，最大回撤会恶化到什么程度？
   —— 对策略收益序列做冲击注入与自助抽样（bootstrap），输出回撤分布分位数。

注意：压力测试基于策略历史收益序列，隐含「策略行为模式不变」假设，
结果用于风险预算参考，不构成对未来极端损失的上界承诺。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.metrics import max_drawdown

#: 预置历史极端情景（A 股 / 全球市场通用参考窗口）
HISTORICAL_SCENARIOS = [
    ("2015 股灾", "2015-06-12", "2015-08-26"),
    ("2016 熔断", "2016-01-01", "2016-01-31"),
    ("2018 熊市", "2018-01-24", "2018-12-28"),
    ("2020-03 流动性危机", "2020-02-20", "2020-03-23"),
    ("2022 加息熊市", "2022-01-01", "2022-10-31"),
    ("2024-02 微盘股踩踏", "2024-01-15", "2024-02-08"),
]


def historical_scenarios(
    returns: pd.Series,
    scenarios: list[tuple[str, str, str]] | None = None,
) -> pd.DataFrame:
    """历史情景重放：统计各预置窗口内的策略表现。

    Args:
        returns: 策略逐周期收益（DatetimeIndex；非时间索引时返回空表）。
        scenarios: 自定义情景列表 [(名称, 起, 止)]；缺省用内置。

    Returns:
        DataFrame（情景/区间/期间收益/最大回撤/恢复天数），
        仅包含与回测区间有重叠的情景；恢复天数为 NaN 表示区间后未收复。
    """
    if not isinstance(returns.index, pd.DatetimeIndex):
        idx = pd.to_datetime(returns.index, errors="coerce")
        if idx.isna().all():
            return pd.DataFrame()
        returns = pd.Series(returns.values, index=idx)

    rows = []
    for name, start, end in scenarios or HISTORICAL_SCENARIOS:
        window = returns.loc[start:end]
        if len(window) < 5:  # 覆盖太少视为未经历该情景
            continue
        eq = (1.0 + window).cumprod()
        mdd = max_drawdown(eq)
        # 恢复天数：情景后净值收复情景前高点所需的交易日数
        after = returns.loc[window.index[-1] :].iloc[1:]
        recover = np.nan
        if len(after):
            eq_after = float(eq.iloc[-1]) * (1.0 + after).cumprod()
            peak = float(eq.cummax().iloc[-1])
            hit = np.where(eq_after.to_numpy() >= peak)[0]
            if len(hit):
                recover = int(hit[0]) + 1
        rows.append(
            {
                "情景": name,
                "区间": f"{str(window.index[0])[:10]}~{str(window.index[-1])[:10]}",
                "期间收益": float(eq.iloc[-1] - 1.0),
                "最大回撤": mdd,
                "恢复天数": recover,
            }
        )
    return pd.DataFrame(rows)


def monte_carlo_stress(
    returns: pd.Series,
    n_sims: int = 1000,
    shocks: tuple[float, ...] = (-0.05, -0.10),
    vol_scale: float = 2.0,
    seed: int = 42,
) -> dict:
    """蒙特卡洛冲击测试。

    对策略收益做三类扰动，各 ``n_sims`` 次自助抽样后统计最大回撤分布：
    - bootstrap：有放回重抽样（基线，路径风险）；
    - shock：随机位置注入单日冲击（如 -5%/-10%）；
    - vol x N：收益围绕均值放大 ``vol_scale`` 倍（波动政权切换）。

    Args:
        returns: 策略逐周期收益序列。
        n_sims: 每类情景的模拟次数。
        shocks: 注入的单日冲击幅度列表（负数）。
        vol_scale: 波动放大倍数。
        seed: 随机种子（结果可复现）。

    Returns:
        {情景名: {"p50": ..., "p95": ..., "p99": ...}}，值为最大回撤（正数）。
    """
    r = returns.dropna().to_numpy(dtype=float)
    if len(r) < 20:
        return {}
    rng = np.random.default_rng(seed)
    n = len(r)

    def _mdd_dist(sample_fn) -> dict:
        mdds = np.empty(n_sims)
        for i in range(n_sims):
            path = sample_fn()
            eq = np.cumprod(1.0 + path)
            peak = np.maximum.accumulate(eq)
            mdds[i] = float(-((eq / peak) - 1.0).min())
        return {
            "p50": float(np.percentile(mdds, 50)),
            "p95": float(np.percentile(mdds, 95)),
            "p99": float(np.percentile(mdds, 99)),
        }

    out = {"bootstrap 基线": _mdd_dist(lambda: rng.choice(r, size=n, replace=True))}

    for shock in shocks:
        def _with_shock(shock=shock):
            path = rng.choice(r, size=n, replace=True)
            path[rng.integers(0, n)] += shock
            return path

        out[f"单日冲击 {shock:.0%}"] = _mdd_dist(_with_shock)

    mean = r.mean()

    def _vol_scaled():
        path = rng.choice(r, size=n, replace=True)
        return mean + (path - mean) * vol_scale

    out[f"波动 x{vol_scale:g}"] = _mdd_dist(_vol_scaled)
    return out


def stress_tables(
    returns: pd.Series, n_sims: int = 1000, seed: int = 42
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """一次性生成压力测试的两张结果表（供 CLI/报告渲染）。

    Returns:
        (历史情景表, 蒙特卡洛回撤分位表)；不适用时为空表。
    """
    scen = historical_scenarios(returns)
    mc = monte_carlo_stress(returns, n_sims=n_sims, seed=seed)
    mc_df = pd.DataFrame(
        [
            {"情景": name, "回撤p50": v["p50"], "回撤p95": v["p95"], "回撤p99": v["p99"]}
            for name, v in mc.items()
        ]
    )
    return scen, mc_df
