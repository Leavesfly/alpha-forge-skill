---
name: alpha-forge-skill
description: 使用 TickFlow Python SDK 获取 A 股、港股、美股、期货等市场的实时行情、K 线与财务数据（akshare 兜底降级），内置双均线、MACD、RSI、布林带、动量、唐奇安通道、KDJ、网格交易、海龟交易等 9 个量化策略与轻量回测引擎（支持多空、止损止盈、波动率目标仓位、账本引擎整数股/一手约束、绩效指标、可视化、多策略对比、并行参数寻优、压力测试、TOML 配置），多标的组合回测与截面轮动（动量/等权/风险平价/最小方差/最大夏普），多因子选股（价值/质量/规模/动量/波动率因子打分与分层回测），配对交易（市场中性统计套利），机器学习策略（LightGBM/Ridge/Logistic 可插拔方向预测 + 走步样本外验证 + 置信度仓位），新闻情绪交易（akshare 新闻 + AI 情绪打分），定投（定期定额/DCA 现金流回测 + 资金加权 XIRR，含智能定投/超跌加码/价值平均等增强模式与双基准对比），以及实盘前置能力（每日信号服务、模拟盘纸面交易与偏差追踪、事件研究 AAR/CAAR）。当需要查询多市场行情/K线/财务数据、下载历史数据做分析，或对交易策略（含做空、止损止盈、多标的轮动、多因子选股、配对交易/统计套利、组合优化、机器学习、新闻情绪、定投/定期定额）进行历史回测、参数寻优、压力测试、每日信号跟踪、模拟盘演练与事件研究时使用。
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
| [references/backtesting.md](references/backtesting.md) | 回测引擎（含账本引擎）、绩效指标、可视化、多策略对比与参数寻优的详细说明 |
| [references/portfolio.md](references/portfolio.md) | 多标的组合回测、截面轮动（动量/等权/风险平价）与组合优化（最小方差/最大夏普） |
| [references/multi-factor.md](references/multi-factor.md) | 多因子选股：五类因子打分合成、分位选股、分层回测 |
| [references/pairs-trading.md](references/pairs-trading.md) | 配对交易：市场中性统计套利，价差 z-score 开平仓 |
| [references/ml-strategy.md](references/ml-strategy.md) | 机器学习策略：技术指标特征 + 可插拔模型（LightGBM/Ridge/Logistic）方向预测 + 走步样本外验证 |
| [references/sentiment.md](references/sentiment.md) | 新闻情绪交易：akshare 抓新闻 + AI（agent LLM）情绪打分 + 情绪信号回测 |
| [references/dca.md](references/dca.md) | 定投（定期定额/DCA）：现金流账本回测、资金加权 XIRR、智能定投/超跌加码/价值平均等增强模式、双基准对比 |
| [references/stress-testing.md](references/stress-testing.md) | 压力测试（历史情景重放 + 蒙特卡洛冲击）与 TOML 配置文件（--config） |
| [references/live-signal.md](references/live-signal.md) | 实盘前置：每日信号服务（run_signal）与模拟盘纸面交易 + 偏差追踪（run_paper） |
| [references/use-cases.md](references/use-cases.md) | 新手引导动线（Level 0→6 逐级上手）+ 端到端典型用例 + Agent 结构化调用指南（JSON 约定/退出码/批量实验） |
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

### 内置策略（9 个）

| 策略名 | 名称 | 核心逻辑 |
|--------|------|----------|
| `ma_cross` | 双均线交叉 | 短均线上穿长均线做多，下穿空仓 |
| `macd` | MACD | DIF 上穿 DEA 做多，下穿空仓 |
| `rsi` | RSI 超买超卖 | RSI 超卖买入，超买卖出 |
| `bollinger` | 布林带 | 跌破下轨买入，回归中轨/突破上轨卖出 |
| `momentum` | 动量 | 过去 N 期收益为正做多 |
| `donchian` | 唐奇安通道突破 | 突破 N 日高点做多，跌破离场 |
| `kdj` | KDJ | K 上穿 D 做多，下穿平多 |
| `grid` | 网格交易 | 以均线为基准分档，跌加仓涨减仓（连续仓位，适合震荡市） |
| `turtle` | 海龟交易 | 唐奇安突破入场 + ATR 止损（N 值风控） |

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

# 账本引擎：现金+整数股（A 股一手 100 股），10 万本金真实建仓约束
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross \
    --engine ledger --market astock --capital 100000

# 压力测试：历史情景重放（2015 股灾/2018 熊市等）+ 蒙特卡洛冲击
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross --stress

# 从 TOML 配置文件读参数（显式命令行参数优先）
uv run python run_backtest.py --config examples/backtest.toml
```

输出包含累计收益、年化收益、年化波动、夏普比率、索提诺比率、最大回撤、卡玛比率、交易次数、胜率，并与 Buy & Hold 基准对比。引擎支持**多空**（`--allow-short`）、**止损/止盈**（`--stop-loss` / `--take-profit`）与**波动率目标连续仓位**（`--vol-target` / `--max-leverage`）。

### 参数寻优

```bash
# 对策略参数网格寻优，默认按夏普比率排序（多核并行，--jobs 1 可强制串行）
uv run python run_optimize.py --symbol 600000.SH --strategy ma_cross

# 按卡玛比率排序，取前 5 组
uv run python run_optimize.py --symbol AAPL.US --strategy rsi --metric calmar --top 5
```

> 寻优结束后会打印 **Deflated Sharpe Ratio（DSR）** 过拟合诊断：对“试了多少组参数”做惩罚后，最优参数是否仍显著。DSR < 90% 应高度警惕。

### 多策略对比

同一标的一条命令对比多个策略（缺省全部 9 个），并排绩效表 + 净值叠加图 + HTML 对比报告：

```bash
# 全策略对比（默认参数，按夏普排序）
uv run python run_compare.py --symbol 600000.SH

# 指定策略子集 + 净值叠加图 + HTML 对比报告
uv run python run_compare.py --symbol AAPL.US --strategies ma_cross,macd,rsi --plot --report

# 结构化 JSON（stdout 仅留 JSON）
uv run python run_compare.py --symbol 600000.SH --json > compare.json
```

### 交易保真度（成本/规则/成交价）

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
- K 线数据已本地缓存（默认 1 天），重复回测/寻优不再反复走网络；环境变量 `ALPHA_FORGE_NO_CACHE=1` 可全局关闭。
- 数据源兜底：TickFlow 不可用且标的为 A 股日/周/月 K 时自动降级 akshare（stderr 告警）；`ALPHA_FORGE_DATA_SOURCE=tickflow|akshare` 可强制单源。

### 稳健性验证（走步样本外 + PBO）

“寻优挑出来的漂亮曲线”到了新数据上还灵不灵？用 `run_validate.py`：

```bash
# 走步（walk-forward）样本外验证：滚动重寻优，只在样本外计价
uv run python run_validate.py --symbol 600000.SH --strategy ma_cross

# 加做 PBO（组合对称交叉验证）估计过拟合概率
uv run python run_validate.py --symbol AAPL.US --strategy macd --pbo --count 800
```

输出样本外净值/夏普 vs 基准、各走步折的选参与样本外收益，以及 PBO（>50% 意味过拟合风险高）。

### 风险管理与业绩归因

```bash
# 组合：单标的权重上限 + 风险报告（VaR/CVaR/溃疡指数）+ 收益归因
uv run python run_portfolio.py --symbols 600000.SH,000001.SZ,600519.SH \
    --strategy momentum --max-weight 0.5 --risk --attribution
```

### 因子研究（IC/IR/衰减/相关性）

```bash
# 在多因子选股基础上输出每个因子的 IC/IR、t 值、衰减与相关性矩阵
uv run python run_factor.py --symbols 600000.SH,000001.SZ,600519.SH,000858.SZ,600809.SH \
    --factors momentum,low_vol --ic
```

### 研究报告与结构化输出

```bash
# 自包含 HTML 研究报告（净值/回撤/月度收益/交易明细，单文件可直接交付）
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross --report

# 结构化 JSON（便于 agent 解析；不带值打印到 stdout，进度转 stderr；带路径则写入文件）
uv run python run_backtest.py --symbol AAPL.US --strategy macd --json > result.json
```

### CLI 通用约定（Agent 友好）

全部 13 个 `run_*.py` 遵循统一约定，便于脚本与 agent 编排：

- **`--help` 带示例**：每个命令的 `--help` 末尾附可直接复制的运行示例。
- **`--config <TOML>` 全覆盖**：配置文件注入默认值，显式命令行参数优先；未知键报错并给出近似建议与可用键列表。
- **`--json` 统一结构**：顶层固定含 `schema`/`command`/`generated_at` 元信息，字段只增不删；支持 `run_backtest` / `run_optimize` / `run_portfolio` / `run_compare` / `run_signal`；不带值时 stdout 保证纯 JSON（进度转 stderr）。
- **规范退出码**：0=成功，1=运行错误（数据/网络），2=参数错误，130=用户中断；失败信息以 `[error] ` 前缀输出 stderr，含可操作的修复建议（如标的代码格式、数据排查方向）。
- **输出命名规范**：图表/报告默认 `outputs/<命令>_<关键参数>.png|html`，同配置重跑才覆盖。
- **调试开关**：`ALPHA_FORGE_DEBUG=1` 可在出错时查看完整堆栈。

典型的 agent 流水线（对比→寻优→复跑、批量信号巡检、配置驱动批量实验）见
[references/use-cases.md](references/use-cases.md) 的「Agent 使用场景」章节。

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

用可插拔模型（LightGBM/Ridge/Logistic）学习技术指标特征与未来收益方向的关系，
**走步（walk-forward）重训练**并**只在样本外（OOS）段计价**，天然规避前视与未来数据泄露：

```bash
# 走步训练 + 样本外回测 + 出图（免费日 K 即可，无需 API Key）
uv run python run_ml.py --symbol 600000.SH --count 800 --plot

# 线性模型（macOS 无 libomp 也能跑）+ 置信度连续仓位
uv run python run_ml.py --symbol 600000.SH --model ridge --prob-sizing

# 更长历史、允许做空、调中性带阈值
uv run python run_ml.py --symbol AAPL.US --count 1000 --allow-short --threshold 0.08
```

报告中的净值/夏普均为样本外结果；`--model lgbm` 时默认加跑 Ridge 线性基线对照，
未跑赢基线会打印过拟合警告；夏普 > 3 会打印怀疑提示。详见 [references/ml-strategy.md](references/ml-strategy.md)。

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

### 信号服务与模拟盘（实盘前置）

回测验证过的策略，用信号服务每天看「该买该卖」，用模拟盘先用虚拟资金演练并追踪
与回测预期的偏差（**不做自动下单/券商对接**，输出仅供研究参考）：

```bash
# 每日信号：多标的批量，输出目标仓位与调仓动作（买入/卖出/持有/观望）
uv run python run_signal.py --symbols 600000.SH,600519.SH --strategy ma_cross --no-cache

# 模拟盘：虚拟资金纸面交易，状态持久化，同日重跑幂等；输出净值与回测预期偏差
uv run python run_paper.py --symbol 600000.SH --strategy ma_cross

# 重置模拟盘重新开始
uv run python run_paper.py --symbol 600000.SH --strategy ma_cross --reset
```

详见 [references/live-signal.md](references/live-signal.md)。

### 事件研究（AAR/CAAR）

给定事件日期列表（如财报日、政策日），统计事件窗内的平均异常收益（AAR）与累计平均异常收益（CAAR）：

```bash
# 两次财报日的事件反应（默认窗口 [-10, +20] 交易日）
uv run python run_event.py --symbol 600000.SH --events 2025-04-30,2025-08-30

# 相对指数基准的超额反应 + CAAR 曲线图
uv run python run_event.py --symbol 600519.SH --events 2025-04-25 \
    --benchmark 510300.SH --pre -5 --post 15 --plot
```

> 小样本事件研究噪声很大，事件数 < 10 时结论仅供参考。

## 典型用例

以下为常见工作流，完整命令与结果解读见 [references/use-cases.md](references/use-cases.md)：

| 场景 | 一句话做法 |
|------|----------|
| 单股策略选优 | `run_compare.py` 一键对比全部 9 个策略，按夏普排序 |
| 参数寻优→复跑 | `run_optimize.py` 找最优参（并行 + DSR 诊断），再 `run_backtest.py --params ... --plot` |
| 风控改善 | 同策略叠加 `--stop-loss` / `--vol-target` 对比回撤 |
| 多空对冲 | `--allow-short` 应对下跌或震荡市 |
| 组合轮动 | `run_portfolio.py` 比较 momentum/equal_weight/inverse_vol |
| 跨市场组合 | A股+美股+港股混合轮动分散风险 |
| 多因子选股 | `run_factor.py` 对股票池打分选股 + 分层验证 |
| 配对交易 | `run_pairs.py` 价差 z-score 做市场中性套利 |
| 机器学习 | `run_ml.py` 可插拔模型预测方向，走步样本外回测 + 线性基线对照 |
| 新闻情绪 | `run_sentiment.py --stage fetch` 抓新闻→agent 打分→`--stage backtest` |
| 定投定期定额 | `run_dca.py` 按周期定投，看资金加权 IRR 与一次性投入对比 |
| 组合优化 | `run_portfolio.py --strategy min_variance/max_sharpe` |
| 每日信号跟踪 | `run_signal.py` 批量输出目标仓位与调仓动作 |
| 模拟盘演练 | `run_paper.py` 虚拟资金纸面交易，追踪与回测预期的偏差 |
| 事件研究 | `run_event.py` 给定事件日算事件窗 AAR/CAAR（可选基准超额） |

## 注意事项

- 数据获取与回测脚本均在 `scripts/` 目录下用 `uv run python` 运行，首次需 `uv sync`。
- 所有 `--plot` 生成的回测图表统一输出到与 `scripts/` 平级的项目根目录 `outputs/` 目录（首次自动创建，已在 `.gitignore` 忽略）；文件名默认按关键参数自动命名以避免互相覆盖，如 `backtest_600000SH_ma_cross.png`、`portfolio_momentum_4syms.png`、`pairs_600000SH_601398SH.png`、`dca_600000SH_monthly.png`（相同配置重跑才覆盖）；可用 `--output <路径>` 自定义。
- SDK 支持 Python 3.9+，推荐 Python 3.10 或更高版本。
- 免费服务仅提供历史日 K 线；实时行情与分钟 K 线需配置 `TICKFLOW_API_KEY`。
- 支持 A 股、港股、美股、国内期货等多市场，标的代码可混合查询；`as_dataframe=True` 直接返回 pandas DataFrame。
- 机器学习（`run_ml.py`）与新闻情绪（`run_sentiment.py`）模块新增依赖 `lightgbm`、`scikit-learn`、`akshare`（已写入 `scripts/pyproject.toml`，`uv sync` 自动安装，会抬高安装体积）。macOS 上 LightGBM 还需 OpenMP 运行库，若报错 `libomp.dylib` 请 `brew install libomp`，或直接改用 `--model ridge/logistic`（scikit-learn 线性模型，无此依赖）。
- 新闻情绪采用 **agent-in-the-loop**：`--stage fetch` 抓新闻→agent（LLM）读 `../outputs/news_<标的>.csv` 逐条打分写入 `../outputs/sentiment_<标的>.csv`→`--stage backtest` 回测；无 agent 时可加 `--use-lexicon` 词典兜底（质量有限）。akshare 新闻仅约 100 条且仅 A 股，回测为短窗口演示。
- 回测结果不代表未来收益，参数寻优存在过拟合风险，建议用样本外数据验证；机器学习模块已内置走步样本外验证，任何策略**夏普比率 > 3 应优先怀疑**未来数据泄露或过拟合，而非视为策略有效。
- **交易保真度**：`--market astock` 计入卖出印花税与过户费；`--limit-board` 建模涨跌停/停牌不可成交；`--exec-price open` 次日开盘成交；`--adjust` 显式复权（默认前复权）。K 线本地缓存默认 1 天，`ALPHA_FORGE_NO_CACHE=1` 关闭、`ALPHA_FORGE_CACHE_DIR` 自定义目录（缓存写入项目根 `.cache/`，已忽略）。
- **稳健性验证优先**：`run_validate.py` 提供走步样本外 + PBO，`run_optimize.py` 打印 Deflated Sharpe Ratio；判断策略真伪应以样本外/DSR/PBO 为准，而非样本内指标。
- **CLI 统一约定**：全部 `run_*.py` 支持 `--config`（TOML 注入默认值）与 `--help` 示例段；退出码 0/1/2/130 规范化，错误统一 `[error] ` 前缀输出 stderr；`--json` 输出顶层固定含 `schema`/`command`/`generated_at`；排错可设 `ALPHA_FORGE_DEBUG=1` 看完整堆栈。详见 use-cases.md「Agent 使用场景」。
- **测试与 CI**：`cd scripts && uv sync --group dev && uv run pytest tests/ -q`；持续集成配置见 `.github/workflows/ci.yml`。新增模块：`data/`（缓存/复权/多数据源）、`research/`（走步/DSR/PBO/事件研究）、`risk/`（VaR/暴露/熔断/归因/压力测试）、`report/`（JSON/HTML/rich 终端）。
- **实盘前置能力边界**：`run_signal.py` / `run_paper.py` 仅输出信号与纸面记账，**不做任何自动化下单或券商对接**；输出仅供研究参考，不构成投资建议。模拟盘状态文件存于 `outputs/paper_<标的>_<策略>.json`，改 `--params` 后需 `--reset`。
