# 回测引擎参考

本文档说明 `scripts/backtest/` 中的回测引擎、绩效指标、可视化与参数寻优，
以及 `scripts/research/` 的稳健性验证（走步样本外 + PBO）与事件研究。

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

## 账本引擎（ledger.py）

`run_backtest_ledger(...)` 提供更高保真度的回测通道（CLI：`--engine ledger`）：

- **真实账本**：以「现金余额 + 持股数」逐日演进，而非收益率相乘；
- **整数股/一手约束**：`--lot-size`（`--market astock` 时默认 100），小资金可能买不足一手（会告警）；
- **初始资金真实生效**：`--capital`（默认 100 万）影响可建仓数量与取整误差；
- 信号管线（防前视、止损止盈、波动率目标、涨跌停/停牌、成交价约定）与向量化引擎一致；
  无摩擦时两引擎净值近似一致（回归测试保证）。

```bash
# A 股一手 100 股 + 真实成本 + 10 万本金的账本回测
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross \
    --engine ledger --market astock --capital 100000
```

已知局限：分红除权现金流未建模（依赖复权价格近似）；未建模 T+1 可用资金与保证金。

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

`grid_search(df, strategy_cls, param_grid, metric, top_n, n_jobs, method, n_iter, seed, ...)`：

- 遍历参数网格（缺省用策略类的 `param_grid`）的笛卡尔积，逐组回测。
- 自动跳过无意义组合（如 `fast >= slow`）。
- 汇总为 DataFrame，按 `metric`（默认 `sharpe`）降序排序，可取 `top_n`。
- **多进程并行**：`n_jobs>1`（CLI `--jobs`，默认 CPU 核数）且有效组合数 >= 8 时
  启用 `ProcessPoolExecutor`，结果与串行逐行一致；组合数少时自动保持串行避免进程开销。
- **随机搜索**：`method="random"`（CLI `--method random`）从全网格中无放回随机采样
  `n_iter` 组（CLI `--n-iter`，默认 60），`seed`（CLI `--seed`，默认 42）保证可复现；
  组合数不超过 `n_iter` 时退化为全网格。试验次数更少 → 多重检验惩罚（DSR）更轻、
  过拟合风险更低，大网格下建议优先随机搜索。
- **贝叶斯搜索**：`method="bayes"`（CLI `--method bayes`）在离散网格上做 TPE 风格
  自适应搜索：把已评估组合按指标分为好/坏两组，按参数取值的似然比挑下一批
  最有希望的候选（零新依赖，固定 seed 可复现，串行/并行结果一致）；同预算下
  通常找到比随机更优的参数，DSR 惩罚同样按实际评估次数计算。

支持的排序指标即 `compute_metrics` 返回的任意键，如 `sharpe`、`total_return`、
`annual_return`、`calmar`、`win_rate`。

## 多策略对比（run_compare.py）

同一标的一次回测多个策略（默认参数），并排比较绩效：

```bash
uv run python run_compare.py --symbol 600000.SH                      # 全部内置策略
uv run python run_compare.py --symbol AAPL.US --strategies ma_cross,macd --plot --report
```

- 终端输出按 `--sort`（默认 sharpe）排序的并排指标表（含基准列）；
- `--plot` 生成净值叠加图（`backtest.plot.plot_compare`）；
- `--report` 生成自包含 HTML 对比报告（`report.render_compare_report`）；
- `--json` 输出结构化结果；支持全部保真度参数（`--market/--exec-price/--limit-board/--adjust` 等）。
- 注意：同标的多策略挑最优存在选择性偏差，结论应用 `run_validate.py` 样本外复核。

## CLI 用法

回测：

```bash
cd scripts && uv sync
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross --plot
uv run python run_backtest.py --symbol AAPL.US --strategy macd --count 800 \
    --params fast=10 slow=30
```

参数：`--symbol`、`--strategy`、`--period`（默认 1d）、`--count`（默认 1250，约 5 年）、
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

```bash
# 半 Kelly 连续仓位：f = clip(0.5μ/σ², 0, max_leverage)，滚动窗口估计（默认 60）
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross --kelly
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross --kelly --kelly-window 90
```

`--kelly` 与 `--vol-target` 同时给出时以 Kelly 为准；滚动期望为负时仓位归零
（不反向加杠杆）；账本引擎（`--engine ledger`）暂不支持，会告警忽略。

寻优：

```bash
uv run python run_optimize.py --symbol 600000.SH --strategy ma_cross
uv run python run_optimize.py --symbol AAPL.US --strategy rsi --metric calmar --top 5

# 大网格随机采样 40 组（可复现），DSR 按全部试验计算
uv run python run_optimize.py --symbol 600000.SH --strategy macd --method random --n-iter 40 --seed 7

# 贝叶斯搜索：同预算自适应聚焦高潜力参数区
uv run python run_optimize.py --symbol 600000.SH --strategy macd --method bayes --n-iter 40
```

参数：`--metric`（默认 sharpe）、`--top`（默认 10）、`--method grid|random|bayes`、
`--n-iter`（random/bayes 评估组数，默认 60）、`--seed`（默认 42）、`--allow-short`、
`--stop-loss`、`--take-profit`、`--vol-target`、`--vol-window`、`--max-leverage`，其余与回测一致。

## 交易保真度（成本/规则/成交价）

回测可信度的根基。`run_backtest.py` / `run_optimize.py` / `run_validate.py` 均支持：

```bash
# A 股真实成本（卖出印花税 5bp + 双边过户费）+ 主板涨跌停/停牌不可成交 + 次日开盘成交
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross \
    --market astock --limit-board main --exec-price open

# 显式指定复权口径（前复权默认，回测推荐）；--no-cache 强制重新拉取
uv run python run_backtest.py --symbol 600519.SH --strategy macd --adjust hfq --no-cache
```

- `--market {generic,astock}`：成本预设（astock 含卖出印花税 + 过户费）。
- `--exec-price {close,open}`：收盘成交（默认）或次日开盘成交（更贴近现实，不吃建仓前的隔夜跳空）。
- `--limit-board {main,star,chinext,st}`：启用 A 股涨跌停/停牌「不可成交」建模。
- `--adjust {forward|qfq, backward|hfq, none}`：复权口径显式化；默认前复权。
- K 线数据本地缓存与增量更新、数据源自动兜底降级（baostock/akshare/yfinance）及相关
  环境变量（`ALPHA_FORGE_NO_CACHE` / `ALPHA_FORGE_CACHE_TTL` / `ALPHA_FORGE_DATA_SOURCE`
  / `ALPHA_FORGE_RETRIES` 等）见 [data-fetching.md](data-fetching.md) 与 [faq.md](faq.md)。

## 稳健性验证（run_validate.py：走步样本外 + PBO）

「寻优挑出来的漂亮曲线」到了新数据上还灵不灵？用 `run_validate.py`：

```bash
# 走步（walk-forward）样本外验证：滚动重寻优，只在样本外计价
uv run python run_validate.py --symbol 600000.SH --strategy ma_cross

# 加做 PBO（组合对称交叉验证）估计过拟合概率
uv run python run_validate.py --symbol AAPL.US --strategy macd --pbo --count 800
```

输出样本外净值/夏普 vs 基准、各走步折的选参与样本外收益，以及 PBO（>50% 意味过拟合风险高）。
判断策略真伪应以样本外/DSR/PBO 为准，而非样本内指标。

## 事件研究（run_event.py：AAR/CAAR）

给定事件日期列表（如财报日、政策日），统计事件窗内的平均异常收益（AAR）与累计平均异常收益（CAAR）：

```bash
# 两次财报日的事件反应（默认窗口 [-10, +20] 交易日）
uv run python run_event.py --symbol 600000.SH --events 2025-04-30,2025-08-30

# 相对指数基准的超额反应 + CAAR 曲线图
uv run python run_event.py --symbol 600519.SH --events 2025-04-25 \
    --benchmark 510300.SH --pre -5 --post 15 --plot
```

> 小样本事件研究噪声很大，事件数 < 10 时结论仅供参考。

## 编程方式调用

```python
from datafeed import fetch_ohlcv
from strategies import get_strategy
from backtest import run_backtest, format_report

df = fetch_ohlcv("600519.SH", period="1d", count=500)
strategy = get_strategy("ma_cross", fast=10, slow=30)
result = run_backtest(df, strategy, symbol="600519.SH")
print(format_report(result.metrics))
```

## 仓位模式总结

| 模式 | 仓位取值 | 启用方式 |
|------|----------|----------|
| 满仓多头（默认） | {0, 1} | 无 |
| 多空 | {-1, 0, 1} | 策略 `--allow-short` |
| 连续仓位（波动率目标） | [-max_lev, max_lev] 区间连续值 | `--vol-target` |
| 连续仓位（半 Kelly） | [0, max_lev] 区间连续值 | `--kelly`（优先于 `--vol-target`） |

## 注意事项与局限

- 引擎支持**满仓（{-1,0,1}）与连续仓位**（波动率目标）两种仓位模式，暂不支持分批建仓与多标的组合（可按需扩展）。
- 止损/止盈基于收盘价逐 bar 判断（非盘中触发），适用于日线及以上周期的近似回测。
- 免费服务仅提供历史日 K；分钟级回测需配置 `TICKFLOW_API_KEY`。
- 绩效指标的年化基于近似交易日数（日线 240），仅供横向比较参考。
- 回测结果不代表未来收益，参数寻优存在过拟合风险，建议用样本外数据验证。
