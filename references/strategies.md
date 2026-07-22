# 量化策略参考

本文档详细说明 `scripts/strategies/` 中内置的 14 个**单标的信号策略**。所有策略继承自
[`Strategy`](../scripts/strategies/base.py) 基类，接收 OHLCV DataFrame，输出取值
`{0, 1}` 的目标持仓信号（1=满仓多头，0=空仓）。回测引擎会对信号做 `shift(1)`
处理，因此策略内部可直接使用当日收盘价计算指标而不引入未来函数。

> 本文仅覆盖单标的策略。项目还支持**组合轮动/优化**、**多因子选股**、**配对交易**、**机器学习**、**新闻情绪**、**定投（定期定额）**六类，详见文末[其他策略类型](#其他策略类型)。

## 通用约定

- 输入：至少包含 `close` 列的 DataFrame（时间升序）。
- 输出：与输入等长的持仓信号 `pd.Series`，取值 `{-1, 0, 1}`（-1 仅在开启做空时出现）。
- 指标未形成前（如均线窗口不足）一律输出 0，不入场。
- 每个策略类定义 `param_grid` 供参数寻优使用。
- 所有策略支持 `allow_short` 参数（默认 `False`）：开启后在反向条件下输出 -1（做空），趋势类为反向趋势、均值回归类为反向极值。CLI 通过 `--allow-short` 开启。
- **跨参数校验**：非法参数组合（如 ma_cross 的 fast≥slow、donchian/turtle 的 exit>entry、cci 的 entry≥exit）在构造期抛 `ValueError` 并附修改提示；CLI 转为友好错误（退出码 1），寻优网格中的非法组合自动跳过。

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

## 8. 网格交易（grid）

- 文件：`scripts/strategies/grid_trading.py`
- 逻辑：以 `window` 日均线（shift 1，防前视）为基准价，按 `step` 划分价格网格：价格每跌一档加仓 1/(2·levels)，每涨一档减仓；基准附近持半仓。**输出 [0,1] 连续仓位**（非离散信号）。
- 参数：
  - `step`（默认 0.05）：每档价格间距（如 5%）
  - `levels`（默认 5）：单侧档数
  - `window`（默认 60）：基准均线窗口
- 参数网格：`step ∈ {0.03,0.05,0.08}`，`levels ∈ {3,5}`，`window ∈ {60,120}`

适用场景：震荡市高抛低吸；**单边下跌市会持续加仓**，建议叠加 `--stop-loss` 控制尾部风险。

## 9. 海龟交易（turtle）

- 文件：`scripts/strategies/turtle.py`
- 逻辑：唐奇安通道突破入场（突破 `entry` 日高点做多）+ 双重离场：跌破 `exit` 日低点，或自建仓价回撤超过 `atr_mult` × ATR（N 值止损）。通道与 ATR 均用历史窗口（shift 1）。
- 参数：
  - `entry`（默认 20）/ `exit`（默认 10）：入场/离场通道周期
  - `atr_window`（默认 20）：ATR 窗口
  - `atr_mult`（默认 2.0）：止损宽度（N 的倍数）
- 参数网格：`entry ∈ {20,55}`，`exit ∈ {10,20}`，`atr_mult ∈ {2.0,3.0}`

适用场景：趋势行情；与 donchian 的区别在 ATR 止损——波动放大时限制单笔亏损。

## 10. 肯特纳通道（keltner）

- 文件：`scripts/strategies/keltner.py`
- 逻辑：`window` 期 EMA 为中轨，上下轨为中轨 ± `atr_mult` × ATR（均用历史窗口 shift 1）。收盘突破上轨做多，回落中轨下方平仓；开启做空时跌破下轨做空、回升中轨上方平空。依赖 high/low（缺失时退化为 close）。
- 参数：
  - `window`（默认 20）：EMA 中轨与 ATR 窗口
  - `atr_mult`（默认 2.0）：通道宽度倍数
- 参数网格：`window ∈ {10,20,30}`，`atr_mult ∈ {1.5,2.0,2.5}`
- 参数约束：`window >= 2`、`atr_mult > 0`

适用场景：趋势突破；相比布林带（标准差轨）用 ATR 定宽，对跳空/异常波动更稳健。

## 11. SuperTrend（supertrend）

- 文件：`scripts/strategies/supertrend.py`
- 逻辑：由 (high+low)/2 ± `mult` × ATR 迭代生成追踪止损线（上升趋势中下轨只升不降、下降趋势中上轨只降不升）；收盘在 SuperTrend 线上方做多，线下离场（开启做空时线下做空）。依赖 high/low（缺失时退化为 close）。
- 参数：
  - `period`（默认 10）：ATR 窗口
  - `mult`（默认 3.0）：轨道倍数
- 参数网格：`period ∈ {7,10,14}`，`mult ∈ {2.0,3.0,4.0}`
- 参数约束：`period >= 2`、`mult > 0`

适用场景：单边趋势跟踪，追踪止损特性使回撤控制优于普通均线；震荡市会反复翻转。

## 12. Dual Thrust（dual_thrust）

- 文件：`scripts/strategies/dual_thrust.py`
- 逻辑：由过去 `n` 日的 HH/LC/HC/LL 算区间幅度 `Range = max(HH-LC, HC-LL)`；当日收盘上突 `开盘 + k1×Range` 做多，下破 `开盘 - k2×Range` 离场/做空（日线上以「次日突破当日算出的轨道」近似，与引擎 shift(1) 约定一致）。依赖 open/high/low（缺失时退化为 close）。
- 参数：
  - `n`（默认 4）：区间回看天数
  - `k1`（默认 0.5）：上轨系数（越小越易触发做多）
  - `k2`（默认 0.5）：下轨系数
- 参数网格：`n ∈ {3,4,7}`，`k1 ∈ {0.4,0.5,0.7}`，`k2 ∈ {0.4,0.5,0.7}`
- 参数约束：`n >= 1`、`k1/k2 > 0`

适用场景：日内/短周期突破体系的经典代表；震荡窄幅市容易两边挨打。

## 13. CCI 顺势（cci）

- 文件：`scripts/strategies/cci.py`
- 逻辑：典型价 `TP=(H+L+C)/3`，`CCI = (TP - MA(TP)) / (0.015 × 平均绝对偏差)`；CCI 下穿 `entry`（默认 -100，超卖）买入并维持，回升穿过 `exit`（默认 +100，超买）卖出（状态延续）。开启做空时对称反向。
- 参数：
  - `period`（默认 20）：CCI 窗口
  - `entry`（默认 -100）：超卖买入阈值
  - `exit`（默认 100）：超买卖出阈值
- 参数网格：`period ∈ {14,20,28}`，`entry ∈ {-150,-100}`，`exit ∈ {100,150}`
- 参数约束：`period >= 2`、`entry < exit`

适用场景：震荡市超卖反弹；与 RSI 类似但对极端偏离更敏感。

## 14. 威廉指标（williams_r）

- 文件：`scripts/strategies/williams_r.py`
- 逻辑：`WR = -100 × (HH - C) / (HH - LL)`（取值 [-100, 0]）；WR 低于 `lower`（默认 -80，超卖）买入并维持，高于 `upper`（默认 -20，超买）卖出（状态延续）。依赖 high/low（缺失时退化为 close）。
- 参数：
  - `period`（默认 14）：回看窗口
  - `lower`（默认 -80）：超卖买入阈值
  - `upper`（默认 -20）：超买卖出阈值
- 参数网格：`period ∈ {10,14,21}`，`lower ∈ {-90,-80}`，`upper ∈ {-20,-10}`
- 参数约束：`period >= 2`、`-100 <= lower < upper <= 0`

适用场景：短周期超买超卖均值回归；与 KDJ 的 RSV 同源，信号更直接。

## 其他策略类型

除上述单标的策略外，项目还支持以下策略类型（各有独立文档与 CLI）：

| 类型 | 策略/方法 | CLI | 文档 |
| --- | --- | --- | --- |
| 组合轮动/优化 | `momentum` / `equal_weight` / `inverse_vol` / `min_variance` / `max_sharpe` / `hrp` / `min_cvar` | `run_portfolio.py --strategy <name>` | [portfolio.md](./portfolio.md) |
| 多因子选股 | `momentum` / `low_vol` / `reversal` / `sharpe_mom` / `consistency` / `value_ep` / `value_bp` / `quality_roe` / `quality_debt` / `size`（可多因子合成） | `run_factor.py --factors <names>` | [multi-factor.md](./multi-factor.md) |
| 配对交易（统计套利） | 相关性+对冲比率+半衰期筛选，价差 z-score 开平仓，市场中性多空 | `run_pairs.py` | [pairs-trading.md](./pairs-trading.md) |
| 机器学习 | 技术指标特征 + LightGBM 方向预测，走步（walk-forward）重训练与样本外验证；支持三重障碍标注（--label triple）与 meta-labeling 信号过滤（--meta <策略>） | `run_ml.py` | [ml-strategy.md](./ml-strategy.md) |
| 新闻情绪 | akshare 抓 A 股新闻 + AI（agent LLM）情绪打分，情绪信号回测 | `run_sentiment.py`（两阶段） | [sentiment.md](./sentiment.md) |
| 定投（定期定额/DCA） | 按周期（日/周/月）定额注入现金、份额累积，资金加权 XIRR 计量；含智能定投/超跌加码/价值平均增强模式，双基准对比 | `run_dca.py --mode <name>` | [dca.md](./dca.md) |
| 自定义规则（DSL） | TOML 声明式规则：白名单指标 + 受限条件表达式，入场/离场逻辑 and/or | `run_custom.py --rules <file>` | 见下文 |

## 自定义规则策略（DSL，Agent 可生成）

内置 14 个策略是「固定菜单」；**自定义规则 DSL** 让用户（或 Agent）用自然语言
描述策略想法，生成 TOML 规则文件后直接回测验证——策略空间不再受限。对应
CLI：[`run_custom.py`](../scripts/run_custom.py)，引擎：[`strategies/custom.py`](../scripts/strategies/custom.py)。

### 规则文件结构（三段式）

```toml
[meta]
name = "golden_cross_rsi"
description = "金叉且 RSI 未过热时买入，死叉或 RSI 超买时卖出"

# 指标定义（白名单算子，按定义顺序计算，可引用其他指标作 source）
[indicators.fast_ma]
type = "sma"
period = 10

[indicators.slow_ma]
type = "sma"
period = 30

[indicators.rsi14]
type = "rsi"
period = 14

# 入场条件（全部满足 = and）
[entry]
logic = "and"
conditions = [
    "fast_ma crosses_above slow_ma",   # 金叉
    "rsi14 < 70",                       # RSI 未过热
]

# 离场条件（任一满足 = or）
[exit]
logic = "or"
conditions = [
    "fast_ma crosses_below slow_ma",   # 死叉
    "rsi14 > 80",                       # RSI 超买
]
```

### 指标白名单

| 类型 | 参数 | 说明 |
|------|------|------|
| `sma` / `ema` | `period`, `source`(默认 close) | 简单/指数均线 |
| `rsi` | `period` | Wilder RSI |
| `macd_line` / `macd_signal` / `macd_hist` | `fast`/`slow`/`signal` | MACD 三线 |
| `bollinger_upper` / `bollinger_mid` / `bollinger_lower` | `period`, `std`(默认 2) | 布林带三轨 |
| `atr` | `period` | 平均真实波幅 |
| `donchian_upper` / `donchian_lower` | `period` | 唐奇安通道上下轨 |
| `kdj_k` / `kdj_d` | `period`/`k_smooth`/`d_smooth` | KDJ 的 K/D 值 |
| `momentum` / `roc` | `period` | 动量（差值）/ 变动率 |
| `close`/`open`/`high`/`low`/`volume` | — | 原始 OHLCV 列 |

条件中可直接引用 `close/open/high/low/volume` 而无需在 `[indicators]` 中定义。

### 条件表达式（受限语法）

格式：`<指标名或数值> <运算符> <指标名或数值>`。运算符：
`>` / `<` / `>=` / `<=` / `crosses_above`（金叉）/ `crosses_below`（死叉）。
`[entry]`/`[exit]` 各自的 `logic` 控制条件间是 `and` 还是 `or` 组合。

### 安全设计

- **不执行任意代码**：仅解析受限表达式，无 `eval`；
- **白名单约束**：未知指标类型/未定义引用/语法错误均报友好错误（附可用指标与运算符清单）；
- **预热期保护**：指标窗口不足期间信号强制为 0，不入场。

### 运行

```bash
# 用示例规则回测（金叉 + RSI 过滤）
uv run python run_custom.py --symbol 600000.SH --rules examples/custom_rule.toml --plot

# 结构化 JSON（含规则摘要 rules 字段）
uv run python run_custom.py --symbol AAPL.US --rules my_rule.toml --json
```

指标/运算符白名单可用 `run_list.py --json` 的 `custom_dsl` 字段查询。
自定义规则**未经样本外验证**，回测结果不代表未来收益；建议与内置策略对比
（`run_compare.py`）并用 `run_validate.py` 验证稳健性。

## 扩展新策略

1. 在 `scripts/strategies/` 新建文件，定义继承 `Strategy` 的类，实现
   `default_params()`、`generate_signals()`，并设置 `name`、`display_name`、`param_grid`；
   如有跨参数约束，覆盖 `validate_params()` 在非法组合时抛 `ValueError`（含修改提示）。
2. 在 `scripts/strategies/__init__.py` 的 `STRATEGIES` 注册表中登记该类。
3. 之后即可通过 `--strategy <name>` 在 CLI 中使用，并自动支持回测、寻优、对比与信号服务；寻优会自动跳过 `validate_params` 判定的非法网格组合。
