# 回测引擎参考

本文档说明 `scripts/backtest/` 中的回测引擎、绩效指标、可视化与参数寻优。

## 架构总览

```
数据 (datafeed.fetch_ohlcv)
   -> 策略 (strategies.*.generate_signals)  产出目标持仓信号
   -> 引擎 (backtest.engine.run_backtest)    信号 shift(1)、扣成本、算净值
   -> 指标 (backtest.metrics.compute_metrics) 绩效指标
   -> 可视化 (backtest.plot.plot_result)     净值/回撤/买卖点出图
   -> 寻优 (backtest.optimize.grid_search)    参数网格搜索排序
```

## 回测引擎（engine.py）

`run_backtest(df, strategy, symbol, period, initial_capital, commission, slippage, risk_free, stop_loss, take_profit)`

核心计算：

```python
signals   = strategy.generate_signals(df)      # {-1, 0, 1}
# 可选：止损/止盈作用于目标持仓时间线
if stop_loss or take_profit:
    signals = _apply_risk_management(signals, close, stop_loss, take_profit)
positions = signals.shift(1).fillna(0.0)        # 次日生效，防前视
price_ret = close.pct_change().fillna(0.0)
turnover  = positions.diff().abs()              # 持仓变动（多空翻转记为 2）
cost      = turnover * (commission + slippage)  # 交易成本
strat_ret = positions * price_ret - cost        # 空头持仓为负，盈亏自动反向
equity    = (1 + strat_ret).cumprod()           # 策略净值
```

- **防前视偏差**：当日产生的信号在下一周期才建仓。
- **交易成本**：按持仓变动比例扣除，`commission` 与 `slippage` 均为单边费率。
- **多空支持**：策略信号可取 `{-1, 0, 1}`，`positions * price_ret` 天然处理做空盈亏（价格下跌时空头盈利）；方向翻转的换手成本按 `|Δpos|` 计入。
- **止损/止盈**：`stop_loss` / `take_profit` 为浮动盈亏比例（如 `0.05`=5%）。以建仓价为基准，触发即离场并保持空仓，直到策略信号归零后才允许重新入场，避免同一段信号内被反复止损又立刻重进。
- **波动率目标仓位**：设置 `vol_target`（年化目标波动率，如 `0.15`）后，仓位 = 信号 × min(目标波动率 / 实现波动率, `max_leverage`)，将离散信号缩放为**连续仓位**。实现波动率由收益率滚动标准差（`vol_window` 窗口）年化得到；`max_leverage` 默认 1.0（不加杠杆）。
- **基准**：同时计算 Buy & Hold 净值用于对比。

返回 `BacktestResult`（dataclass），字段包括 `close`、`signals`、`positions`、
`returns`、`equity`、`benchmark_equity`、`metrics`、`benchmark_metrics`，
并提供 `trades` 属性返回开平仓记录。

## 绩效指标（metrics.py）

`compute_metrics(returns, equity, period, positions, risk_free)` 返回：

| 指标键 | 含义 |
|--------|------|
| `total_return` | 累计收益率 |
| `annual_return` | 年化收益率（按周期年化因子折算） |
| `annual_volatility` | 年化波动率 |
| `sharpe` | 夏普比率 |
| `sortino` | 索提诺比率（仅下行波动） |
| `max_drawdown` | 最大回撤（正值） |
| `calmar` | 卡玛比率 = 年化收益 / 最大回撤 |
| `num_trades` | 交易次数（开仓计数） |
| `win_rate` | 胜率（按持仓区间聚合盈亏） |
| `num_periods` | 回测周期数 |

年化因子按周期取自 `ANNUALIZATION`（如 `1d`=240，`1w`=52，`1M`=12）。
`format_report(metrics, title)` 可将指标格式化为可读文本。

## 可视化（plot.py）

`plot_result(result, strategy_name, output)` 生成三联图并保存 PNG，返回图片路径：

1. 净值曲线：策略 vs 基准（Buy & Hold）
2. 回撤区间：策略净值回撤填充图
3. 价格与买卖点：收盘价叠加买入（红▲）/卖出（绿▼）标记

使用 `matplotlib` 的 `Agg` 后端（无界面环境可用），并预设中文字体避免乱码。

## 参数寻优（optimize.py）

`grid_search(df, strategy_cls, param_grid, metric, top_n, ...)`：

- 遍历参数网格（缺省用策略类的 `param_grid`）的笛卡尔积，逐组回测。
- 自动跳过无意义组合（如 `fast >= slow`）。
- 汇总为 DataFrame，按 `metric`（默认 `sharpe`）降序排序，可取 `top_n`。

支持的排序指标即 `compute_metrics` 返回的任意键，如 `sharpe`、`total_return`、
`annual_return`、`calmar`、`win_rate`。

## CLI 用法

回测：

```bash
cd scripts && uv sync
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross --plot
uv run python run_backtest.py --symbol AAPL.US --strategy macd --count 800 \
    --params fast=10 slow=30
```

参数：`--symbol`、`--strategy`、`--period`（默认 1d）、`--count`（默认 500）、
`--params key=value ...`、`--commission`、`--slippage`、`--allow-short`、
`--stop-loss`、`--take-profit`、`--vol-target`、`--vol-window`、`--max-leverage`、
`--plot`、`--output`。

多空、风控与仓位管理示例：

```bash
# 开启做空（策略在反向条件输出 -1）
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross --allow-short

# 止损 5% + 止盈 15%
uv run python run_backtest.py --symbol 600000.SH --strategy macd --stop-loss 0.05 --take-profit 0.15

# 波动率目标 15%（连续仓位，默认不加杠杆）
uv run python run_backtest.py --symbol 600000.SH --strategy kdj --vol-target 0.15

# 波动率目标 20% + 允许 2 倍杠杆
uv run python run_backtest.py --symbol 600000.SH --strategy kdj --vol-target 0.2 --max-leverage 2.0
```

寻优：

```bash
uv run python run_optimize.py --symbol 600000.SH --strategy ma_cross
uv run python run_optimize.py --symbol AAPL.US --strategy rsi --metric calmar --top 5
```

参数：`--metric`（默认 sharpe）、`--top`（默认 10）、`--allow-short`、
`--stop-loss`、`--take-profit`、`--vol-target`、`--vol-window`、`--max-leverage`，其余与回测一致。

## 仓位模式总结

| 模式 | 仓位取值 | 启用方式 |
|------|----------|----------|
| 满仓多头（默认） | {0, 1} | 无 |
| 多空 | {-1, 0, 1} | 策略 `--allow-short` |
| 连续仓位 | [-max_lev, max_lev] 区间连续值 | `--vol-target` |

## 注意事项与局限

- 引擎支持**满仓（{-1,0,1}）与连续仓位**（波动率目标）两种仓位模式，暂不支持分批建仓与多标的组合（可按需扩展）。
- 止损/止盈基于收盘价逐 bar 判断（非盘中触发），适用于日线及以上周期的近似回测。
- 免费服务仅提供历史日 K；分钟级回测需配置 `TICKFLOW_API_KEY`。
- 绩效指标的年化基于近似交易日数（日线 240），仅供横向比较参考。
- 回测结果不代表未来收益，参数寻优存在过拟合风险，建议用样本外数据验证。
