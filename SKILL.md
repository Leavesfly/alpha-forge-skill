---
name: alpha-forge-skill
description: A股/港股/美股量化研究与交易辅助。获取行情K线/财务数据，内置 14 个经典策略（双均线/MACD/RSI/布林/海龟/动量/网格等）+ 回测引擎 + 并行参数寻优（网格/随机/贝叶斯）+ 压力测试 + 多标的组合轮动与优化（含 HRP/最小CVaR）+ 多因子选股 + 配对交易 + 机器学习预测（含三重障碍标注/meta-labeling 信号过滤）+ 新闻情绪 + 定投DCA（含分红建模）+ 纪律评分决策层（能不能买/该不该卖/买多少）+ CAN SLIM 欧奈尔成长股清单（港美股基本面自动兜底）+ 市场状态识别 + 统一持仓账户 + 全市场扫描 + 每日信号（支持 webhook 推送）与模拟盘（含组合级总览）。对话式触发：“XX现在能买吗/值不值得买”“持仓该不该卖/减仓”“买多少合适”“帮我记一下持仓”“最近有什么值得买的”“这只股符不符合 CAN SLIM”“这只股用什么策略好”“帮我回测/调参/做组合/定投”“每天帮我盯盘看该买该卖”。全部 CLI 支持 --json 结构化输出，适合 Agent 程序化消费。
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
| [references/scoring.md](references/scoring.md) | 纪律评分与市场扫描：四层否决式评分、结论五态、ATR 交易计划、回放验证、事件风险降级、持仓联动 |
| [references/canslim.md](references/canslim.md) | CAN SLIM 检查清单：欧奈尔七项法则的纪律化核查（当季/年度 EPS 增长、新高、量能、相对强度、大势否决）与横截面 RS 排名 |
| [references/stress-testing.md](references/stress-testing.md) | 压力测试（历史情景重放 + 蒙特卡洛冲击）与 TOML 配置文件（--config） |
| [references/live-signal.md](references/live-signal.md) | 实盘前置：每日信号服务（run_signal）与模拟盘纸面交易 + 偏差追踪（run_paper） |
| [references/use-cases.md](references/use-cases.md) | 新手引导动线（Level 0→6 逐级上手）+ 端到端典型用例 + Agent 结构化调用指南（JSON 约定/退出码/批量实验） |
| [references/faq.md](references/faq.md) | Troubleshooting/FAQ：常见报错（API Key 缺失、libomp、标的格式、0 根 K 线、缓存陈旧等）与解决方案对照表 |
| `scripts/` | 可直接运行的回测工具代码（策略库、回测引擎、组合、CLI） |

## 对话意图路由（Agent 优先阅读）

本 Skill 主要在对话中被触发：用户用口语提需求，agent 路由到命令、执行、再用自然语言转述结果。
按用户话术对号入座（命令均在 `scripts/` 下 `uv run python` 执行，建议加 `--json` 取结构化结果）：

| 用户大致会说…… | 执行 | 转述时必须包含 |
|----------------|------|--------------|
| “XX 现在能买吗 / 值不值得入手 / 帮我看看 XX” | `run_score.py --symbol <代码> --json` | 结论五态中文（verdict_cn）+ 哪一层给出的理由 + 交易计划价位与建议仓位；必须说明这是纪律过滤而非涨跌预测 |
| “我持有 XX，成本 N，该不该卖/减仓” | `run_score.py --symbol <代码> --cost N --json` | 同上；「持仓需减风险」≠预测下跌，是风控纪律 |
| “帮我记一下持仓 / 我的持仓怎么样了” | 登记 `run_account.py --set --symbol <代码> --shares N --cost P`；查看 `run_account.py --json` | 持仓清单与浮盈亏；登记后 run_score/run_scan 自动联动（带入成本/标注已持有） |
| “最近有什么值得买的 / 帮我从这几只里挑一挑” | `run_scan.py --symbols <逗号列表> --json`（或 `--universe`，需 Key） | 达标/降级分列；建议对入选者再跑 run_score 复核 |
| “XX 符不符合 CAN SLIM / 用欧奈尔法则筛一筛” | `run_canslim.py --symbol <代码> --json`（多标的比较用 `--symbols`） | 七项通过/失败/不可评明细 + 结论；M（大势）不满足直接否；基本面缺失时诚实说明封顶「观察」 |
| “XX 用什么策略好 / 哪个策略适合 XX” | `run_compare.py --symbol <代码> --json` | 最优策略 + 夏普/回撤 + 是否跑赢 Buy&Hold；提示样本内选冠军有偏差 |
| “帮我回测一下 XX 的 YY 策略” | `run_backtest.py --symbol <代码> --strategy <策略> --json`（出图加 `--plot`） | 累计/年化收益、夏普、最大回撤，并与基准对比；回测不代表未来 |
| “帮我调参 / 找最优参数” | `run_optimize.py --symbol <代码> --strategy <策略> --json`（大网格加 `--method random`） | 最优参数 + DSR 诊断结论；DSR<90% 时必须提醒过拟合风险并建议 run_validate |
| “这策略靠谱吗 / 是不是过拟合” | `run_validate.py --symbol <代码> --strategy <策略> --pbo --json` | 样本外夏普 vs 样本内、PBO 概率；以样本外为准 |
| “这几只股票帮我做个组合” | `run_portfolio.py --symbols <列表> --strategy momentum --json` | 组合 vs 等权基准；调仓频率与成本假设 |
| “我想定投 XX” | `run_dca.py --symbol <代码> --json`（A 股可加 `--dividends` 显式建模分红） | XIRR 与一次性投入对比；定投价值在纪律而非必然更高收益 |
| “每天帮我盯着 XX 该买该卖” | 首次 `run_paper.py --symbol <代码> --mode score`（或 `--strategy <策略>`），以后每日重跑；只看信号用 `run_signal.py` | 今日动作 + 当前持仓/净值；不自动下单，仅纸面跟踪 |
| “拉一下 XX 的行情/K 线/财务” | 直接用 TickFlow SDK（见 [references/data-fetching.md](references/data-fetching.md)） | 数据口径（复权/周期） |
| “出个报告给我” | 在对应命令加 `--report`（HTML）或 `--plot`（图） | 文件路径（outputs/ 下） |
| “帮我看看今天整体情况 / 出个总览” | `run_dashboard.py`（可加 `--symbols` 附信号） | Dashboard HTML 路径 + 风控提示摘要 |
| 命令报错 / 环境不确定 | `run_list.py --doctor`；再查 [references/faq.md](references/faq.md) | 失败项与修复建议 |

标的代码需补全市场后缀（600519 → `600519.SH`，腾讯 → `00700.HK`，苹果 → `AAPL.US`）；
用户只说公司名时先推断代码，无法确定则向用户确认。

### Agent 执行与转述守则

1. **取数用 `--json`，回答用自然语言**：stdout 是纯 JSON（进度在 stderr）；不要把原始 JSON 或完整指标表直接粘给用户，提炼结论 + 2–3 个关键数字即可。
2. **转述三要素**：结论（干不干，为什么）、关键证据（与基准对比后的夏普/回撤，或评分分层理由）、局限性（回测不代表未来/评分非预测/不构成投资建议）。涉及买卖建议性质的输出，免责声明不可省略。
3. **异常自愈不盲重试**：退出码 2 = 参数错（stderr 已附近似候选建议，改参重跑）；退出码 1 = 运行错，先 `run_list.py --doctor` 定位，再对照 faq.md；同一命令失败两次后应向用户说明而非继续重试。
4. **多轮会话接住上下文**：模拟盘状态在 `outputs/paper_<标的>_<策略>.json`（同日重跑幂等，改参需 `--reset`）；情绪交易是 fetch→agent 打分→backtest 三步；反复实验同一口径建议写 TOML 用 `--config`。
5. **链式引导**：每个命令的 `--json` 输出含 `next_steps` 字段（结构化后续动作），据此向用户提议下一步（如寻优后建议样本外验证）。
6. **按需加载文档**：本文件足以路由与执行；仅当需要参数细节、原理解释或结果深度解读时再读对应 references 文档，不要一次性全部加载。

### 首次用户引导协议

当用户首次使用或问“你能做什么 / 怎么用”时，**不要输出功能清单**，而是用三选一引导：

> “我可以帮你：
> ① 看一只股票现在能不能买（纪律评分，10 秒出结论）
> ② 回测一个策略的历史表现（看看过去赚不赚钱）
> ③ 制定定投计划（每月定额，看长期收益）
> 你想先试哪个？或者直接告诉我你关心哪只股票。”

用户选择后立即执行对应命令，不再追问。

### 模糊意图消歧

用户话术不明确时，按以下优先级处理：

| 用户说 | 判断策略 |
|---------|----------|
| “帮我分析一下 XX” / “看看 XX” | 默认走 `run_score.py`（最轻量的“看一眼”） |
| “XX 怎么样” / “XX 值得入手吗” | 同上，评分结论先行 |
| “帮我研究一下 XX” / “XX 用什么策略” | 走 `run_compare.py`（多策略对比） |
| “帮我理财” / “推荐一下” | 先问目的（想买/想研究/想定投），再路由 |
| 无法判断 | 主动问一句：“你是想看它现在能不能买，还是想研究它的历史表现？” |

### 转述深度分级

根据用户意图和追问程度，选择不同转述深度：

| 级别 | 触发场景 | 转述内容 |
|------|----------|----------|
| 一句话 | 用户问“能买吗”“怎么样” | 结论 + 1 个关键理由 + 免责 |
| 摘要 | 用户问“表现如何”“回测一下” | 结论 + 3 个数字（收益/夏普/回撤）+ 与基准对比 |
| 详解 | 用户追问“为什么”“详细说说” | 分层展开 + 图表路径 + 下一步建议 |

### 指标白话翻译表

转述时将技术指标翻译为用户能理解的语言：

| 指标 | 白话翻译 |
|------|----------|
| 夏普比率 1.5 | “每承担 1% 的波动风险，能多赚 1.5% 的超额收益” |
| 最大回撤 -20% | “最坏情况下从高点到低点会亏 20%” |
| 年化收益 12% | “平均每年赚 12%（复利）” |
| 胜率 60% | “10 次交易大约 6 次赚钱” |
| XIRR 8% | “考虑投入时点后，实际年化收益 8%” |
| DSR < 90% | “这组参数很可能是运气好，换个时间段可能就不灵了” |
| PBO > 50% | “过拟合概率超半数，样本内好看但实盘大概率不行” |

### 链式引导模板

每个命令的 `--json` 输出已含 `next_steps` 字段，Agent 应据此主动提议（而非等用户问）：

| 当前命令 | 典型引导话术 |
|----------|----------------|
| run_score | “要不要我帮你盯着它？每天告诉你该买该卖。”（→ paper） |
| run_backtest | “要不要试试其他策略对比一下，看哪个更适合？”（→ compare） |
| run_optimize | “最优参数的可靠性需要验证，要跑一下样本外吗？”（→ validate） |
| run_compare | “冠军策略可能有偏差，建议做个样本外验证。”（→ validate） |
| run_validate | “策略验证通过，要开始纸面跟踪吗？”（→ paper） |
| run_scan | “达标候选建议逐个复核，要看第一名的详细评分吗？”（→ score） |
| run_dca | “要不要试试智能定投模式，看能否提升收益？”（→ smart） |

### 优雅降级与预判式错误规避

当功能受限或可预判失败时，主动提供替代方案而非纯报错：

| 场景 | 降级话术 |
|------|----------|
| 用户要分钟 K 线但无 API Key | “分钟 K 线需要 API Key，不过用日 K 线也能看到趋势，要试试吗？” |
| 新股/次新股 K 线不足 | “这只股票上市不到一年，评分可能不准，我可以帮你做个回测看看历史表现。” |
| 用户说公司名无法确定代码 | “你说的是 XX 还是 YY？请确认一下股票代码。” |
| 财务因子无权限 | “财务因子需要 API Key，我先用价格因子（动量+低波）帮你分析。” |
| 命令失败两次 | 停止重试，向用户说明失败原因并建议 `run_list.py --doctor` 自检 |

### JSON 输出新增字段（Agent 程序化消费）

全部 20 个 `run_*.py` 的 `--json` 输出现已包含两个 Agent 友好字段：

- **`summary`**：1–2 句自然语言结论，Agent 可直接引用或改写后转述给用户。
- **`next_steps`**：结构化后续动作列表，每项含 `action`（动作标识）、`reason`（为何建议）、`command`（可执行命令）。Agent 据此程序化链式引导，无需解析 stderr 文本。

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

### 60 秒上手：按目的选一条路径

三类典型目的各一条命令直达（`scripts/` 下运行，免费日 K 即可）：

```bash
# ① 知道这只股票现在能不能买（结论先行：是/观察/否 + 交易计划）
uv run python run_score.py --symbol 600000.SH

# ② 研究一个策略的历史表现（回测 + 出图）
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross --plot

# ③ 定期定额定投一只标的（XIRR 与一次性投入对比）
uv run python run_dca.py --symbol 600000.SH --plot
```

环境有问题先跑 `uv run python run_list.py --doctor`（逐项自检 + 修复建议）；
新手逐级上手动线见 [references/use-cases.md](references/use-cases.md)。

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

### 内置策略（14 个）

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
| `keltner` | 肯特纳通道 | EMA 中轨 ± ATR 倍数通道，突破上轨做多，回落中轨平仓 |
| `supertrend` | SuperTrend | ATR 追踪止损线，价格在线上做多、线下离场 |
| `dual_thrust` | Dual Thrust | 开盘价 ± K×区间幅度突破做多/做空 |
| `cci` | CCI 顺势 | CCI 低于 -100 买入，回升过 +100 卖出 |
| `williams_r` | 威廉指标 | WR 超卖（<-80）买入，超买（>-20）卖出 |

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

# 半 Kelly 连续仓位（仓位 = 信号 × 0.5μ/σ²，滚动估计，与 --vol-target 互斥）
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross --kelly

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

# 随机搜索：大参数空间只采样 40 组（更快，且试验数少 → 多重检验惩罚更轻）
uv run python run_optimize.py --symbol 600000.SH --strategy turtle --method random --n-iter 40

# 贝叶斯搜索（TPE 风格）：同预算下自适应聚焦高潜力参数区，通常优于随机
uv run python run_optimize.py --symbol 600000.SH --strategy turtle --method bayes --n-iter 40
```

> 寻优结束后会打印 **Deflated Sharpe Ratio（DSR）** 过拟合诊断：对“试了多少组参数”做惩罚后，最优参数是否仍显著。DSR < 90% 应高度警惕。

### 多策略对比

同一标的一条命令对比多个策略（缺省全部 14 个），并排绩效表 + 净值叠加图 + HTML 对比报告：

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
- K 线数据已本地缓存（默认 1 天），重复回测/寻优不再反复走网络；陈旧缓存走**增量更新**（只拉尾部小段合并，重叠区复权一致性校验，不一致自动全量重拉；`ALPHA_FORGE_INCR_CACHE=0` 关闭）；环境变量 `ALPHA_FORGE_NO_CACHE=1` 可全局关闭缓存。
- 数据源兜底：TickFlow 不可用时自动降级（stderr 告警）——A 股日/周/月 K 走 baostock → akshare，港股/美股日/周/月 K 走 yfinance；`ALPHA_FORGE_DATA_SOURCE=tickflow|baostock|akshare|yfinance` 可强制单源。

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

全部 20 个 `run_*.py` 遵循统一约定，便于脚本与 agent 编排：

- **`--help` 带示例**：每个命令的 `--help` 末尾附可直接复制的运行示例。
- **`--config <TOML>` 全覆盖**：配置文件注入默认值，显式命令行参数优先；未知键报错并给出近似建议与可用键列表。
- **`--json` 全命令支持**：顶层固定含 `schema`/`command`/`generated_at` 元信息，字段只增不删；全部 20 个命令（backtest/optimize/compare/portfolio/signal/dca/score/scan/canslim/ml/pairs/factor/validate/sentiment/paper/event/list/account/dashboard/verify）均已支持；不带值时 stdout 保证纯 JSON（进度转 stderr）。
- **`run_list.py` 能力清单**：一条命令列出全部策略（含默认参数与参数网格）、轮动策略、因子、ML 模型与定投模式，`--json` 供 agent 发现能力：`uv run python run_list.py --json`。
- **`run_dashboard.py` 统一 Dashboard**：聚合真实持仓 + 全部模拟盘 + 可选今日信号于一页自包含 HTML（`--symbols` 附信号，`--json` 结构化）；含集中度/回撤风控提示。
- **规范退出码**：0=成功，1=运行错误（数据/网络），2=参数错误，130=用户中断；失败信息以 `[error] ` 前缀输出 stderr，含可操作的修复建议（如标的代码格式、数据排查方向）；非法策略参数组合（如 fast>=slow）会在启动期报友好错误。
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

除单标的策略外，还支持多标的组合回测与截面轮动（动量轮动 `momentum`、等权 `equal_weight`、风险平价 `inverse_vol`，及优化类 `min_variance`/`max_sharpe`/`hrp`/`min_cvar`）：

```bash
# 截面动量轮动（持有涨幅前 2 名，每 20 日调仓）
uv run python run_portfolio.py --symbols 600000.SH,000001.SZ,600519.SH,000858.SZ --strategy momentum --top-k 2

# 风险平价 + 出图
uv run python run_portfolio.py --symbols AAPL.US,MSFT.US,TSLA.US --strategy inverse_vol --plot
```

组合回测会与**等权基准**对比，详见 [references/portfolio.md](references/portfolio.md)。

### 多因子选股

对股票池按五类因子（价值/质量/规模/动量/波动率）打分选股，并用分层回测验证因子有效性；
价格类因子除动量/低波外还有短期反转（reversal）、风险调整动量（sharpe_mom）与趋势一致性（consistency）：

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

# 组合优化：最小方差 / 最大夏普 / HRP 层次风险平价 / 最小 CVaR
uv run python run_portfolio.py --symbols AAPL.US,MSFT.US,TSLA.US,AMZN.US,GOOGL.US --strategy min_variance
uv run python run_portfolio.py --symbols AAPL.US,MSFT.US,TSLA.US,AMZN.US --strategy hrp
uv run python run_portfolio.py --symbols 600000.SH,600519.SH,000858.SZ --strategy min_cvar
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

# 三重障碍标签：止盈/止损/最长持有期先触发者定标签，更贴近真实交易
uv run python run_ml.py --symbol 600000.SH --label triple --pt-mult 2 --sl-mult 1

# meta-labeling：二级模型过滤一级策略（如 ma_cross）的假信号，对比过滤前后 OOS 绩效
uv run python run_ml.py --symbol 600000.SH --meta ma_cross --count 800
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

# 显式分红建模：自动拉 A 股分红历史，不复权价 + 分红再投入（--div-policy cash 为现金落袋）
uv run python run_dca.py --symbol 600000.SH --dividends --div-policy reinvest
```

> 先比 **IRR vs 基准 A（纯定投）** 判断加码/择时是否真的有正贡献，再比 **基准 B（一次性投入）**。详见 [references/dca.md](references/dca.md)。

### 纪律评分与市场扫描（决策层）

回答「**现在是否适合参与**」：四层否决式评分（ALPHA 加权 → 风险否决 → 技术确认 →
入场时机，单向降级、利好不加分），输出结论五态（是/观察/否/持仓需减风险/无法评分）、
入场/止损/2R/3R 交易计划价位与**建议仓位**（风险预算法：股数 = 资金×风险比例/R，
回答「买多少」）；同时输出**市场状态**（趋势/震荡/高波动，描述性上下文）；
A股/港股/美股基准自动选择（510300.SH / 02800.HK / SPY.US）：

```bash
# 单股评分（免费日 K 即可）；--brief 只要结论与价位；--capital/--risk-pct 控制建议仓位
uv run python run_score.py --symbol 600000.SH
uv run python run_score.py --symbol AAPL.US --brief
uv run python run_score.py --symbol 600000.SH --capital 200000 --risk-pct 0.02

# 事件风险三步闭环：抓新闻素材生成待标注模板 → agent 填 risk 列 → --risk-file 回传
uv run python run_score.py --symbol 600000.SH --fetch-events

# 历史回放验证：逐日重算结论 + 21/63 日前瞻收益事件研究（样本不足诚实标注 inconclusive）
uv run python run_score.py --symbol 600519.SH --count 800 --replay --plot

# 阈值自校准：回放驱动网格搜索最优 alpha_score 入场阈值（胜率/平均前瞻收益）
uv run python run_score.py --symbol 600519.SH --count 800 --calibrate --calibrate-horizon 21

# 结合持仓成本（结论可能变为「持仓需减风险」；缺省自动探测账户/模拟盘持仓）
uv run python run_score.py --symbol 600000.SH --cost 8.50 --shares 1000

# 全市场扫描：流动性初筛 → 批量评分 → 达标/降级候选分列
uv run python run_scan.py --symbols 600000.SH,600519.SH,000858.SZ,AAPL.US
uv run python run_scan.py --universe CN_Equity_A --limit 100 --pool 30 --top 10 --json

# 按评分裁决纸面执行（决策→跟踪闭环：是=建仓、否/减风险=离场、观察=持有）
uv run python run_paper.py --symbol 600000.SH --mode score
```

> 评分是纪律工具而非收益预测，阈值未经样本外验证（`--replay` 可自证）；事件风险只降级不加分（`--fetch-events` 抓素材→agent 标注→`--risk-file` 回传）。详见 [references/scoring.md](references/scoring.md)。

### CAN SLIM 检查清单（欧奈尔成长股法则）

七项纪律化核查：C 当季EPS增长 / A 年度EPS复合增长 / N 新高 / S 量能供求 /
L 相对强度 / I 机构认同（无免费数据源，诚实标注）/ M 市场方向（否决项）。
A 股自动拉季度 EPS/ROE（akshare 免 Key），港美股自动用 yfinance 利润表兜底
（财年口径），基本面缺失时结论封顶「观察」：

```bash
# 单标的七项详评（免费日 K 即可）
uv run python run_canslim.py --symbol 600519.SH

# 多标的横截面：L 用 RS 百分位（≥70 通过），按通过数/RS 排名
uv run python run_canslim.py --symbols 600519.SH,000858.SZ,300750.SZ

# 阈值本土化 + 港美股基本面（缺省 yfinance 自动兜底，离线可 CSV 注入）
uv run python run_canslim.py --symbol 300750.SZ --c-growth 0.15 --a-growth 0.15
uv run python run_canslim.py --symbol AAPL.US
uv run python run_canslim.py --symbol AAPL.US --fundamentals-csv aapl_eps.csv
```

> M（大势）不满足直接「否」——欧奈尔纪律为大势不对不买；阈值为原著预设未经 A 股样本外验证。详见 [references/canslim.md](references/canslim.md)。

### 统一持仓账户（跨命令联动）

真实持仓登记在 `outputs/account.json`，各命令自动联动（仅登记，不做交易执行）：

```bash
# 登记/更新持仓（同标的重复 --set 即更新）
uv run python run_account.py --set --symbol 600000.SH --shares 1000 --cost 8.50

# 查看账户（默认拉最新收盘价算浮盈亏；--no-quote 离线）
uv run python run_account.py

# 移除持仓
uv run python run_account.py --remove --symbol 600000.SH
```

联动效果：`run_score.py` 未传 `--cost` 时自动带入账户成本给操作建议（优先级：显式
`--cost` > 账户登记 > 模拟盘探测）；`run_scan.py` 对已持标的标注「已持有」，
`--exclude-held` 可直接排除。

### 信号服务与模拟盘（实盘前置）

回测验证过的策略，用信号服务每天看「该买该卖」，用模拟盘先用虚拟资金演练并追踪
与回测预期的偏差（**不做自动下单/券商对接**，输出仅供研究参考）：

```bash
# 每日信号：多标的批量，输出目标仓位与调仓动作（买入/卖出/持有/观望）
uv run python run_signal.py --symbols 600000.SH,600519.SH --strategy ma_cross --no-cache

# 信号推送到 webhook（钉钉/企微/飞书机器人自动适配；配合 cron 每日定时，只在有买卖动作时才推）
uv run python run_signal.py --symbols 600000.SH --strategy ma_cross \
    --notify https://oapi.dingtalk.com/robot/send?access_token=xxx --notify-only-changes

# 模拟盘：虚拟资金纸面交易，状态持久化，同日重跑幂等；输出净值与回测预期偏差
uv run python run_paper.py --symbol 600000.SH --strategy ma_cross

# 模拟盘·纪律评分模式：按评分结论纸面执行（无需 --strategy，记录每日裁决历史）
uv run python run_paper.py --symbol 600000.SH --mode score

# 组合级总览：聚合全部模拟盘 → 账户级净值/权重/集中度与回撤告警
uv run python run_paper.py --summary

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
| 单股策略选优 | `run_compare.py` 一键对比全部 14 个策略，按夏普排序 |
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
| 单股纪律评分 | `run_score.py` 四层否决式评分，结论 + 交易计划价位 + 建议仓位，`--replay` 回放验证 |
| CAN SLIM 清单核查 | `run_canslim.py` 欧奈尔七项法则逐项核查，多标的横截面 RS 百分位排名 |
| 持仓登记与体检 | `run_account.py --set` 登记持仓，run_score/run_scan 自动联动（带入成本/标注已持有） |
| 市场扫描选候选 | `run_scan.py` 流动性初筛 + 批量评分，达标/降级候选分列 |
| 组合优化 | `run_portfolio.py --strategy min_variance/max_sharpe` |
| 每日信号跟踪 | `run_signal.py` 批量输出目标仓位与调仓动作 |
| 模拟盘演练 | `run_paper.py` 虚拟资金纸面交易，追踪与回测预期的偏差 |
| 事件研究 | `run_event.py` 给定事件日算事件窗 AAR/CAAR（可选基准超额） |
| 能力发现 | `run_list.py --json` 一条命令列出全部策略/因子/模型/模式清单 |

## 注意事项

- 数据获取与回测脚本均在 `scripts/` 目录下用 `uv run python` 运行，首次需 `uv sync`。
- 所有 `--plot` 生成的回测图表统一输出到与 `scripts/` 平级的项目根目录 `outputs/` 目录（首次自动创建，已在 `.gitignore` 忽略）；文件名默认按关键参数自动命名以避免互相覆盖，如 `backtest_600000SH_ma_cross.png`、`portfolio_momentum_4syms.png`、`pairs_600000SH_601398SH.png`、`dca_600000SH_monthly.png`（相同配置重跑才覆盖）；可用 `--output <路径>` 自定义。
- SDK 支持 Python 3.9+，推荐 Python 3.10 或更高版本。
- 免费服务仅提供历史日 K 线；实时行情与分钟 K 线需配置 `TICKFLOW_API_KEY`。
- 支持 A 股、港股、美股、国内期货等多市场，标的代码可混合查询；`as_dataframe=True` 直接返回 pandas DataFrame。
- 机器学习（`run_ml.py`）与新闻情绪（`run_sentiment.py`）模块新增依赖 `lightgbm`、`scikit-learn`、`akshare`（已写入 `scripts/pyproject.toml`，`uv sync` 自动安装，会抬高安装体积）。macOS 上 LightGBM 还需 OpenMP 运行库，若报错 `libomp.dylib` 请 `brew install libomp`，或直接改用 `--model ridge/logistic`（scikit-learn 线性模型，无此依赖）。
- 新闻情绪采用 **agent-in-the-loop**：`--stage fetch` 抓新闻→agent（LLM）读 `../outputs/news_<标的>.csv` 逐条打分写入 `../outputs/sentiment_<标的>.csv`→`--stage backtest` 回测；无 agent 时可加 `--use-lexicon` 词典兜底（质量有限）。akshare 新闻仅约 100 条且仅 A 股，回测为短窗口演示。
- 回测结果不代表未来收益，参数寻优存在过拟合风险，建议用样本外数据验证；机器学习模块已内置走步样本外验证，任何策略**夏普比率 > 3 应优先怀疑**未来数据泄露或过拟合，而非视为策略有效。
- **交易保真度**：`--market astock` 计入卖出印花税与过户费；`--limit-board` 建模涨跌停/停牌不可成交；`--exec-price open` 次日开盘成交；`--adjust` 显式复权（默认前复权）。K 线本地缓存 TTL 按周期分级：日线及以上默认 1 天、分钟线默认 30 分钟；`ALPHA_FORGE_CACHE_TTL`（秒）显式设置时全局覆盖，`ALPHA_FORGE_NO_CACHE=1` 关闭、`ALPHA_FORGE_CACHE_DIR` 自定义目录（缓存写入项目根 `.cache/`，已忽略）。
- **网络健壮性**：单数据源拉取失败先自动重试（默认 2 次，退避 1s/2s，stderr 告警），重试仍失败才降级下一源（A 股：baostock → akshare；港美股：yfinance）；环境变量 `ALPHA_FORGE_RETRIES` 可调重试次数（0 关闭）。拉取失败但存在过期缓存时自动回退使用过期缓存。
- **稳健性验证优先**：`run_validate.py` 提供走步样本外 + PBO，`run_optimize.py` 打印 Deflated Sharpe Ratio；判断策略真伪应以样本外/DSR/PBO 为准，而非样本内指标。
- **CLI 统一约定**：全部 `run_*.py` 支持 `--config`（TOML 注入默认值）、`--json`（结构化输出）与 `--help` 示例段；退出码 0/1/2/130 规范化，错误统一 `[error] ` 前缀输出 stderr；`--json` 输出顶层固定含 `schema`/`command`/`generated_at`；`run_list.py --json` 可查询能力清单；排错可设 `ALPHA_FORGE_DEBUG=1` 看完整堆栈。常见报错与解决方案见 [references/faq.md](references/faq.md)。详见 use-cases.md「Agent 使用场景」。
- **测试与 CI**：`cd scripts && uv sync --group dev && uv run pytest tests/ -q`；持续集成配置见 `.github/workflows/ci.yml`。新增模块：`data/`（缓存/复权/多数据源）、`research/`（走步/DSR/PBO/事件研究）、`risk/`（VaR/暴露/熔断/归因/压力测试）、`report/`（JSON/HTML/rich 终端）。
- **实盘前置能力边界**：`run_signal.py` / `run_paper.py` 仅输出信号与纸面记账，**不做任何自动化下单或券商对接**；输出仅供研究参考，不构成投资建议。模拟盘状态文件存于 `outputs/paper_<标的>_<策略>.json`，改 `--params` 后需 `--reset`。
