# 信号服务与模拟盘参考

回测验证过的策略，离实盘还差两步：**每天知道该干什么**（信号服务），以及
**先用虚拟资金演练一段时间**（模拟盘 + 偏差追踪）。本模块提供这两个前置能力，
**不做任何自动化下单或券商对接**，所有输出仅供研究参考，不构成投资建议。

> 相关能力：回测引擎与账本引擎见 [backtesting.md](backtesting.md)，
> 压力测试见 [stress-testing.md](stress-testing.md)。

## 信号服务 `run_signal.py`

拉取最新 K 线 → 跑策略 → 输出「最新目标仓位 vs 前一日仓位 + 调仓动作」，支持多标的批量：

```bash
cd scripts   # 首次先 uv sync

# 单标的最新信号
uv run python run_signal.py --symbols 600000.SH --strategy ma_cross

# 多标的批量 + 自定义参数（建议 --no-cache 取最新价）
uv run python run_signal.py --symbols 600000.SH,600519.SH,AAPL.US \
    --strategy macd --no-cache

# 结构化 JSON（便于定时任务/agent 消费）
uv run python run_signal.py --symbols 600000.SH --strategy turtle --json > signal.json
```

输出字段：

| 字段 | 说明 |
|------|------|
| `date` | 信号对应的最新 K 线日期 |
| `close` | 最新收盘价 |
| `current_position` | 前一日策略仓位（-1~1） |
| `target_position` | 最新目标仓位（-1~1） |
| `action` | 调仓动作：买入/加仓、卖出/减仓、持有、观望 |

- 动作由仓位变化方向推导：目标 > 当前 → 买入/加仓；目标 < 当前 → 卖出/减仓；
  持平且有仓 → 持有；持平且空仓 → 观望。
- 信号基于**已收盘 K 线**计算（盘中运行时最后一根为当日未完成 K 线，注意口径）。
- 支持全部 9 个策略与 `--params`/`--allow-short`/`--config`。

## 模拟盘 `run_paper.py`

用虚拟资金按信号逐日「纸面成交」，并与同期回测预期对比偏差（tracking error）。
状态持久化在 `outputs/paper_<标的>_<策略>.json`（虚拟持仓/现金/历次成交）：

```bash
# 首次运行：初始化 10 万虚拟资金并按最新信号成交
uv run python run_paper.py --symbol 600000.SH --strategy ma_cross

# 之后每个交易日运行一次即可；同一天重复运行会幂等跳过
uv run python run_paper.py --symbol 600000.SH --strategy ma_cross

# 重置状态重新开始
uv run python run_paper.py --symbol 600000.SH --strategy ma_cross --reset
```

每次运行输出：

- **当日成交**：按账本引擎口径虚拟成交（整数股/一手约束、A 股卖出印花税等成本）；
- **当前净值**：虚拟持仓市值 + 现金，相对初始资金归一；
- **回测预期净值**：同区间账本引擎回放的理论净值（含指标预热，全量数据回放后按模拟盘起始日归一）；
- **偏差（tracking error）**：模拟盘净值与回测预期的差值，用于发现「回测与执行不一致」问题
  （如信号时点、成交约束、成本口径差异）。

关键参数：`--capital`（初始资金，仅首次生效）、`--market {generic,astock}`（成本预设，
默认 astock）、`--lot-size`（最小交易单位，astock 默认 100）。

### 建议的演练流程

1. 回测 + 稳健性验证（`run_backtest.py` / `run_validate.py`）选定策略与参数；
2. 每日收盘后运行 `run_signal.py` 看信号、`run_paper.py` 记账；
3. 跟踪 2~4 周，若偏差持续扩大，优先排查成交价约定（`--exec-price`）与成本口径；
4. 偏差稳定且可解释后，才考虑人工实盘执行（本工具不做自动下单）。

## 已知局限

- 模拟盘按**收盘价**当日成交（与账本引擎 close 口径一致），不模拟盘中滑点与部分成交；
- 不处理分红除权现金流与 T+1 可用资金（与账本引擎局限一致）；
- 状态文件按「标的 + 策略」隔离，改 `--params` 不会自动重置，需 `--reset`。

**免责声明：以上输出仅供研究参考，不构成投资建议；据此操作风险自负。**
