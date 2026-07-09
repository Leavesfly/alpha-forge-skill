# 量化策略参考

本文档详细说明 `scripts/strategies/` 中内置的 7 个**单标的信号策略**。所有策略继承自
[`Strategy`](../scripts/strategies/base.py) 基类，接收 OHLCV DataFrame，输出取值
`{0, 1}` 的目标持仓信号（1=满仓多头，0=空仓）。回测引擎会对信号做 `shift(1)`
处理，因此策略内部可直接使用当日收盘价计算指标而不引入未来函数。

> 本文仅覆盖单标的策略。项目还支持**组合轮动/优化**、**多因子选股**、**配对交易**、**机器学习**、**新闻情绪**五类，详见文末[其他策略类型](#其他策略类型)。

## 通用约定

- 输入：至少包含 `close` 列的 DataFrame（时间升序）。
- 输出：与输入等长的持仓信号 `pd.Series`，取值 `{-1, 0, 1}`（-1 仅在开启做空时出现）。
- 指标未形成前（如均线窗口不足）一律输出 0，不入场。
- 每个策略类定义 `param_grid` 供参数寻优使用。
- 所有策略支持 `allow_short` 参数（默认 `False`）：开启后在反向条件下输出 -1（做空），趋势类为反向趋势、均值回归类为反向极值。CLI 通过 `--allow-short` 开启。

## 1. 双均线交叉（ma_cross）

- 文件：`scripts/strategies/ma_cross.py`
- 逻辑：计算快、慢两条均线，快线在慢线之上时持有多头，否则空仓。
- 参数：
  - `fast`（默认 5）：快均线周期
  - `slow`（默认 20）：慢均线周期
  - `ma_type`（默认 `sma`）：`sma` 简单均线或 `ema` 指数均线
- 参数网格：`fast ∈ {5,10,20}`，`slow ∈ {20,30,60}`（寻优时自动跳过 fast≥slow）

适用场景：趋势明显的品种。震荡市易频繁交叉产生假信号。

## 2. MACD（macd）

- 文件：`scripts/strategies/macd.py`
- 逻辑：`DIF = EMA(fast) - EMA(slow)`，`DEA = EMA(DIF, signal)`；DIF 在 DEA 之上持有多头。
- 参数：
  - `fast`（默认 12）：快 EMA 周期
  - `slow`（默认 26）：慢 EMA 周期
  - `signal`（默认 9）：信号线周期
- 参数网格：`fast ∈ {8,12,16}`，`slow ∈ {20,26,34}`，`signal ∈ {7,9,12}`

适用场景：中期趋势跟踪，比双均线略平滑。

## 3. RSI 超买超卖（rsi）

- 文件：`scripts/strategies/rsi.py`
- 逻辑：Wilder 平滑法计算 RSI。RSI 低于 `lower`（超卖）买入并维持多头，
  直到 RSI 高于 `upper`（超买）卖出空仓（状态延续，避免频繁进出）。
- 参数：
  - `period`（默认 14）：RSI 周期
  - `lower`（默认 30）：超卖阈值（买入）
  - `upper`（默认 70）：超买阈值（卖出）
- 参数网格：`period ∈ {6,14,21}`，`lower ∈ {20,30,40}`，`upper ∈ {60,70,80}`

适用场景：震荡市均值回归。单边趋势中可能过早离场。

## 4. 布林带（bollinger）

- 文件：`scripts/strategies/bollinger.py`
- 逻辑：中轨为 `window` 期均线，上下轨为中轨 ± `num_std` 倍标准差。价格跌破下轨
  买入，回升至中轨上方或触及上轨时卖出（均值回归模式，状态延续）。
- 参数：
  - `window`（默认 20）：均线与标准差窗口
  - `num_std`（默认 2.0）：轨道标准差倍数
- 参数网格：`window ∈ {10,20,30}`，`num_std ∈ {1.5,2.0,2.5}`

适用场景：区间震荡品种。带口开张的强趋势中需谨慎。

## 5. 动量（momentum）

- 文件：`scripts/strategies/momentum.py`
- 逻辑：计算过去 `period` 期收益率（ROC），为正则做多；开启做空时为负则做空。
- 参数：
  - `period`（默认 20）：动量回看周期
- 参数网格：`period ∈ {10,20,30,60}`

适用场景：趋势持续的品种。震荡市易反复进出。

## 6. 唐奇安通道突破（donchian）

- 文件：`scripts/strategies/donchian.py`
- 逻辑：收盘价突破过去 `entry` 日最高价做多；跌破过去 `exit` 日最低价平多。开启做空时，跌破 `entry` 日最低价做空，突破 `exit` 日最高价平空。通道基于历史窗口（shift 1）计算。依赖 high/low（缺失时退化为 close）。
- 参数：
  - `entry`（默认 20）：入场通道周期
  - `exit`（默认 10）：离场通道周期
- 参数网格：`entry ∈ {20,40,55}`，`exit ∈ {10,20}`

适用场景：趋势/突破行情（海龟交易法的核心）。

## 7. KDJ（kdj）

- 文件：`scripts/strategies/kdj.py`
- 逻辑：基于 RSV 计算 K/D/J 三线（K/D 采用平滑）；K 在 D 之上（金叉）持多，下穿（死叉）平多；开启做空时死叉持空。依赖 high/low（缺失时退化为 close）。
- 参数：
  - `n`（默认 9）：RSV 周期
  - `k_period`（默认 3）：K 平滑周期
  - `d_period`（默认 3）：D 平滑周期
- 参数网格：`n ∈ {9,14,21}`，`k_period ∈ {3,5}`，`d_period ∈ {3,5}`

适用场景：震荡与趋势均可，对短期转折比较敏感。

## 其他策略类型

除上述单标的策略外，项目还支持以下策略类型（各有独立文档与 CLI）：

| 类型 | 策略/方法 | CLI | 文档 |
| --- | --- | --- | --- |
| 组合轮动/优化 | `momentum` / `equal_weight` / `inverse_vol` / `min_variance` / `max_sharpe` | `run_portfolio.py --strategy <name>` | [portfolio.md](./portfolio.md) |
| 多因子选股 | `momentum` / `low_vol` / `value_ep` / `value_bp` / `quality_roe` / `quality_debt` / `size`（可多因子合成） | `run_factor.py --factors <names>` | [multi-factor.md](./multi-factor.md) |
| 配对交易（统计套利） | 相关性+对冲比率+半衰期筛选，价差 z-score 开平仓，市场中性多空 | `run_pairs.py` | [pairs-trading.md](./pairs-trading.md) |
| 机器学习 | 技术指标特征 + LightGBM 方向预测，走步（walk-forward）重训练与样本外验证 | `run_ml.py` | [ml-strategy.md](./ml-strategy.md) |
| 新闻情绪 | akshare 抓 A 股新闻 + AI（agent LLM）情绪打分，情绪信号回测 | `run_sentiment.py`（两阶段） | [sentiment.md](./sentiment.md) |

## 扩展新策略

1. 在 `scripts/strategies/` 新建文件，定义继承 `Strategy` 的类，实现
   `default_params()`、`generate_signals()`，并设置 `name`、`display_name`、`param_grid`。
2. 在 `scripts/strategies/__init__.py` 的 `STRATEGIES` 注册表中登记该类。
3. 之后即可通过 `--strategy <name>` 在 CLI 中使用，并自动支持回测与寻优。
