---
name: alpha-forge-skill
description: 使用 TickFlow Python SDK 获取 A 股、港股、美股、期货等市场的实时行情、K 线与财务数据，内置双均线、MACD、RSI、布林带、动量、唐奇安通道、KDJ 等 7 个量化策略与轻量回测引擎（支持多空、止损止盈、波动率目标仓位、绩效指标、可视化、参数寻优），多标的组合回测与截面轮动（动量/等权/风险平价/最小方差/最大夏普），多因子选股（价值/质量/规模/动量/波动率因子打分与分层回测），配对交易（市场中性统计套利），机器学习策略（LightGBM 方向预测 + 走步样本外验证），新闻情绪交易（akshare 新闻 + AI 情绪打分），以及定投（定期定额/DCA 现金流回测 + 资金加权 XIRR，含智能定投/超跌加码/价值平均等增强模式与双基准对比）。当需要查询多市场行情/K线/财务数据、下载历史数据做分析，或对交易策略（含做空、止损止盈、多标的轮动、多因子选股、配对交易/统计套利、组合优化、机器学习、新闻情绪、定投/定期定额）进行历史回测与参数寻优时使用。
compatibility: Requires Python 3.10+, uv, and network access; optional TICKFLOW_API_KEY for realtime/minute data
metadata: {"clawdbot":{"emoji":"📈","homepage":"https://tickflow.org","requires":{"bins":["python3","uv"],"env":["TICKFLOW_API_KEY"]}}}
---

# Alpha Forge Skill

通过 TickFlow Python SDK 获取 A 股、港股、美股、期货等市场的实时行情、K 线与财务数据，并内置经典量化策略与轻量回测引擎。适用于量化交易、数据分析、策略研究等场景。

## 能力导航

按需查阅以下资源，避免一次性加载全部细节：

| 资源 | 用途 |
|------|------|
| [references/data-fetching.md](references/data-fetching.md) | 数据获取详解：标的代码格式、行情/K线/财务示例、常用 K 线周期、实用分析场景 |
| [references/strategies.md](references/strategies.md) | 内置量化策略的原理、参数与信号逻辑 |
| [references/backtesting.md](references/backtesting.md) | 回测引擎、绩效指标、可视化与参数寻优的详细说明 |
| [references/portfolio.md](references/portfolio.md) | 多标的组合回测、截面轮动（动量/等权/风险平价）与组合优化（最小方差/最大夏普） |
| [references/multi-factor.md](references/multi-factor.md) | 多因子选股：五类因子打分合成、分位选股、分层回测 |
| [references/pairs-trading.md](references/pairs-trading.md) | 配对交易：市场中性统计套利，价差 z-score 开平仓 |
| [references/ml-strategy.md](references/ml-strategy.md) | 机器学习策略：技术指标特征 + LightGBM 方向预测 + 走步样本外验证 |
| [references/sentiment.md](references/sentiment.md) | 新闻情绪交易：akshare 抓新闻 + AI（agent LLM）情绪打分 + 情绪信号回测 |
| [references/dca.md](references/dca.md) | 定投（定期定额/DCA）：现金流账本回测、资金加权 XIRR、智能定投/超跌加码/价值平均等增强模式、双基准对比 |
| [references/use-cases.md](references/use-cases.md) | 新手引导动线（Level 0→6 逐级上手）+ 端到端典型用例（策略选优、风控、多空、组合、跨市场、研究流水线） |
| `scripts/` | 可直接运行的回测工具代码（策略库、回测引擎、组合、CLI） |

## 环境配置

### 1. 安装 uv（如果未安装）

```bash
uv --version   # 检查是否已安装

# 未安装时（macOS/Linux）
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. 安装依赖

项目的 `scripts/` 目录已配置好运行环境（含 tickflow、pandas、numpy、matplotlib），数据获取与回测均在此运行：

```bash
cd scripts
uv sync
```

### 3. 配置 API Key

免费服务无需 API Key 即可获取历史日 K 线并完成单标的回测；以下能力依赖 TickFlow 完整服务，需要配置环境变量 `TICKFLOW_API_KEY`：

| 能力 | 是否需要 API Key |
|------|------------------|
| 历史日 K 线（1d/1w/1M/1Q/1Y）、单标的回测、参数寻优 | 否，免费服务即可 |
| 实时行情、分钟 K 线（1m/5m/15m/30m/60m）、日内分时 | 是 |
| 股票池成分（`--universe`，多因子/配对交易自动选池依赖） | 是 |
| 财务数据 / 基本面因子（价值、质量、规模） | 是（且账号需具备财务数据权限） |

**如何申请：** 前往 [tickflow.org](https://tickflow.org) 注册并在控制台申请 API Key。

**如何配置（macOS/Linux）：**

```bash
# 当前会话临时生效
export TICKFLOW_API_KEY="your-api-key"

# 持久化写入 shell 配置（zsh），一劳永逸
echo 'export TICKFLOW_API_KEY="your-api-key"' >> ~/.zshrc && source ~/.zshrc

# 验证是否配置成功（应回显你的 Key）
echo $TICKFLOW_API_KEY
```

Windows (PowerShell)：`$env:TICKFLOW_API_KEY="your-api-key"`（当前会话）或 `setx TICKFLOW_API_KEY "your-api-key"`（持久化，需重开终端）。

> 未配置时，脚本会在需要 API Key 的接口处报错或告警，并附带上述申请与配置步骤指引；其中财务因子等能力会自动降级为价格因子继续运行。

## 快速开始

标的代码统一格式为 **代码.市场后缀**（如 `600000.SH`、`AAPL.US`、`00700.HK`）。完整市场后缀与更多示例见 [references/data-fetching.md](references/data-fetching.md)。

### 免费服务（历史数据分析）

```python
from tickflow import TickFlow

tf = TickFlow.free()  # 无需 API Key
df = tf.klines.get("600000.SH", period="1d", count=100, as_dataframe=True)
print(df.tail())
```

支持历史日 K 线（1d/1w/1M/1Q/1Y）、标的信息与标的池；不支持实时行情与分钟 K 线。

### 完整服务（实时行情 + 全部功能）

```python
from tickflow import TickFlow

tf = TickFlow()  # 自动读取环境变量 TICKFLOW_API_KEY
quotes = tf.quotes.get(symbols=["600000.SH", "AAPL.US", "00700.HK"])
for q in quotes:
    print(f"{q['symbol']}: {q['last_price']}")
```

更多数据获取用法（行情、K 线、日内分时、财务数据、批量接口、实用分析场景）详见 [references/data-fetching.md](references/data-fetching.md)。

## 量化策略与回测

在数据获取能力之上，本 Skill 内置了一套经典量化策略与轻量回测引擎，代码位于 `scripts/` 目录，可直接运行。

### 内置策略

| 策略名 | 名称 | 核心逻辑 |
|--------|------|----------|
| `ma_cross` | 双均线交叉 | 短均线上穿长均线做多，下穿空仓 |
| `macd` | MACD | DIF 上穿 DEA 做多，下穿空仓 |
| `rsi` | RSI 超买超卖 | RSI 超卖买入，超买卖出 |
| `bollinger` | 布林带 | 跌破下轨买入，回归中轨/突破上轨卖出 |
| `momentum` | 动量 | 过去 N 期收益为正做多 |
| `donchian` | 唐奇安通道突破 | 突破 N 日高点做多，跌破离场 |
| `kdj` | KDJ | K 上穿 D 做多，下穿平多 |

策略原理、参数与信号逻辑详见 [references/strategies.md](references/strategies.md)；回测引擎、绩效指标、可视化与参数寻优详见 [references/backtesting.md](references/backtesting.md)。

### 运行回测

```bash
cd scripts   # 首次先执行 uv sync

# 基础回测（输出绩效指标报告）
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross

# 回测并生成图表（净值曲线/回撤/买卖点）
uv run python run_backtest.py --symbol 600000.SH --strategy macd --plot

# 自定义策略参数与 K 线数量（--params 支持空格或逗号分隔，如 fast=10,slow=30）
uv run python run_backtest.py --symbol AAPL.US --strategy ma_cross --count 800 --params fast=10 slow=30

# 开启做空 + 止损 5% + 止盈 15%
uv run python run_backtest.py --symbol 600000.SH --strategy macd --allow-short --stop-loss 0.05 --take-profit 0.15

# 波动率目标仓位 15%（连续仓位，默认不加杠杆）
uv run python run_backtest.py --symbol 600000.SH --strategy kdj --vol-target 0.15
```

输出包含累计收益、年化收益、年化波动、夏普比率、索提诺比率、最大回撤、卡玛比率、交易次数、胜率，并与 Buy & Hold 基准对比。引擎支持**多空**（`--allow-short`）、**止损/止盈**（`--stop-loss` / `--take-profit`）与**波动率目标连续仓位**（`--vol-target` / `--max-leverage`）。

### 参数寻优

```bash
# 对策略参数网格寻优，默认按夏普比率排序
uv run python run_optimize.py --symbol 600000.SH --strategy ma_cross

# 按卡玛比率排序，取前 5 组
uv run python run_optimize.py --symbol AAPL.US --strategy rsi --metric calmar --top 5
```

### 编程方式调用

```python
from datafeed import fetch_ohlcv
from strategies import get_strategy
from backtest import run_backtest, format_report

df = fetch_ohlcv("600519.SH", period="1d", count=500)
strategy = get_strategy("ma_cross", fast=10, slow=30)
result = run_backtest(df, strategy, symbol="600519.SH")
print(format_report(result.metrics))
```

> 注：回测引擎内部对信号做 `shift(1)`（当日信号次日生效）以避免前视偏差，并按持仓变动扣除手续费与滑点。

### 多标的组合轮动

除单标的策略外，还支持多标的组合回测与截面轮动（动量轮动 `momentum`、等权 `equal_weight`、风险平价 `inverse_vol`）：

```bash
# 截面动量轮动（持有涨幅前 2 名，每 20 日调仓）
uv run python run_portfolio.py --symbols 600000.SH,000001.SZ,600519.SH,000858.SZ --strategy momentum --top-k 2

# 风险平价 + 出图
uv run python run_portfolio.py --symbols AAPL.US,MSFT.US,TSLA.US --strategy inverse_vol --plot
```

组合回测会与**等权基准**对比，详见 [references/portfolio.md](references/portfolio.md)。

### 多因子选股

对股票池按五类因子（价值/质量/规模/动量/波动率）打分选股，并用分层回测验证因子有效性：

```bash
# 股票池前 30 只，全因子（价值/质量/规模因子需财务数据权限，否则自动降级为价格因子）
uv run python run_factor.py --universe CN_Equity_A --limit 30

# 仅价格因子（动量+低波），无需财务权限
uv run python run_factor.py --symbols 600000.SH,000001.SZ,600519.SH,000858.SZ,600809.SH --factors momentum,low_vol --plot
```

> 财务因子需 `TICKFLOW_API_KEY` 及财务数据权限；无权限时自动跳过并仅用价格因子。详见 [references/multi-factor.md](references/multi-factor.md)。

### 配对交易与组合优化

支持市场中性的配对交易（统计套利），以及最小方差/最大夏普组合优化：

```bash
# 配对交易：手动一对，价差 z-score 开平仓
uv run python run_pairs.py --symbols 600000.SH,601398.SH --plot

# 配对交易：从股票池自动筛选最佳配对
uv run python run_pairs.py --universe CN_Equity_A --limit 40 --top-pairs 3

# 组合优化：最小方差 / 最大夏普
uv run python run_portfolio.py --symbols AAPL.US,MSFT.US,TSLA.US,AMZN.US,GOOGL.US --strategy min_variance
```

配对交易详见 [references/pairs-trading.md](references/pairs-trading.md)，组合优化详见 [references/portfolio.md](references/portfolio.md)。

### 机器学习策略

用 LightGBM 学习技术指标特征与未来收益方向的关系，**走步（walk-forward）重训练**并
**只在样本外（OOS）段计价**，天然规避前视与未来数据泄露：

```bash
# 走步训练 + 样本外回测 + 出图（免费日 K 即可，无需 API Key）
uv run python run_ml.py --symbol 600000.SH --count 800 --plot

# 更长历史、允许做空、调中性带阈值
uv run python run_ml.py --symbol AAPL.US --count 1000 --allow-short --threshold 0.08
```

报告中的净值/夏普均为样本外结果；夏普 > 3 会打印怀疑提示。详见 [references/ml-strategy.md](references/ml-strategy.md)。

### 新闻情绪交易

让 AI 实时读新闻给出情绪判断，再转化为持仓信号回测。情绪判断由 **agent 的 LLM**
完成（agent-in-the-loop 三步），无需本地 NLP 模型或额外 LLM Key：

```bash
# 第一步：抓 A 股个股新闻（akshare，无需 Key），生成待填打分模板
uv run python run_sentiment.py --symbol 600000.SH --stage fetch

# 第二步：agent 阅读 ../outputs/news_600000SH.csv，将情绪分（-1~1）写入 ../outputs/sentiment_600000SH.csv

# 第三步：读取打分，聚合情绪信号并回测出图
uv run python run_sentiment.py --symbol 600000.SH --stage backtest --plot
```

> 数据源 akshare 仅返回最近约 100 条新闻，回测为近端短窗口演示；仅支持 A 股。详见 [references/sentiment.md](references/sentiment.md)。

### 定投（定期定额 / DCA）

定投按固定周期投入固定金额、累积份额，靠摊薄成本获利，与信号择时策略本质不同，
因此单独用**现金流账本**建模，核心指标为**资金加权年化收益率（XIRR）**。除纯定投外，还内置
**智能定投 / 超跌加码 / 价值平均**等增强模式（`--mode`），并与**一次性投入**、**纯定投**双基准对比：

```bash
# 每月纯定投 1000（默认），输出定投报告 + 一次性投入基准对比（免费日 K 即可）
uv run python run_dca.py --symbol 600000.SH

# 智能定投：按偏离 60 日均线幅度分档加码/减码/暂停
uv run python run_dca.py --symbol 600519.SH --mode smart --ma-window 60 --plot

# 超跌回撤加码：按距近期高点回撤深度分档 + RSI 超卖
uv run python run_dca.py --symbol AAPL.US --mode dip --dip-window 120 --count 1000 --plot

# 价值平均：盯目标市值增长线，涨过目标会卖出
uv run python run_dca.py --symbol 600000.SH --mode value_avg --amount 1000 --plot
```

> 先比 **IRR vs 基准 A（纯定投）** 判断加码/择时是否真的有正贡献，再比 **基准 B（一次性投入）**。详见 [references/dca.md](references/dca.md)。

## 典型用例

以下为常见工作流，完整命令与结果解读见 [references/use-cases.md](references/use-cases.md)：

| 场景 | 一句话做法 |
|------|----------|
| 单股策略选优 | 多个 `--strategy` 逐个回测，比夏普与回撤 |
| 参数寻优→复跑 | `run_optimize.py` 找最优参，再 `run_backtest.py --params ... --plot` |
| 风控改善 | 同策略叠加 `--stop-loss` / `--vol-target` 对比回撤 |
| 多空对冲 | `--allow-short` 应对下跌或震荡市 |
| 组合轮动 | `run_portfolio.py` 比较 momentum/equal_weight/inverse_vol |
| 跨市场组合 | A股+美股+港股混合轮动分散风险 |
| 多因子选股 | `run_factor.py` 对股票池打分选股 + 分层验证 |
| 配对交易 | `run_pairs.py` 价差 z-score 做市场中性套利 |
| 机器学习 | `run_ml.py` LightGBM 预测方向，走步样本外回测 |
| 新闻情绪 | `run_sentiment.py --stage fetch` 抓新闻→agent 打分→`--stage backtest` |
| 定投定期定额 | `run_dca.py` 按周期定投，看资金加权 IRR 与一次性投入对比 |
| 组合优化 | `run_portfolio.py --strategy min_variance/max_sharpe` |

## 注意事项

- 数据获取与回测脚本均在 `scripts/` 目录下用 `uv run python` 运行，首次需 `uv sync`。
- 所有 `--plot` 生成的回测图表统一输出到与 `scripts/` 平级的项目根目录 `outputs/` 目录（首次自动创建，已在 `.gitignore` 忽略）；文件名默认按关键参数自动命名以避免互相覆盖，如 `backtest_600000SH_ma_cross.png`、`portfolio_momentum_4syms.png`、`pairs_600000SH_601398SH.png`、`dca_600000SH_monthly.png`（相同配置重跑才覆盖）；可用 `--output <路径>` 自定义。
- SDK 支持 Python 3.9+，推荐 Python 3.10 或更高版本。
- 免费服务仅提供历史日 K 线；实时行情与分钟 K 线需配置 `TICKFLOW_API_KEY`。
- 支持 A 股、港股、美股、国内期货等多市场，标的代码可混合查询；`as_dataframe=True` 直接返回 pandas DataFrame。
- 机器学习（`run_ml.py`）与新闻情绪（`run_sentiment.py`）模块新增依赖 `lightgbm`、`akshare`（已写入 `scripts/pyproject.toml`，`uv sync` 自动安装，会抬高安装体积）。macOS 上 LightGBM 还需 OpenMP 运行库，若报错 `libomp.dylib` 请 `brew install libomp`。
- 新闻情绪采用 **agent-in-the-loop**：`--stage fetch` 抓新闻→agent（LLM）读 `../outputs/news_<标的>.csv` 逐条打分写入 `../outputs/sentiment_<标的>.csv`→`--stage backtest` 回测；无 agent 时可加 `--use-lexicon` 词典兜底（质量有限）。akshare 新闻仅约 100 条且仅 A 股，回测为短窗口演示。
- 回测结果不代表未来收益，参数寻优存在过拟合风险，建议用样本外数据验证；机器学习模块已内置走步样本外验证，任何策略**夏普比率 > 3 应优先怀疑**未来数据泄露或过拟合，而非视为策略有效。
