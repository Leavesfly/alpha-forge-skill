<div align="center">

# ⚒️ Alpha Forge

**从数据到决策的量化研究工作台**

A 股 · 港股 · 美股 · 期货 | 14 种策略 · 6 类研究范式 · 23 个 CLI 工具

[![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)](https://python.org)
[![uv](https://img.shields.io/badge/uv-powered-8A2BE2)](https://docs.astral.sh/uv/)
[![Tests](https://img.shields.io/badge/tests-434_passed-brightgreen)](scripts/tests/)
[![License](https://img.shields.io/badge/license-MIT-gray)](LICENSE)

[快速开始](#-60-秒上手) · [功能全景](#-功能全景) · [命令速查](#-命令速查) · [文档](#-文档)

</div>

---

## 💡 这是什么

Alpha Forge 是一个 **AI Agent 原生的量化研究工作台**，为 [Qoder / Claude Code Skill](https://tickflow.org) 设计。

它不只是回测框架——而是一套完整的 **「数据 → 研究 → 决策 → 跟踪」** 闭环：

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Alpha Forge 工作流                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│   📊 数据层          🔬 研究层           🎯 决策层          📈 跟踪层    │
│   ─────────         ─────────          ─────────         ─────────     │
│   • 多源行情         • 策略回测          • 纪律评分         • 每日信号    │
│   • 财务数据         • 参数寻优          • CAN SLIM        • 模拟盘      │
│   • 新闻情绪         • 机器学习          • 市场扫描         • 持仓账户    │
│   • 分红历史         • 组合优化          • 交易计划         • Dashboard  │
│                     • 因子研究          • 仓位建议                       │
│                     • 压力测试                                          │
│                                                                         │
│   ◄──────────── 全部 CLI 支持 --json 结构化输出 ────────────►           │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

**核心设计原则：**

| 原则 | 体现 |
|------|------|
| 🤖 Agent 优先 | 23 个 CLI 全部 `--json` 输出，含 `summary` + `next_steps` 链式引导 |
| 🛡️ 诚实回测 | 信号 `shift(1)` 防前视、DSR/PBO 过拟合诊断、样本外验证 |
| 🎯 决策导向 | 不只告诉你「历史表现」，更回答「现在能买吗 / 买多少 / 何时卖」 |
| 🔄 渐进增强 | 免费日 K 即可上手，API Key 解锁实时数据与高级功能 |

---

## ✨ 功能全景

### 📊 数据能力

| 特性 | 说明 |
|------|------|
| **多市场统一** | A 股（SH/SZ/BJ）、港股（HK）、美股（US）、期货（SHF/DCE/ZCE/CFX/INE/GFE）混合查询 |
| **丰富类型** | 实时行情、K 线（分钟级至年线）、分时、财务三大报表、标的池 |
| **智能缓存** | Parquet 本地缓存 + 增量更新 + 复权一致性校验，重复研究零网络开销 |
| **多源降级** | TickFlow → baostock → akshare → yfinance 自动降级，单源故障无感知 |

### 🔬 策略与研究

| 特性 | 说明 |
|------|------|
| **14 种策略** | 双均线、MACD、RSI、布林带、动量、唐奇安、KDJ、网格、海龟、肯特纳、SuperTrend、Dual Thrust、CCI、威廉指标 |
| **自定义策略 DSL** | TOML 声明式规则（白名单指标 + 受限条件表达式），Agent 按自然语言生成规则回测，策略空间无限 |
| **双引擎回测** | 向量化引擎（快速研究）+ 账本引擎（现金 + 整数股，A 股 100 股约束） |
| **参数寻优** | 网格 / 随机 / 贝叶斯搜索，多核并行，DSR 过拟合诊断 |
| **组合优化** | 动量轮动、等权、风险平价、最小方差、最大夏普、HRP、最小 CVaR |
| **多因子选股** | 价值/质量/规模/动量/波动率五类因子，IC/IR/衰减/正交化研究 |
| **配对交易** | 市场中性统计套利，自动筛选配对，价差 z-score 开平仓 |
| **机器学习** | LightGBM/Ridge/Logistic 可插拔，走步样本外，三重障碍标注，meta-labeling |
| **新闻情绪** | akshare 抓新闻 + AI 情绪打分（agent-in-the-loop），情绪信号回测 |
| **定投 DCA** | 现金流账本 + XIRR，智能定投/超跌加码/价值平均，显式分红建模 |
| **事件研究** | 事件窗 AAR/CAAR，可选基准超额 |

### 🎯 决策与风控

| 特性 | 说明 |
|------|------|
| **纪律评分** | 四层否决式（ALPHA → 风险 → 技术 → 时机），结论五态 + ATR 交易计划 + 建议仓位 |
| **CAN SLIM** | 欧奈尔七项法则纪律化，A 股 EPS/ROE 自动获取，横截面 RS 排名 |
| **市场扫描** | 流动性初筛 → 批量评分 → 达标/降级候选分列 |
| **持仓账户** | 统一登记持仓，评分/扫描自动联动（带入成本、标注已持有） |
| **用户风险画像** | 保守/平衡/激进三档预设，评分建议仓位因人而异（显式参数优先） |
| **风险指标** | VaR/CVaR/下行偏差/尾部比率/溃疡指数，暴露约束，回撤熔断 |
| **压力测试** | 历史情景重放（2015 股灾/2018 熊市/2020-03）+ 蒙特卡洛冲击 |
| **稳健验证** | 走步样本外、Deflated Sharpe Ratio、PBO 过拟合概率 |

### 📈 输出与集成

| 特性 | 说明 |
|------|------|
| **绩效指标** | 夏普、索提诺、欧米茄、卡玛、最大回撤、胜率、盈亏比、信息比率、alpha/beta |
| **可视化** | 净值曲线、回撤图、买卖点、月度热力图、滚动夏普、收益分布 |
| **HTML 报告** | 自包含 tear sheet，单文件可交付 |
| **实盘前置** | 每日信号（webhook 推送）+ 模拟盘纸面交易 + 组合级 Dashboard |

---

## 🚀 60 秒上手

### 安装

```bash
# 1. 安装 uv（如未安装）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. 同步依赖
cd scripts && uv sync
```

### 三条路径，按需选择

| 我想…… | 一条命令 | 耗时 |
|--------|----------|------|
| 🔍 **看这只股票能不能买** | `uv run python run_score.py --symbol 600000.SH` | ~10s |
| 📊 **研究一个策略的历史表现** | `uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross --plot` | ~15s |
| 💰 **制定定投计划** | `uv run python run_dca.py --symbol 600000.SH --plot` | ~10s |

> 💡 环境有问题？运行 `uv run python run_list.py --doctor` 逐项自检。

### API Key（可选）

免费服务即可获取历史日 K 线，完成上述所有操作。实时行情与分钟数据需要：

```bash
export TICKFLOW_API_KEY="your-key"  # 访问 https://tickflow.org 获取
```

---

## 📖 命令速查

### 研究类

```bash
cd scripts

# 策略回测（14 种策略任选）
uv run python run_backtest.py --symbol 600000.SH --strategy macd --plot

# 多策略对比（一键 PK 全部策略）
uv run python run_compare.py --symbol 600000.SH --plot

# 自定义策略 DSL（TOML 规则文件，Agent 可按自然语言生成）
uv run python run_custom.py --symbol 600000.SH --rules examples/custom_rule.toml --plot

# 参数寻优（网格/随机/贝叶斯，多核并行）
uv run python run_optimize.py --symbol 600000.SH --strategy ma_cross --method bayes

# 稳健性验证（走步样本外 + PBO 过拟合概率）
uv run python run_validate.py --symbol 600000.SH --strategy ma_cross --pbo

# 组合轮动与优化
uv run python run_portfolio.py --symbols 600000.SH,000001.SZ,600519.SH --strategy momentum
uv run python run_portfolio.py --symbols AAPL.US,MSFT.US,TSLA.US --strategy hrp

# 多因子选股
uv run python run_factor.py --symbols 600000.SH,000001.SZ,600519.SH --factors momentum,low_vol --ic

# 配对交易（市场中性）
uv run python run_pairs.py --symbols 600000.SH,601398.SH --plot

# 机器学习策略（走步样本外，防过拟合）
uv run python run_ml.py --symbol 600000.SH --count 800 --plot

# 新闻情绪交易（agent-in-the-loop 三步）
uv run python run_sentiment.py --symbol 600000.SH --stage fetch
uv run python run_sentiment.py --symbol 600000.SH --stage backtest --plot

# 定投回测（含分红建模）
uv run python run_dca.py --symbol 600000.SH --mode smart --dividends --plot

# 事件研究
uv run python run_event.py --symbol 600000.SH --events 2025-04-30,2025-08-30 --plot
```

### 决策类

```bash
# 纪律评分（能不能买 / 买多少 / 何时卖）
uv run python run_score.py --symbol 600000.SH --capital 200000 --risk-pct 0.02

# 评分回放验证（自证有效性）
uv run python run_score.py --symbol 600519.SH --count 800 --replay --plot

# CAN SLIM 成长股检查清单
uv run python run_canslim.py --symbol 600519.SH
uv run python run_canslim.py --symbols 600519.SH,000858.SZ,300750.SZ  # 多标的横截面

# 全市场扫描
uv run python run_scan.py --symbols 600000.SH,600519.SH,000858.SZ,AAPL.US

# 持仓账户管理
uv run python run_account.py --set --symbol 600000.SH --shares 1000 --cost 8.50
uv run python run_account.py  # 查看持仓与浮盈亏

# 用户风险画像（评分建议仓位因人而异）
uv run python run_profile.py --set --risk-tolerance balanced --capital 200000
uv run python run_profile.py  # 查看画像
```

### 跟踪类

```bash
# 每日信号（可推送到钉钉/企微/飞书）
uv run python run_signal.py --symbols 600000.SH,600519.SH --strategy ma_cross

# 模拟盘纸面交易
uv run python run_paper.py --symbol 600000.SH --strategy ma_cross
uv run python run_paper.py --symbol 600000.SH --mode score  # 按评分裁决

# 组合级总览 Dashboard
uv run python run_dashboard.py

# 能力清单与环境自检
uv run python run_list.py --json
uv run python run_list.py --doctor
```

### 高级用法

```bash
# A 股真实成本 + 涨跌停规则 + 次日开盘成交
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross \
    --market astock --limit-board main --exec-price open

# 压力测试（历史情景 + 蒙特卡洛）
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross --stress

# 结构化 JSON 输出（Agent 消费）
uv run python run_backtest.py --symbol AAPL.US --strategy macd --json > result.json

# TOML 配置文件
uv run python run_backtest.py --config examples/backtest.toml

# HTML 研究报告
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross --report
```

---

## 🎯 内置策略

| 策略 | 名称 | 核心逻辑 | 适用场景 |
|------|------|----------|----------|
| `ma_cross` | 双均线交叉 | 短均线上穿长均线做多 | 趋势市 |
| `macd` | MACD | DIF 上穿 DEA 做多 | 趋势市 |
| `rsi` | RSI 超买超卖 | RSI 超卖买入，超买卖出 | 震荡市 |
| `bollinger` | 布林带 | 跌破下轨买入，突破上轨卖出 | 震荡市 |
| `momentum` | 动量 | 过去 N 期收益为正做多 | 趋势市 |
| `donchian` | 唐奇安通道 | 突破 N 日高点做多 | 突破行情 |
| `kdj` | KDJ | K 上穿 D 做多 | 震荡市 |
| `grid` | 网格交易 | 跌加仓涨减仓（连续仓位） | 震荡市 |
| `turtle` | 海龟交易 | 唐奇安突破 + ATR 止损 | 趋势市 |
| `keltner` | 肯特纳通道 | EMA ± ATR 通道突破 | 趋势市 |
| `supertrend` | SuperTrend | ATR 追踪止损线 | 趋势市 |
| `dual_thrust` | Dual Thrust | 开盘价 ± K×区间突破 | 日内/短线 |
| `cci` | CCI 顺势 | CCI < -100 买入 | 超跌反弹 |
| `williams_r` | 威廉指标 | WR 超卖买入 | 震荡市 |

> 📖 策略原理与参数详解 → [references/strategies.md](references/strategies.md)

---

## 🏗️ 项目结构

```
alpha-forge-skill/
├── SKILL.md                 # Skill 入口（Agent 路由、意图识别）
├── README.md                # 本文件
│
├── references/              # 📚 详细文档
│   ├── data-fetching.md     #    数据获取详解
│   ├── strategies.md        #    策略原理与参数
│   ├── backtesting.md       #    回测引擎与指标
│   ├── portfolio.md         #    组合回测与优化
│   ├── multi-factor.md      #    多因子选股
│   ├── pairs-trading.md     #    配对交易
│   ├── ml-strategy.md       #    机器学习策略
│   ├── sentiment.md         #    新闻情绪交易
│   ├── dca.md               #    定投 DCA
│   ├── scoring.md           #    纪律评分
│   ├── canslim.md           #    CAN SLIM 清单
│   ├── stress-testing.md    #    压力测试
│   ├── live-signal.md       #    信号与模拟盘
│   ├── use-cases.md         #    新手引导 + 典型用例
│   └── faq.md               #    常见问题
│
├── outputs/                 # 📊 图表与报告输出（自动创建）
│
└── scripts/                 # ⚙️ 可运行代码
    ├── run_*.py             #    23 个 CLI 工具
    ├── datafeed.py          #    统一数据获取
    ├── cli_common.py        #    CLI 公共工具
    │
    ├── strategies/          #    14 种策略实现 + custom.py（自定义规则 DSL 引擎）
    ├── backtest/            #    回测引擎（向量化 + 账本）
    ├── portfolio/           #    组合回测与优化
    ├── factors/             #    因子库与研究
    ├── pairs/               #    配对交易
    ├── ml/                  #    机器学习
    ├── sentiment/           #    新闻情绪
    ├── dca/                 #    定投引擎
    ├── scoring/             #    纪律评分
    ├── canslim/             #    CAN SLIM
    ├── data/                #    缓存与多数据源
    ├── research/            #    验证与事件研究
    ├── risk/                #    风险管理
    ├── report/              #    报告生成
    └── tests/               #    434 个测试用例
```

---

## 🤖 Agent 集成

Alpha Forge 专为 AI Agent 设计，全部 CLI 支持结构化输出：

```bash
# JSON 输出（stdout 纯净，进度转 stderr）
uv run python run_score.py --symbol 600000.SH --json
```

输出结构：

```json
{
  "schema": "alpha-forge/score/v1",
  "command": "run_score.py",
  "generated_at": "2025-01-15T10:30:00Z",
  "summary": "600000.SH 当前评分 72/100，结论「观察」：技术面确认但入场时机未到",
  "next_steps": [
    {
      "action": "paper_track",
      "reason": "结论为观察，建议纸面跟踪等待时机",
      "command": "uv run python run_paper.py --symbol 600000.SH --mode score"
    }
  ],
  "data": { ... }
}
```

**Agent 对话示例：**

| 用户说 | Agent 执行 |
|--------|-----------|
| "浦发银行现在能买吗" | `run_score.py --symbol 600000.SH --json` |
| "帮我回测一下茅台的 MACD 策略" | `run_backtest.py --symbol 600519.SH --strategy macd --json` |
| "这几只股票哪个最好" | `run_scan.py --symbols ... --json` |
| "帮我盯着这只股票" | `run_paper.py --symbol ... --mode score` |
| "符合 CAN SLIM 吗" | `run_canslim.py --symbol ... --json` |

**对话意图路由表（完整版）：**

| 用户大致会说…… | 执行 | 转述时必须包含 |
|----------------|------|--------------|
| "XX 现在能买吗 / 值不值得入手 / 帮我看看 XX" | `run_score.py --symbol <代码> --json` | 结论五态中文（verdict_cn）+ 哪一层给出的理由 + 交易计划价位与建议仓位；必须说明这是纪律过滤而非涨跌预测 |
| "我持有 XX，成本 N，该不该卖/减仓" | `run_score.py --symbol <代码> --cost N --json` | 同上；「持仓需减风险」≠预测下跌，是风控纪律 |
| "帮我记一下持仓 / 我的持仓怎么样了" | 登记 `run_account.py --set --symbol <代码> --shares N --cost P`；查看 `run_account.py --json` | 持仓清单与浮盈亏；登记后 run_score/run_scan 自动联动（带入成本/标注已持有） |
| "我是保守型/平衡型/激进型投资者 / 记住我的风险偏好 / 我只有20万" | `run_profile.py --set --risk-tolerance <档位> --capital N --json` | 画像登记结果；说明后续 run_score 的建议仓位会因人而异（显式参数优先） |
| "最近有什么值得买的 / 帮我从这几只里挑一挑" | `run_scan.py --symbols <逗号列表> --json`（或 `--universe`，需 Key） | 达标/降级分列；建议对入选者再跑 run_score 复核 |
| "有没有低估值的股票 / 高分红的 / 便宜又好的" | `run_screener.py --json`（A 股全市场默认）或 `--symbols <列表>`（港美股） | 达标候选排名 + 关键估值指标；建议对候选跑 run_score 做技术面复核 |
| "帮我找潜在十倍股 / 十倍成长股 / multibagger" | `run_screener.py --preset multibagger --json` | 十倍股统计特征候选；须声明是统计共性非预测，建议接 run_canslim 交叉确认 + run_portfolio 组合持有 |
| "XX 符不符合 CAN SLIM / 用欧奈尔法则筛一筛" | `run_canslim.py --symbol <代码> --json`（多标的比较用 `--symbols`） | 七项通过/失败/不可评明细 + 结论；M（大势）不满足直接否；基本面缺失时诚实说明封顶「观察」 |
| "XX 用什么策略好 / 哪个策略适合 XX" | `run_compare.py --symbol <代码> --json` | 最优策略 + 夏普/回撤 + 是否跑赢 Buy&Hold；提示样本内选冠军有偏差 |
| "帮我回测一下 XX 的 YY 策略" | `run_backtest.py --symbol <代码> --strategy <策略> --json`（出图加 `--plot`） | 累计/年化收益、夏普、最大回撤，并与基准对比；回测不代表未来 |
| "我想自己定义一个策略：金叉且 RSI 不过热时买……" | agent 按用户描述生成 TOML 规则文件，再 `run_custom.py --symbol <代码> --rules <文件> --json` | 规则如何被解析（入场/离场条件）+ 回测结果 vs 基准；提醒自定义规则未经样本外验证 |
| "帮我调参 / 找最优参数" | `run_optimize.py --symbol <代码> --strategy <策略> --json`（大网格加 `--method random`） | 最优参数 + DSR 诊断结论；DSR<90% 时必须提醒过拟合风险并建议 run_validate |
| "这策略靠谱吗 / 是不是过拟合" | `run_validate.py --symbol <代码> --strategy <策略> --pbo --json` | 样本外夏普 vs 样本内、PBO 概率；以样本外为准 |
| "这几只股票帮我做个组合" | `run_portfolio.py --symbols <列表> --strategy momentum --json` | 组合 vs 等权基准；调仓频率与成本假设 |
| "我想定投 XX" | `run_dca.py --symbol <代码> --json`（A 股可加 `--dividends` 显式建模分红） | XIRR 与一次性投入对比；定投价值在纪律而非必然更高收益 |
| "每天帮我盯着 XX 该买该卖" | 首次 `run_paper.py --symbol <代码> --mode score`（或 `--strategy <策略>`），以后每日重跑；只看信号用 `run_signal.py` | 今日动作 + 当前持仓/净值；不自动下单，仅纸面跟踪 |
| "拉一下 XX 的行情/K 线/财务" | 直接用 TickFlow SDK（见 data-fetching.md） | 数据口径（复权/周期） |
| "出个报告给我" | 在对应命令加 `--report`（HTML）或 `--plot`（图） | 文件路径（outputs/ 下） |
| "帮我看看今天整体情况 / 出个总览" | `run_dashboard.py`（可加 `--symbols` 附信号） | Dashboard HTML 路径 + 风控提示摘要 |
| 命令报错 / 环境不确定 | `run_list.py --doctor`；再查 faq.md | 失败项与修复建议 |

> 📖 Agent 完整集成指南 → [references/use-cases.md](references/use-cases.md)

---

## 🧪 测试

```bash
cd scripts
uv sync --group dev
uv run pytest tests/ -q

# 434 passed in ~20s
```

测试覆盖：防前视偏差、成本模型、绩效指标、缓存一致性、并行寻优、账本引擎、分红建模等核心逻辑。

---

## 📚 文档

| 文档 | 内容 |
|------|------|
| [SKILL.md](SKILL.md) | Skill 入口：Agent 路由、意图识别、转述守则 |
| [data-fetching.md](references/data-fetching.md) | 数据获取：标的格式、行情/K线/财务示例 |
| [strategies.md](references/strategies.md) | 14 种策略的原理、参数与信号逻辑 |
| [backtesting.md](references/backtesting.md) | 回测引擎、绩效指标、可视化、参数寻优 |
| [portfolio.md](references/portfolio.md) | 组合回测、轮动策略、组合优化 |
| [multi-factor.md](references/multi-factor.md) | 多因子选股与分层回测 |
| [pairs-trading.md](references/pairs-trading.md) | 配对交易（市场中性） |
| [ml-strategy.md](references/ml-strategy.md) | 机器学习策略（走步样本外） |
| [sentiment.md](references/sentiment.md) | 新闻情绪交易 |
| [dca.md](references/dca.md) | 定投 DCA（现金流 + XIRR） |
| [scoring.md](references/scoring.md) | 纪律评分与市场扫描 |
| [canslim.md](references/canslim.md) | CAN SLIM 检查清单 |
| [stress-testing.md](references/stress-testing.md) | 压力测试与 TOML 配置 |
| [live-signal.md](references/live-signal.md) | 信号服务与模拟盘 |
| [use-cases.md](references/use-cases.md) | 新手引导 + 典型用例 + Agent 指南 |
| [faq.md](references/faq.md) | 常见问题与排错 |

---

## ⚙️ 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | 3.10+ | SDK 支持 3.9+ |
| [uv](https://docs.astral.sh/uv/) | latest | 包管理器 |
| tickflow | ≥0.1.17 | 数据源 SDK |
| pandas / numpy | ≥2.0 / ≥1.24 | 数据处理 |
| matplotlib | ≥3.7 | 可视化 |
| rich | ≥13.0 | 终端渲染 |
| lightgbm / scikit-learn | ≥4.0 / ≥1.3 | 机器学习（可选） |
| akshare / baostock / yfinance | latest | 多数据源兜底 |

> 💡 macOS 上 LightGBM 需要 OpenMP：`brew install libomp`，或改用 `--model ridge`。

---

## ⚠️ 免责声明

本项目仅用于数据分析与策略研究。

- 回测结果基于历史数据，**不代表未来收益**
- 参数寻优存在过拟合风险，建议使用样本外验证（`run_validate.py`）
- 纪律评分是风控工具，**不是涨跌预测**
- 信号与模拟盘**不做自动下单**，输出仅供研究参考

**据此进行的任何投资决策，风险自负。**

---

<div align="center">

**Built with ⚒️ by Alpha Forge Team**

[TickFlow](https://tickflow.org) · [Qoder](https://qoder.ai)

</div>
