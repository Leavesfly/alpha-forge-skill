# Alpha Forge Skill

> 使用 TickFlow Python SDK 获取 A 股、港股、美股、期货等市场的实时行情、K 线与财务数据，并内置经典量化策略与轻量回测引擎。

这是一个 [Qoder / Claude Code Skill](https://tickflow.org)，在数据获取能力之上提供开箱即用的量化策略、回测、绩效分析、可视化与参数寻优能力。适用于量化交易、数据分析与策略研究。

## 功能特性

- **多市场数据**：A 股（SH/SZ/BJ）、港股（HK）、美股（US）、国内期货（SHF/DCE/ZCE/CFX/INE/GFE）统一代码格式，可混合查询。
- **丰富数据类型**：实时行情、历史 K 线（日内分钟级至年线）、日内分时、财务数据（三大报表 + 核心指标）、标的池。
- **内置量化策略**：双均线交叉、MACD、RSI 超买超卖、布林带、动量、唐奇安通道突破、KDJ、网格交易、海龟交易、肯特纳通道、SuperTrend、Dual Thrust、CCI 顺势、威廉指标（共 14 个，含跨参数非法组合校验）。
- **轻量回测引擎**：向量化实现，信号 `shift(1)` 防前视偏差，支持**多空**、**止损/止盈**风控与**波动率目标连续仓位**，内置手续费/滑点成本模型与 Buy & Hold 基准对比；另提供**账本引擎**（现金 + 整数股、A 股一手 100 股约束，`--engine ledger`）。
- **多策略对比**：`run_compare.py` 一条命令对比多个策略，并排绩效表 + 净值叠加图 + HTML 对比报告。
- **多标的组合回测**：截面轮动（动量/等权/风险平价）与组合优化（最小方差/最大夏普），按周期调仓，与等权基准对比。
- **多因子选股**：五类因子（价值/质量/规模/动量/波动率）去极值标准化打分合成、分位选股，并以分层回测验证因子有效性。
- **配对交易**：市场中性统计套利，自动筛选/手动指定配对，价差 z-score 开平仓。
- **机器学习策略**：技术指标特征 + 可插拔模型（LightGBM/Ridge/Logistic，`--model`）方向预测，走步（walk-forward）重训练并只在样本外（OOS）段计价；支持置信度连续仓位（`--prob-sizing`）与 Ridge 线性基线过拟合对照。
- **新闻情绪交易**：akshare 抓 A 股新闻 + AI（agent LLM）半自动情绪打分（agent-in-the-loop），情绪信号回测。
- **定投（定期定额/DCA）**：按周期（日/周/月）注入现金、份额累积，现金流账本建模，资金加权 XIRR 计量；内置智能定投（分档加码）、超跌回撤加码、价值平均等增强模式，并与一次性投入、纯定投双基准对比；支持**显式分红建模**（`--dividends` 自动拉 A 股分红历史或读 CSV，不复权价 + 分红再投入/现金落袋两种策略）。
- **纪律评分决策层**：四层否决式评分（ALPHA 加权 → 风险否决 → 技术确认 → 入场时机，单向降级、利好不加分），输出结论五态（是/观察/否/持仓需减风险/无法评分）、ATR 交易计划价位（入场/止损/2R/3R）与**建议仓位**（风险预算法，`--capital`/`--risk-pct`）；`--replay` 历史回放 + 21/63 日前瞻收益事件研究自证有效性；`--fetch-events` 抓新闻素材生成待标注风险模板（agent-in-the-loop 事件风险闭环）；`run_scan.py` 全市场扫描漏斗（流动性初筛 → 批量评分 → 达标/降级候选分列）；A股/港股/美股基准自适应。
- **统一持仓账户**：`run_account.py` 登记/查看/移除真实持仓（outputs/account.json）；run_score 自动带入账户成本给操作建议，run_scan 标注「已持有」并支持 `--exclude-held`（仅登记，不做交易执行）。
- **CAN SLIM 检查清单**：`run_canslim.py` 把欧奈尔七项法则纪律化（C 当季EPS增长 / A 年度EPS复合增长 / N 新高 / S 量能供求 / L 相对强度 / I 机构认同（诚实标注不可评）/ M 市场方向否决）；A 股季度 EPS/ROE 自动获取（akshare 免 Key），多标的横截面 RS 百分位排名，阈值可本土化（`--c-growth`/`--a-growth`/`--roe`）。
- **市场状态识别**：趋势效率（Kaufman ER）+ 波动率分位将市场分为趋势上行/下行/震荡/高波动四态；run_score 输出状态上下文，run_compare 在样本内冠军与当前状态适配策略族不符时预警。
- **绩效与可视化**：累计/年化收益、夏普、索提诺、欧米茄、最大回撤、最长回撤持续期、卡玛比率、胜率、盈亏比、偏度/峰度等指标，另提供相对基准的信息比率/跟踪误差/beta/alpha；净值曲线/回撤/买卖点图表，HTML 报告含月度收益热力图、滚动夏普 + 收益分布图与逐指标解释。
- **交易保真度**：可组合成本模型（A 股卖出印花税 + 双边过户费）、A 股涨跌停/停牌「不可成交」建模、次日开盘成交约定（`--exec-price open`）。
- **数据缓存、复权与多数据源**：K 线本地缓存（Parquet，缺 pyarrow 时回退 pickle，TTL 按周期分级：日线 1 天/分钟线 30 分钟；陈旧缓存增量更新，只拉尾部小段 + 重叠区复权一致性校验，每日扫描不再全量重拉）+ 前/后/不复权口径显式化；单源拉取失败自动重试退避（`ALPHA_FORGE_RETRIES`），TickFlow 主源仍失败时自动降级兜底：A 股日/周/月 K 走 baostock → akshare，港股/美股日/周/月 K 走 yfinance。
- **压力测试**：历史情景重放（2015 股灾/2018 熊市/2020-03 等预置窗口）+ 蒙特卡洛冲击（单日冲击注入/波动放大/bootstrap 回撤分位数），`--stress` 一键开启。
- **稳健性验证**：走步（walk-forward）样本外重寻优、Deflated Sharpe Ratio（多重检验惩罚）、PBO 过拟合概率（组合对称交叉验证 CSCV）。
- **风险管理**：VaR/CVaR/下行偏差/尾部比率/溃疡指数、仓位暴露约束、回撤熔断、收益贡献与多因子回归归因。
- **因子研究平台**：IC/IR、t 值、因子衰减、因子相关性矩阵、正交化（中性化）。
- **研究报告与集成**：自包含 HTML 研究报告（tear sheet，含压力测试与假设局限区块）、CLI `--json` 结构化输出、rich 终端表格与进度条、TOML 配置文件（`--config`，全部 CLI 支持，显式命令行优先）。
- **Agent 友好**：全部 18 个 CLI 支持 `--json`，输出顶层固定含 `schema`/`command`/`generated_at` 元信息（stdout 纯净，进度转 stderr）；`run_list.py --json` 一条命令查询全部策略/因子/模型/模式清单，`run_list.py --doctor` 环境逐项自检（依赖/Key/缓存/字体/数据拉取 + 修复建议）；枚举参数拼错附近似候选建议；退出码 0/1/2/130 规范化，错误统一 `[error] ` 前缀并附修复建议；所有 `--help` 带可复制示例；常见报错对照表见 `references/faq.md`。
- **实盘前置**：每日信号服务（`run_signal.py`，目标仓位 + 调仓动作）、模拟盘纸面交易与偏差追踪（`run_paper.py`，状态持久化 + 幂等；`--mode score` 可直接按纪律评分裁决纸面执行，决策→跟踪闭环）；**不做自动下单/券商对接**。
- **事件研究**：`run_event.py` 给定事件日算事件窗 AAR/CAAR（可选基准超额）。
- **测试与 CI**：pytest 回归测试（重点覆盖防前视/成本/指标/缓存/并行一致性/账本引擎），GitHub Actions 多版本 CI。
- **参数寻优**：策略参数网格/随机搜索（`--method random` 从网格空间采样 `--n-iter` 组，大空间更快且多重检验惩罚更轻；多核并行 `--jobs`），按任意指标排序，并给出过拟合诊断（DSR）。
- **仓位管理**：波动率目标连续仓位（`--vol-target`）与半 Kelly 连续仓位（`--kelly`，仓位 = 信号 × 0.5μ/σ² 滚动估计）；评分交易计划附风险预算法建议仓位（回答「买多少」）。

## 目录结构

```
alpha-forge-skill/
├── SKILL.md                     # Skill 入口（概览、导航、快速开始）
├── README.md                    # 本文件
├── references/                  # 详细文档（按需查阅）
│   ├── data-fetching.md         # 数据获取详解与实用场景
│   ├── strategies.md            # 内置策略原理与参数
│   ├── backtesting.md           # 回测引擎、指标、可视化、寻优
│   ├── portfolio.md             # 多标的组合回测与轮动
│   ├── multi-factor.md          # 多因子选股与分层回测
│   ├── pairs-trading.md         # 配对交易（市场中性）
│   ├── ml-strategy.md           # 机器学习策略（可插拔模型 + 走步样本外）
│   ├── sentiment.md             # 新闻情绪交易（akshare + AI 打分）
│   ├── dca.md                   # 定投（定期定额/DCA，现金流回测 + XIRR）
│   ├── scoring.md               # 纪律评分与市场扫描（四层否决式评分 + 交易计划 + 回放验证）
│   ├── stress-testing.md        # 压力测试与 TOML 配置文件
│   ├── live-signal.md           # 信号服务与模拟盘（实盘前置）
│   ├── use-cases.md             # 新手引导动线 + 典型用例 + Agent 调用指南
│   └── faq.md                   # Troubleshooting/FAQ：常见报错与解决方案对照表
├── outputs/                     # --plot 图表与新闻/打分/模拟盘状态文件（与 scripts/ 平级，自动创建、已忽略）
└── scripts/                     # 可运行的回测工具代码
    ├── pyproject.toml           # 依赖（tickflow / pandas / numpy / matplotlib / rich / lightgbm / scikit-learn / akshare；dev: pytest）
    ├── datafeed.py              # 统一数据获取（含缓存/复权/多源降级/股票池/财务）
    ├── cli_common.py            # CLI 公共工具（参数校验/错误处理/JSON 输出/退出码）
    ├── cli_config.py            # TOML 配置文件注入（--config，显式命令行优先）
    ├── run_backtest.py          # 回测 CLI（含成本/规则/成交价/账本引擎/--stress/--json/--report）
    ├── run_optimize.py          # 参数寻优 CLI（多核并行 + DSR 过拟合诊断）
    ├── run_compare.py           # 多策略对比 CLI（并排指标表 + 净值叠加图 + HTML 报告）
    ├── run_validate.py          # 稳健性验证 CLI（走步样本外 + PBO）
    ├── run_portfolio.py         # 多标的组合/优化 CLI（含暴露约束/风险/归因/--stress）
    ├── run_factor.py            # 多因子选股 CLI（含 IC/IR/衰减/相关性）
    ├── run_pairs.py             # 配对交易 CLI
    ├── run_ml.py                # 机器学习策略 CLI（--model/--prob-sizing + 线性基线对照）
    ├── run_sentiment.py         # 新闻情绪交易 CLI（两阶段 agent-in-the-loop）
    ├── run_dca.py               # 定投（定期定额）回测 CLI（现金流账本 + XIRR）
    ├── run_score.py             # 单股纪律评分 CLI（四层否决 + 交易计划 + 建议仓位 + 回放验证 + --fetch-events）
    ├── run_scan.py              # 全市场扫描 CLI（流动性初筛 + 批量评分漏斗，--exclude-held）
    ├── run_account.py           # 统一持仓账户 CLI（登记/查看/移除，score/scan 自动联动）
    ├── run_signal.py            # 每日信号服务 CLI（目标仓位 + 调仓动作，不下单）
    ├── run_paper.py             # 模拟盘 CLI（纸面交易 + 偏差追踪）
    ├── run_event.py             # 事件研究 CLI（AAR/CAAR）
    ├── run_list.py              # 能力清单 CLI（策略/轮动/因子/ML 模型/定投模式，--json）
    ├── examples/                # 示例 TOML 配置（backtest.toml）
    ├── strategies/              # 策略库（base + 14 策略 + 注册表）
    ├── backtest/                # 回测引擎（向量化 + 账本）、成本模型、A股交易规则、指标、可视化、并行寻优
    ├── portfolio/               # 组合回测引擎、轮动/优化、可视化
    ├── factors/                # 因子库、预处理合成、选股、分层回测、IC/IR 研究
    ├── pairs/                   # 配对筛选、价差信号、可视化
    ├── ml/                      # 特征工程、可插拔模型走步训练、可视化
    ├── sentiment/               # 新闻抓取、情绪打分契约、情绪信号回测
    ├── dca/                     # 定投现金流账本、XIRR 指标、可视化
    ├── scoring/                 # 纪律评分（四层引擎、交易计划、回放验证、扫描漏斗）
    ├── data/                    # K 线本地缓存、复权口径、多数据源抽象（TickFlow/baostock/akshare/yfinance）
    ├── research/                # 走步验证、Deflated Sharpe、PBO、事件研究、市场状态识别（regime）
    ├── risk/                    # VaR/CVaR、暴露约束、回撤熔断、业绩归因、压力测试
    ├── report/                  # 结构化 JSON、自包含 HTML 报告、rich 终端渲染
    └── tests/                   # pytest 回归测试套件
```

## 快速开始

### 60 秒上手：按目的选一条路径

装好环境（下方步骤 1）后，三类典型目的各一条命令直达，免费日 K 即可：

| 我想…… | 一条命令（scripts/ 下） | 继续深入 |
|--------|--------------------|---------|
| 知道这只股票现在能不能买 | `uv run python run_score.py --symbol 600000.SH` | [scoring.md](references/scoring.md) |
| 研究一个策略的历史表现 | `uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross --plot` | [use-cases.md](references/use-cases.md) 新手动线 |
| 定期定额定投一只标的 | `uv run python run_dca.py --symbol 600000.SH --plot` | [dca.md](references/dca.md) |

遇到问题先跑环境自检：`uv run python run_list.py --doctor`（逐项 ✓/✗ 并附修复建议）。

### 1. 安装 uv 并同步依赖

```bash
# 安装 uv（若未安装，macOS/Linux）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 安装项目依赖
cd scripts
uv sync
```

### 2. 配置 API Key（可选）

免费服务无需 API Key 即可获取历史日 K 线；实时行情与分钟 K 线需配置：

```bash
export TICKFLOW_API_KEY="your-api-key"   # 访问 https://tickflow.org 获取
```

### 3. 获取数据

```python
from tickflow import TickFlow

tf = TickFlow.free()  # 免费服务，无需 API Key
df = tf.klines.get("600000.SH", period="1d", count=100, as_dataframe=True)
print(df.tail())
```

### 4. 运行回测

```bash
cd scripts

# 基础回测（输出绩效指标报告）
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross

# 回测并生成图表
uv run python run_backtest.py --symbol 600000.SH --strategy macd --plot

# 参数寻优（按夏普比率排序，多核并行）
uv run python run_optimize.py --symbol 600000.SH --strategy ma_cross

# 多策略对比（缺省全部 14 个策略，并排指标表 + 净值叠加图）
uv run python run_compare.py --symbol 600000.SH --plot

# 账本引擎：现金 + 整数股（A 股一手 100 股），10 万本金真实建仓约束
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross --engine ledger --market astock

# 压力测试：历史情景重放 + 蒙特卡洛冲击
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross --stress

# 多标的组合动量轮动
uv run python run_portfolio.py --symbols 600000.SH,000001.SZ,600519.SH --strategy momentum

# 多因子选股（仅价格因子，无需财务权限）
uv run python run_factor.py --symbols 600000.SH,000001.SZ,600519.SH,000858.SZ,600809.SH --factors momentum,low_vol

# 配对交易（市场中性统计套利）
uv run python run_pairs.py --symbols 600000.SH,601398.SH

# 机器学习策略（可插拔模型 + 走步样本外，免费日 K 即可；无 libomp 可用 --model ridge）
uv run python run_ml.py --symbol 600000.SH --count 800 --plot

# 新闻情绪交易（三步：抓新闻 → agent 打分 → 回测）
uv run python run_sentiment.py --symbol 600000.SH --stage fetch
# （agent 读 ../outputs/news_600000SH.csv，将情绪分写入 ../outputs/sentiment_600000SH.csv）
uv run python run_sentiment.py --symbol 600000.SH --stage backtest --plot
```

```bash
# 定投（定期定额）：每月定投 + 资金加权 IRR 与一次性投入对比（免费日 K 即可；增强模式见 --mode smart/dip/value_avg）
uv run python run_dca.py --symbol 600000.SH --freq monthly --amount 1000 --plot

# 单股纪律评分：四层否决式评分，结论 + 交易计划价位；--replay 回放验证
uv run python run_score.py --symbol 600000.SH
uv run python run_score.py --symbol 600519.SH --count 800 --replay --plot

# 全市场扫描：流动性初筛 + 批量评分，达标/降级候选分列
uv run python run_scan.py --symbols 600000.SH,600519.SH,000858.SZ,AAPL.US

# 每日信号服务：多标的批量输出目标仓位与调仓动作（不下单）
uv run python run_signal.py --symbols 600000.SH,600519.SH --strategy ma_cross --no-cache

# 模拟盘：虚拟资金纸面交易，追踪与回测预期的偏差（同日重跑幂等，--reset 重置）
uv run python run_paper.py --symbol 600000.SH --strategy ma_cross

# 事件研究：给定事件日算事件窗 AAR/CAAR（可选 --benchmark 基准超额）
uv run python run_event.py --symbol 600000.SH --events 2025-04-30,2025-08-30 --plot
```

### 5. 交易保真度、稳健性验证与报告

```bash
# A 股真实成本 + 涨跌停规则 + 次日开盘成交，并生成自包含 HTML 报告
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross \
    --market astock --limit-board main --exec-price open --report

# 结构化 JSON 输出（便于 agent 解析；进度信息自动转 stderr）
uv run python run_backtest.py --symbol AAPL.US --strategy macd --json > result.json

# 走步样本外验证 + PBO 过拟合概率
uv run python run_validate.py --symbol 600000.SH --strategy ma_cross --pbo --count 800

# 组合暴露约束 + 风险报告 + 收益归因
uv run python run_portfolio.py --symbols 600000.SH,000001.SZ,600519.SH \
    --strategy momentum --max-weight 0.5 --risk --attribution

# 因子 IC/IR、衰减与相关性分析
uv run python run_factor.py --symbols 600000.SH,000001.SZ,600519.SH,000858.SZ,600809.SH \
    --factors momentum,low_vol --ic

# 从 TOML 配置文件读参数（显式命令行参数优先）
uv run python run_backtest.py --config examples/backtest.toml
```

### 6. 运行测试

```bash
cd scripts
uv sync --group dev   # 安装开发依赖（pytest）
uv run pytest tests/ -q
```

## 内置策略

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

## 文档

| 文档 | 内容 |
|------|------|
| [SKILL.md](SKILL.md) | Skill 入口：能力导航、环境配置、快速开始 |
| [references/data-fetching.md](references/data-fetching.md) | 数据获取详解：标的格式、行情/K线/财务示例、实用场景 |
| [references/strategies.md](references/strategies.md) | 内置策略原理、参数与信号逻辑 |
| [references/backtesting.md](references/backtesting.md) | 回测引擎、绩效指标、可视化与参数寻优 |
| [references/portfolio.md](references/portfolio.md) | 多标的组合回测、截面轮动与组合优化（最小方差/最大夏普） |
| [references/multi-factor.md](references/multi-factor.md) | 多因子选股与分层回测 |
| [references/pairs-trading.md](references/pairs-trading.md) | 配对交易（市场中性统计套利） |
| [references/ml-strategy.md](references/ml-strategy.md) | 机器学习策略：技术指标特征 + 可插拔模型（LightGBM/Ridge/Logistic）方向预测 + 走步样本外验证 |
| [references/sentiment.md](references/sentiment.md) | 新闻情绪交易：akshare 抓新闻 + AI（agent LLM）情绪打分 + 情绪信号回测 |
| [references/dca.md](references/dca.md) | 定投（定期定额/DCA）：现金流账本回测、资金加权 XIRR、智能定投/超跌加码/价值平均等增强模式、双基准对比 |
| [references/scoring.md](references/scoring.md) | 纪律评分与市场扫描：四层否决式评分、结论五态、ATR 交易计划、回放验证、事件风险降级、持仓联动 |
| [references/stress-testing.md](references/stress-testing.md) | 压力测试（历史情景重放 + 蒙特卡洛冲击）与 TOML 配置文件（--config） |
| [references/live-signal.md](references/live-signal.md) | 实盘前置：每日信号服务（run_signal）与模拟盘纸面交易 + 偏差追踪（run_paper） |
| [references/use-cases.md](references/use-cases.md) | 新手引导动线（Level 0→6）+ 端到端典型用例 + Agent 结构化调用指南（JSON 约定/退出码/批量实验） |

## 环境要求

- Python 3.10+（SDK 支持 3.9+）
- [uv](https://docs.astral.sh/uv/) 包管理器
- 机器学习与新闻情绪模块额外依赖 `lightgbm`、`scikit-learn`、`akshare`（`uv sync` 自动安装）；macOS 上 LightGBM 需 OpenMP 运行库，若报错 `libomp.dylib` 请执行 `brew install libomp`，或改用 `--model ridge/logistic`。

## 免责声明

本项目仅用于数据分析与策略研究。回测结果基于历史数据，不代表未来收益；参数寻优存在过拟合风险，建议使用样本外数据验证。据此进行的任何投资决策风险自负。
