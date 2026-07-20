# 多标的组合回测参考

本文档说明 `scripts/portfolio/` 中的多标的组合回测引擎与截面轮动策略。
单标的策略与回测见 [strategies.md](strategies.md) 与 [backtesting.md](backtesting.md)。

## 架构总览

```
多标的数据 (datafeed.fetch_prices)       收盘价矩阵（日期 × 标的）
   -> 轮动 (portfolio.rotation.get_weights)  产出目标权重矩阵
   -> 引擎 (portfolio.run_portfolio_backtest) 权重 shift(1)、扣换手成本、算净值
   -> 指标 (backtest.metrics.compute_metrics) 复用单标的绩效指标
   -> 可视化 (portfolio.plot.plot_portfolio)  净值/回撤/权重堆叠图
```

组合模块复用单标的的绩效指标（`backtest.metrics`），与单标的链路解耦、互不影响。

## 数据获取（datafeed.fetch_prices）

`fetch_prices(symbols, period, count)` 拉取多标的收盘价，按**共同交易日内连接对齐**，
返回 `DataFrame`（索引为日期，列为标的代码）。对齐后不足 2 个标的会报错。

## 组合回测引擎（engine.py）

`run_portfolio_backtest(prices, target_weights, period, commission, slippage, risk_free)`

核心计算：

```python
returns = prices.pct_change()
held    = target_weights.shift(1)               # 权重次日生效，防前视
port_ret = (held * returns).sum(axis=1)          # 组合收益
turnover = held.diff().abs().sum(axis=1)         # 换手（各标的权重变动绝对值之和）
port_ret -= turnover * (commission + slippage)   # 扣换手成本
equity   = (1 + port_ret).cumprod()
```

- **防前视**：目标权重 `shift(1)` 后生效。
- **换手成本**：按权重变动总额扣除；非调仓日权重前向填充保持不变，故不产生额外换手。
- **基准**：等权（每日再平衡）组合 `returns.mean(axis=1)`。
- 返回 `PortfolioResult`，含 `equity`、`weights`（实际持仓权重）、`metrics`、`benchmark_metrics`、`rebalance_count`。

> 说明：采用「固定目标权重 + 每日隐含再平衡」的简化模型，未建模日内权重漂移，适用于教学与相对比较。

## 轮动策略（rotation.py）

所有策略接收收盘价矩阵，按 `rebalance` 周期在调仓日计算目标权重，非调仓日前向填充。
通过 `ROTATIONS` 注册表与 `get_weights(name, prices, **params)` 调用。

| 策略名 | 说明 | 关键参数 |
|--------|------|----------|
| `momentum` | 截面动量轮动：持有过去 `lookback` 期涨幅最高的 `top_k` 只，等权（默认仅动量为正才持有） | `lookback`/`top_k`/`rebalance` |
| `equal_weight` | 等权组合，所有标的 1/N | `rebalance` |
| `inverse_vol` | 风险平价（逆波动率）：权重与各标的波动率成反比 | `lookback`/`rebalance` |
| `min_variance` | 最小方差组合：解析解 w ∝ Σ⁻¹·1 | `lookback`/`rebalance` |
| `max_sharpe` | 最大夏普组合：解析解 w ∝ Σ⁻¹·(μ-rf) | `lookback`/`rebalance` |
| `hrp` | HRP 层次风险平价：相关距离聚类 + 递归二分配权，不求逆协方差，对估计误差更稳健 | `lookback`/`rebalance` |
| `min_cvar` | 最小 CVaR：SLSQP 最小化历史尾部均值亏损（尾部风险最小） | `lookback`/`rebalance`/`cvar_alpha` |

### 组合优化（optimize.py）

`min_variance`/`max_sharpe` 在每个调仓日用过去 `lookback` 窗口的收益估计协方差/均值，求解权重（`np.linalg.pinv` 解析解）：

- 最小方差：`w = Σ⁻¹·1 / (1ᵀΣ⁻¹·1)`，波动最小。
- 最大夏普：`w ∝ Σ⁻¹·(μ-rf)`，风险调整后收益最优。
- HRP（`hrp`）：相关距离 `√(0.5(1-ρ))` 做层次聚类，准对角化后递归二分、
  按簇内逆方差分配风险（López de Prado, 2016）；不求逆协方差矩阵，
  对估计误差比解析解更稳健，标的多/相关性高时优先选它。
- 最小 CVaR（`min_cvar`）：直接最小化历史收益尾部（`1-cvar_alpha` 分位以下）
  的均值亏损（SLSQP，失败退化等权）；关注崩盘风险而非日常波动时使用。
- **仅做多近似**：负权重截断为 0 后归一化（非严格二次规划）；样本协方差在标的多/样本少时不稳，建议控制标的数与足够 `lookback`。

## CLI 用法

```bash
cd scripts && uv sync

# 截面动量轮动（持有涨幅前 2 名）
uv run python run_portfolio.py --symbols 600000.SH,000001.SZ,600519.SH,000858.SZ \
    --strategy momentum --lookback 60 --top-k 2 --rebalance 20

# 风险平价 + 出图
uv run python run_portfolio.py --symbols AAPL.US,MSFT.US,TSLA.US --strategy inverse_vol --plot

# 等权基准组合
uv run python run_portfolio.py --symbols 600519.SH,000858.SZ,600809.SH --strategy equal_weight

# 最小方差 / 最大夏普 / HRP / 最小 CVaR 组合优化
uv run python run_portfolio.py --symbols AAPL.US,MSFT.US,TSLA.US,AMZN.US,GOOGL.US --strategy min_variance
uv run python run_portfolio.py --symbols AAPL.US,MSFT.US,TSLA.US,AMZN.US,GOOGL.US --strategy max_sharpe
uv run python run_portfolio.py --symbols AAPL.US,MSFT.US,TSLA.US,AMZN.US --strategy hrp
uv run python run_portfolio.py --symbols 600000.SH,600519.SH,000858.SZ --strategy min_cvar --cvar-alpha 0.95
```

参数：`--symbols`（逗号分隔，至少 2 个）、`--strategy`（momentum/equal_weight/inverse_vol/min_variance/max_sharpe/hrp/min_cvar）、
`--period`、`--count`、`--lookback`、`--top-k`、`--rebalance`、`--cvar-alpha`（min_cvar 置信水平，默认 0.95）、`--commission`、`--slippage`、
`--plot`、`--output`。

输出组合与等权基准的绩效对比（累计/年化收益、夏普、最大回撤、卡玛等）及调仓次数。

## 编程方式调用

```python
from datafeed import fetch_prices
from portfolio import get_weights, run_portfolio_backtest

prices = fetch_prices(["600000.SH", "000001.SZ", "600519.SH"], period="1d", count=400)
weights = get_weights("momentum", prices, lookback=60, top_k=2, rebalance=20)
result = run_portfolio_backtest(prices, weights)
print(result.metrics)
```

## 注意事项与局限

- 组合为**多头、权重之和 ≤ 1** 的现金 + 持仓模型，暂不支持组合层面做空与杠杆。
- 采用固定目标权重的简化再平衡，未建模日内漂移与冲击成本。
- 免费服务仅提供历史日 K；多标的按共同交易日对齐，停牌/上市时间不一致会缩短可回测区间。
- 回测结果不代表未来收益，请用样本外数据验证。
