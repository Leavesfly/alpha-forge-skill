# 配对交易参考

本文档说明 `scripts/pairs/` 的配对交易（市场中性统计套利）：从相关标的构造价差，
用 z-score 均值回复开平仓，复用组合引擎做多空两腿回测。组合与因子策略见
[portfolio.md](portfolio.md)、[multi-factor.md](multi-factor.md)。

## 原理

两只走势高度相关的标的，其对数价差应围绕均值波动。价差偏离过大时反向下注、
回归时平仓，赚取价差收敛的钱。由于同时做多一只、做空另一只，组合近似
**市场中性**（净敞口≈0），波动通常显著低于单标的。

## 架构总览

```
标的/股票池 (datafeed.fetch_prices / fetch_universe)
  -> 配对筛选 (pairs.select)     相关性 + 对冲比率 beta + 半衰期
  -> 价差信号 (pairs.strategy)   spread=logA-beta·logB -> 滚动 z-score 开平仓
  -> 两腿权重 (pairs.pair_weights) 多价差 A:+0.5/B:-0.5，空价差反向
  -> 回测 (portfolio.run_portfolio_backtest)  多空两腿，市场中性
  -> 可视化 (pairs.plot)         z-score/阈值/开平仓点 + 净值
```

## 配对筛选（`pairs/select.py`）

- **相关性**：对数收益两两相关性，筛出 >= `min_corr`（默认 0.7）的候选。
- **对冲比率 beta**：`np.polyfit(log(B), log(A), 1)` 斜率，价差 `spread = log(A) - beta·log(B)`。
- **半衰期**：对价差拟合 AR(1)，半衰期 `-ln(2)/ln(1+rho)`，越短回复越快；按半衰期升序取前 `top_n`。

## 信号与阈值（`pairs/strategy.py`）

对价差做滚动 z-score（窗口 `lookback`，截至当日避免前视）：

| 条件 | 动作 | 持仓 |
|------|------|------|
| z <= -entry | 价差偏低，做多价差（多 A 空 B） | +1 |
| z >= entry | 价差偏高，做空价差（空 A 多 B） | -1 |
| \|z\| <= exit | 均值回复，平仓 | 0 |
| \|z\| >= stop | 反向过大，止损平仓 | 0 |

默认 `entry=2.0`、`exit=0.5`、`stop=3.5`。持仓转两腿权重（多空各 0.5，总杠杆 1），
交由组合引擎 `shift(1)` 次日执行。

## CLI 用法

```bash
cd scripts && uv sync

# 手动指定一对（如两只银行股）
uv run python run_pairs.py --symbols 600000.SH,601398.SH --plot

# 从股票池自动筛选最佳配对
uv run python run_pairs.py --universe CN_Equity_A --limit 40 --top-pairs 3

# 自定义开平仓阈值
uv run python run_pairs.py --symbols 600000.SH,601398.SH --entry 2.5 --exit 0.3 --stop 4.0
```

参数：`--symbols`（手动一对）或 `--universe`+`--limit`+`--top-pairs`（自动筛选）、
`--min-corr`、`--lookback`、`--entry`/`--exit`/`--stop`、`--period`/`--count`、
`--commission`/`--slippage`、`--plot`/`--output`。

输出：配对信息（beta/半衰期/相关性）、市场中性组合绩效、开仓次数与当前持仓。

## 编程方式调用

```python
from datafeed import fetch_prices
from pairs import hedge_ratio, pair_spread, pair_signals, pair_weights
from portfolio import run_portfolio_backtest
import numpy as np

prices = fetch_prices(["600000.SH", "601398.SH"], period="1d", count=400)
a, b = "600000.SH", "601398.SH"
beta = hedge_ratio(np.log(prices[a]), np.log(prices[b]))
spread = pair_spread(prices, a, b, beta)
position = pair_signals(spread, lookback=60, entry=2.0, exit=0.5, stop=3.5)
weights = pair_weights(prices, a, b, position)
result = run_portfolio_backtest(prices, weights)
print(result.metrics)
```

## 注意事项与局限

- **相关性 ≠ 协整**：本实现用相关性 + 半衰期近似筛选，未做严格协整检验（Engle-Granger/ADF），可能存在伪配对，配对关系也可能随时间失效。
- 需足够共同交易日；自动筛选会逐个拉取股票池成分价格，注意接口限流（免费/接口约 10 次/分钟）。
- beta 用全样本估计（静态对冲比率），未滚动更新；可按需改进为滚动 beta。
- 组合引擎的等权基准对市场中性策略参考意义有限，重点看夏普与回撤。
- 回测结果不代表未来收益，请用样本外验证。
