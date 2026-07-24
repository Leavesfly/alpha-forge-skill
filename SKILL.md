---
name: alpha-forge-skill
description: A股/港股/美股量化研究与交易辅助：行情/财务数据获取、内置策略与自定义 TOML 规则回测、参数寻优与样本外验证、组合轮动优化、多因子选股、配对交易、机器学习预测、新闻情绪、定投 DCA、纪律评分决策（能不能买/该不该卖/买多少）、CAN SLIM 清单、低估值全市场筛选、每日信号与模拟盘、统一持仓账户。对话式触发："XX现在能买吗""持仓该不该卖""买多少合适""帮我回测/调参/做组合/定投""最近有什么值得买的""有没有低估值的股票""这只股符不符合CAN SLIM""每天帮我盯盘"。全部 CLI 支持 --json 结构化输出，适合 Agent 程序化消费。
compatibility: Requires Python 3.10+, uv, and network access; optional TICKFLOW_API_KEY for realtime/minute data
metadata: {"clawdbot":{"emoji":"📈","homepage":"https://tickflow.org","requires":{"bins":["python3","uv"],"env":["TICKFLOW_API_KEY"]}}}
---

# Alpha Forge Skill

通过 TickFlow Python SDK 获取 A 股、港股、美股、期货等市场的行情、K 线与财务数据，
并内置经典量化策略、回测引擎与纪律评分决策层。本文件足以完成意图路由与命令执行；
参数细节、原理解释与结果深度解读按需查阅 references/ 对应文档。

## 能力导航

| 资源 | 用途 |
|------|------|
| [references/data-fetching.md](references/data-fetching.md) | 数据获取：标的代码格式、行情/K线/财务 SDK 示例、K 线周期、数据源兜底与缓存 |
| [references/strategies.md](references/strategies.md) | 内置单标的策略的原理、参数与信号逻辑 + 自定义规则 DSL（TOML 格式/指标白名单） |
| [references/backtesting.md](references/backtesting.md) | 回测引擎（含账本引擎）、绩效指标、参数寻优、多策略对比、交易保真度、走步样本外验证（PBO/DSR）、事件研究 |
| [references/portfolio.md](references/portfolio.md) | 多标的组合回测、截面轮动、组合优化（含 HRP/最小CVaR）、风险报告与业绩归因 |
| [references/multi-factor.md](references/multi-factor.md) | 多因子选股：因子打分合成、分位选股、分层回测、IC/IR 因子研究 |
| [references/pairs-trading.md](references/pairs-trading.md) | 配对交易：市场中性统计套利，价差 z-score 开平仓 |
| [references/ml-strategy.md](references/ml-strategy.md) | 机器学习策略：特征/标签（含三重障碍）、可插拔模型、走步样本外验证、meta-labeling |
| [references/sentiment.md](references/sentiment.md) | 新闻情绪交易：抓新闻 → agent（LLM）情绪打分 → 情绪信号回测 |
| [references/dca.md](references/dca.md) | 定投 DCA：现金流账本、XIRR、智能定投/超跌加码/价值平均、分红建模、双基准对比 |
| [references/scoring.md](references/scoring.md) | 纪律评分与市场扫描：分层否决式评分、结论五态、交易计划与建议仓位、估值分位/宏观环境、回放验证、持仓账户与风险画像联动、低估值筛选（run_screener） |
| [references/canslim.md](references/canslim.md) | CAN SLIM 检查清单：欧奈尔七项法则纪律化核查与横截面 RS 排名 |
| [references/stress-testing.md](references/stress-testing.md) | 压力测试（历史情景重放 + 蒙特卡洛冲击）与 TOML 配置文件（--config） |
| [references/live-signal.md](references/live-signal.md) | 每日信号服务（含 webhook 推送）与模拟盘纸面交易 + 偏差追踪 |
| [references/use-cases.md](references/use-cases.md) | 新手引导动线 + 端到端典型用例 + Agent 结构化调用指南（JSON 约定/退出码/批量实验） |
| [references/faq.md](references/faq.md) | 常见报错（API Key、libomp、标的格式、缓存陈旧等）与解决方案、调试环境变量 |
| `scripts/` | 可直接运行的 CLI 工具与策略/回测/组合代码 |

## 对话意图路由（Agent 优先阅读）

本 Skill 主要在对话中被触发：用户用口语提需求，agent 路由到命令、执行、再用自然语言转述结果。
按用户话术对号入座（命令均在 `scripts/` 下 `uv run python` 执行，建议加 `--json` 取结构化结果）：

| 用户大致会说…… | 执行 | 转述时必须包含 |
|----------------|------|--------------|
| “XX 现在能买吗 / 值不值得入手 / 帮我看看 XX” | `run_score.py --symbol <代码> --json` | 结论五态中文（verdict_cn）+ 哪一层给出的理由 + 交易计划价位与建议仓位；必须说明这是纪律过滤而非涨跌预测 |
| “我持有 XX，成本 N，该不该卖/减仓” | `run_score.py --symbol <代码> --cost N --json` | 同上；「持仓需减风险」≠预测下跌，是风控纪律 |
| “帮我记一下持仓 / 我的持仓怎么样了” | 登记 `run_account.py --set --symbol <代码> --shares N --cost P`；查看 `run_account.py --json` | 持仓清单与浮盈亏；登记后 run_score/run_scan 自动联动（带入成本/标注已持有） |
| “我是保守型/平衡型/激进型投资者 / 记住我的风险偏好 / 我只有20万” | `run_profile.py --set --risk-tolerance <档位> --capital N --json` | 画像登记结果；说明后续 run_score 的建议仓位会因人而异（显式参数优先） |
| "最近有什么值得买的 / 帮我从这几只里挑一挑" | `run_scan.py --symbols <逗号列表> --json`（或 `--universe`，需 Key） | 达标/降级分列；建议对入选者再跑 run_score 复核 |
| "有没有低估值的股票 / 高分红的 / 便宜又好的" | `run_screener.py --json`（A 股全市场默认）或 `--symbols <列表>`（港美股） | 达标候选排名 + 关键估值指标；建议对候选跑 run_score 做技术面复核 |
| "帮我找潜在十倍股 / 十倍成长股 / multibagger" | `run_screener.py --preset multibagger --json` | 十倍股统计特征候选；须声明是统计共性非预测，建议接 run_canslim 交叉确认 + run_portfolio 组合持有 |
| “XX 符不符合 CAN SLIM / 用欧奈尔法则筛一筛” | `run_canslim.py --symbol <代码> --json`（多标的比较用 `--symbols`） | 七项通过/失败/不可评明细 + 结论；M（大势）不满足直接否；基本面缺失时诚实说明封顶「观察」 |
| “XX 用什么策略好 / 哪个策略适合 XX” | `run_compare.py --symbol <代码> --json` | 最优策略 + 夏普/回撤 + 是否跑赢 Buy&Hold；提示样本内选冠军有偏差 |
| “帮我回测一下 XX 的 YY 策略” | `run_backtest.py --symbol <代码> --strategy <策略> --json`（出图加 `--plot`） | 累计/年化收益、夏普、最大回撤，并与基准对比；回测不代表未来 |
| “我想自己定义一个策略：金叉且 RSI 不过热时买……” | agent 按用户描述生成 TOML 规则文件（格式见 `examples/custom_rule.toml`，指标/运算符白名单用 `run_list.py --json` 查），再 `run_custom.py --symbol <代码> --rules <文件> --json` | 规则如何被解析（入场/离场条件）+ 回测结果 vs 基准；提醒自定义规则未经样本外验证 |
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

| 用户说 | 判断策略 |
|---------|----------|
| “帮我分析一下 XX” / “看看 XX” / “XX 怎么样” | 默认走 `run_score.py`（最轻量的“看一眼”，评分结论先行） |
| “帮我研究一下 XX” / “XX 用什么策略” | 走 `run_compare.py`（多策略对比） |
| “帮我理财” / “推荐一下” | 先问目的（想买/想研究/想定投），再路由 |
| 无法判断 | 主动问一句：“你是想看它现在能不能买，还是想研究它的历史表现？” |

### 转述深度分级

| 级别 | 触发场景 | 转述内容 |
|------|----------|----------|
| 一句话 | 用户问“能买吗”“怎么样” | 结论 + 1 个关键理由 + 免责 |
| 摘要 | 用户问“表现如何”“回测一下” | 结论 + 3 个数字（收益/夏普/回撤）+ 与基准对比 |
| 详解 | 用户追问“为什么”“详细说说” | 分层展开 + 图表路径 + 下一步建议 |

### 指标白话翻译表

| 指标 | 白话翻译 |
|------|----------|
| 夏普比率 1.5 | “每承担 1% 的波动风险，能多赚 1.5% 的超额收益” |
| 最大回撤 -20% | “最坏情况下从高点到低点会亏 20%” |
| 年化收益 12% | “平均每年赚 12%（复利）” |
| 胜率 60% | “10 次交易大约 6 次赚钱” |
| XIRR 8% | “考虑投入时点后，实际年化收益 8%” |
| DSR < 90% | “这组参数很可能是运气好，换个时间段可能就不灵了” |
| PBO > 50% | “过拟合概率超半数，样本内好看但实盘大概率不行” |

### JSON 输出 Agent 字段与链式引导

全部 `run_*.py` 的 `--json` 输出包含两个 Agent 友好字段：

- **`summary`**：1–2 句自然语言结论，可直接引用或改写后转述给用户。
- **`next_steps`**：结构化后续动作列表，每项含 `action`/`reason`/`command`；部分项含可选
  `condition`（如 `"dsr.dsr < 0.9"`，引用同一 JSON 输出的字段点路径），**仅当条件成立时才应提议该动作**。
  Agent 应据此主动提议下一步（如：run_backtest 后提议 compare 对比、run_optimize 后提议
  validate 样本外验证、run_scan/run_screener 后提议对第一名跑 score 复核、run_score 后提议 paper 纸面跟踪）。

此外 `run_score.py --json` 输出含 **`evidence`** 结构化证据链：每条证据含 `id`（E01/E02…）、
`indicator`、`value`、`threshold`、`triggered`、`impact` 与 `claim`（一句话断言）。
**Agent 深度解读评分时应引用证据而非自行推断**（如「收盘低于 MA200（E02）所以被否决」），从机制上避免事实性错误。

### 优雅降级与预判式错误规避

当功能受限或可预判失败时，主动提供替代方案而非纯报错：

| 场景 | 降级话术 |
|------|----------|
| 用户要分钟 K 线但无 API Key | “分钟 K 线需要 API Key，不过用日 K 线也能看到趋势，要试试吗？” |
| 新股/次新股 K 线不足 | “这只股票上市不到一年，评分可能不准，我可以帮你做个回测看看历史表现。” |
| 用户说公司名无法确定代码 | “你说的是 XX 还是 YY？请确认一下股票代码。” |
| 财务因子无权限 | “财务因子需要 API Key，我先用价格因子（动量+低波）帮你分析。” |
| 命令失败两次 | 停止重试，向用户说明失败原因并建议 `run_list.py --doctor` 自检 |

## 环境配置

```bash
# 1. 安装 uv（如未安装；Windows 用 irm https://astral.sh/uv/install.ps1 | iex）
uv --version || curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. 安装依赖（scripts/ 已配置好运行环境，数据获取与回测均在此运行）
cd scripts && uv sync

# 3. 环境自检（依赖/Key/缓存/字体/数据拉取逐项 ✓/✗ + 修复建议）
uv run python run_list.py --doctor
```

免费服务无需 API Key 即可获取历史日 K 线并完成单标的回测/寻优/机器学习；
以下能力需配置环境变量 `TICKFLOW_API_KEY`（前往 [tickflow.org](https://tickflow.org) 注册申请）：

| 能力 | 是否需要 API Key |
|------|------------------|
| 历史日 K 线（1d/1w/1M/1Q/1Y）、单标的回测、参数寻优 | 否，免费服务即可 |
| 实时行情、分钟 K 线（1m/5m/15m/30m/60m）、日内分时 | 是 |
| 股票池成分（`--universe`，多因子/配对交易自动选池依赖） | 是 |
| 财务数据 / 基本面因子（价值、质量、规模） | 是（且账号需具备财务数据权限） |

配置：`export TICKFLOW_API_KEY="your-api-key"`（持久化写入 `~/.zshrc`；Windows 用
`setx TICKFLOW_API_KEY "..."`）。未配置时，需要 Key 的接口会报错并附申请与配置指引；
财务因子等能力会自动降级为价格因子继续运行。

## 快速开始

标的代码统一格式为 **代码.市场后缀**（如 `600000.SH`、`AAPL.US`、`00700.HK`），
完整格式见 [references/data-fetching.md](references/data-fetching.md)。
三类典型目的各一条命令直达（`scripts/` 下运行，免费日 K 即可）：

```bash
# ① 知道这只股票现在能不能买（结论先行：是/观察/否 + 交易计划）
uv run python run_score.py --symbol 600000.SH

# ② 研究一个策略的历史表现（回测 + 出图）
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross --plot

# ③ 定期定额定投一只标的（XIRR 与一次性投入对比）
uv run python run_dca.py --symbol 600000.SH --plot
```

SDK 直接取数（更多用法见 [references/data-fetching.md](references/data-fetching.md)）：

```python
from tickflow import TickFlow

tf = TickFlow.free()  # 免费历史日 K；完整服务用 TickFlow()（自动读 TICKFLOW_API_KEY）
df = tf.klines.get("600000.SH", period="1d", count=100, as_dataframe=True)
```

新手逐级上手动线与端到端用例见 [references/use-cases.md](references/use-cases.md)。

## 能力总览

各能力的一句话做法与详细文档入口（命令均支持 `--json`/`--plot`/`--report` 按需组合）：

| 场景 | 一句话做法 | 详见 |
|------|----------|------|
| 单股策略选优 | `run_compare.py` 一键对比全部内置策略，按夏普排序 | backtesting.md |
| 策略回测 | `run_backtest.py --strategy <名>`；风控叠加 `--stop-loss`/`--vol-target`/`--kelly`，做空 `--allow-short`，账本引擎 `--engine ledger` | backtesting.md |
| 自定义策略回测 | `run_custom.py --rules <TOML>` 白名单指标+受限条件表达式，Agent 按自然语言生成规则（不执行任意代码） | strategies.md |
| 参数寻优→复跑 | `run_optimize.py`（网格/随机/贝叶斯并行 + DSR 诊断），再 `run_backtest.py --params ...` 复跑 | backtesting.md |
| 稳健性验证 | `run_validate.py` 走步样本外 + `--pbo` 过拟合概率 | backtesting.md |
| 交易保真度 | `--market astock`（印花税/过户费）、`--limit-board`（涨跌停）、`--exec-price open`、`--adjust`（复权） | backtesting.md |
| 压力测试 | 回测/组合命令加 `--stress`（历史情景重放 + 蒙特卡洛） | stress-testing.md |
| 组合轮动与优化 | `run_portfolio.py --strategy momentum/equal_weight/inverse_vol/min_variance/max_sharpe/hrp/min_cvar` | portfolio.md |
| 组合风控与归因 | `run_portfolio.py --max-weight --risk --attribution` | portfolio.md |
| 多因子选股 | `run_factor.py` 打分选股 + 分层验证；`--ic` 输出 IC/IR/衰减/相关性 | multi-factor.md |
| 配对交易 | `run_pairs.py` 价差 z-score 市场中性套利（可 `--universe` 自动选对） | pairs-trading.md |
| 机器学习 | `run_ml.py` 走步样本外方向预测；`--label triple` 三重障碍、`--meta <策略>` 信号过滤 | ml-strategy.md |
| 新闻情绪 | `run_sentiment.py --stage fetch` 抓新闻 → agent 打分 → `--stage backtest`（agent-in-the-loop 三步） | sentiment.md |
| 定投 DCA | `run_dca.py`；增强模式 `--mode smart/dip/value_avg`，A 股分红建模 `--dividends` | dca.md |
| 单股纪律评分 | `run_score.py` 四层否决式评分 + 交易计划与建议仓位；`--valuation-pct` 估值分位、`--macro` 宏观环境、`--replay` 回放验证、`--fetch-events` 事件风险 | scoring.md |
| 市场扫描选候选 | `run_scan.py` 流动性初筛 + 批量评分，达标/降级分列 | scoring.md |
| 低估值/潜力筛选 | `run_screener.py` 基本面硬阈值漏斗（A 股免费全市场）；`--preset multibagger` 十倍股统计特征 | scoring.md |
| CAN SLIM 核查 | `run_canslim.py` 欧奈尔七项逐项核查，多标的横截面 RS 排名 | canslim.md |
| 持仓登记与体检 | `run_account.py --set` 登记，run_score/run_scan 自动联动 | scoring.md |
| 用户风险画像 | `run_profile.py --set --risk-tolerance <档位>`，run_score 建议仓位因人而异 | scoring.md |
| 每日信号跟踪 | `run_signal.py` 批量输出调仓动作，`--notify <webhook>` 推送 | live-signal.md |
| 模拟盘演练 | `run_paper.py` 虚拟资金纸面交易（`--mode score` 按评分裁决）；`--summary` 组合级总览 | live-signal.md |
| 事件研究 | `run_event.py` 事件窗 AAR/CAAR（可选基准超额） | backtesting.md |
| 统一总览 | `run_dashboard.py` 聚合持仓+模拟盘+今日信号于一页 HTML | use-cases.md |
| 能力发现 | `run_list.py --json` 列出全部策略/因子/模型/模式清单；`--doctor` 环境自检 | use-cases.md |

## CLI 通用约定（Agent 友好）

全部 `run_*.py` 遵循统一约定，便于脚本与 agent 编排：

- **`--help` 带示例**：每个命令的 `--help` 末尾附可直接复制的运行示例。
- **`--json` 全命令支持**：顶层固定含 `schema`/`command`/`generated_at` 元信息，字段只增不删；不带值时 stdout 保证纯 JSON（进度转 stderr）。
- **`--config <TOML>` 全覆盖**：配置文件注入默认值，显式命令行参数优先；未知键报错并给出近似建议。
- **规范退出码**：0=成功，1=运行错误（数据/网络），2=参数错误，130=用户中断；失败信息以 `[error] ` 前缀输出 stderr，含可操作的修复建议。
- **输出命名规范**：图表/报告默认 `outputs/<命令>_<关键参数>.png|html`（与 `scripts/` 平级，首次自动创建），同配置重跑才覆盖；`--output` 可自定义。
- **能力清单**：`run_list.py --json` 列出全部策略（含默认参数与参数网格）、轮动策略、因子、ML 模型与定投模式。
- **调试开关**：`ALPHA_FORGE_DEBUG=1` 出错时查看完整堆栈。

典型的 agent 流水线（对比→寻优→复跑、批量信号巡检、配置驱动批量实验）见
[references/use-cases.md](references/use-cases.md) 的「Agent 使用场景」章节。

## 注意事项

- 数据获取与回测脚本均在 `scripts/` 目录下用 `uv run python` 运行，首次需 `uv sync`。
- 回测结果不代表未来收益，参数寻优存在过拟合风险；判断策略真伪以样本外/DSR/PBO 为准（`run_validate.py`），任何策略**夏普比率 > 3 应优先怀疑**数据泄露或过拟合。
- K 线本地缓存与增量更新、数据源自动兜底（baostock/akshare/yfinance）、网络重试及 `ALPHA_FORGE_*` 环境变量详见 [references/data-fetching.md](references/data-fetching.md) 与 [references/faq.md](references/faq.md)。
- 机器学习依赖 lightgbm/scikit-learn（`uv sync` 自动装）；macOS 报 `libomp.dylib` 错误时 `brew install libomp` 或改用 `--model ridge`，详见 faq.md。
- 新闻情绪与事件风险标注采用 **agent-in-the-loop**：脚本抓素材生成模板 → agent（LLM）逐条打分写回 CSV → 脚本回传使用；无 agent 时有词典兜底（质量有限）。
- `run_signal.py` / `run_paper.py` 仅输出信号与纸面记账，**不做任何自动化下单或券商对接**；全部输出仅供研究参考，不构成投资建议。
- **测试与 CI**：`cd scripts && uv sync --group dev && uv run pytest tests/ -q`；持续集成配置见 `.github/workflows/ci.yml`。
