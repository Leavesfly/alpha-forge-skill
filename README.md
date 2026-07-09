# Alpha Forge Skill

> 使用 TickFlow Python SDK 获取 A 股、港股、美股、期货等市场的实时行情、K 线与财务数据，并内置经典量化策略与轻量回测引擎。

这是一个 [Qoder / Claude Code Skill](https://tickflow.org)，在数据获取能力之上提供开箱即用的量化策略、回测、绩效分析、可视化与参数寻优能力。适用于量化交易、数据分析与策略研究。

## 功能特性

- **多市场数据**：A 股（SH/SZ/BJ）、港股（HK）、美股（US）、国内期货（SHF/DCE/ZCE/CFX/INE/GFE）统一代码格式，可混合查询。
- **丰富数据类型**：实时行情、历史 K 线（日内分钟级至年线）、日内分时、财务数据（三大报表 + 核心指标）、标的池。
- **内置量化策略**：双均线交叉、MACD、RSI 超买超卖、布林带、动量、唐奇安通道突破、KDJ。
- **轻量回测引擎**：向量化实现，信号 `shift(1)` 防前视偏差，支持**多空**、**止损/止盈**风控与**波动率目标连续仓位**，内置手续费/滑点成本模型与 Buy & Hold 基准对比。
- **多标的组合回测**：截面轮动（动量/等权/风险平价）与组合优化（最小方差/最大夏普），按周期调仓，与等权基准对比。
- **多因子选股**：五类因子（价值/质量/规模/动量/波动率）去极值标准化打分合成、分位选股，并以分层回测验证因子有效性。
- **配对交易**：市场中性统计套利，自动筛选/手动指定配对，价差 z-score 开平仓。
- **机器学习策略**：技术指标特征 + LightGBM 方向预测，走步（walk-forward）重训练并只在样本外（OOS）段计价，天然规避前视与过拟合。
- **新闻情绪交易**：akshare 抓 A 股新闻 + AI（agent LLM）半自动情绪打分（agent-in-the-loop），情绪信号回测。
- **绩效与可视化**：累计/年化收益、夏普、索提诺、最大回撤、卡玛比率、胜率等指标；净值曲线/回撤/买卖点图表。
- **参数寻优**：策略参数网格搜索，按任意指标排序。

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
│   ├── ml-strategy.md           # 机器学习策略（LightGBM + 走步样本外）
│   ├── sentiment.md             # 新闻情绪交易（akshare + AI 打分）
│   └── use-cases.md             # 端到端典型用例
├── outputs/                     # --plot 图表与新闻/打分中间文件（与 scripts/ 平级，自动创建、已忽略）
└── scripts/                     # 可运行的回测工具代码
    ├── pyproject.toml           # 依赖（tickflow / pandas / numpy / matplotlib / lightgbm / akshare）
    ├── datafeed.py              # 统一数据获取（含股票池/财务）
    ├── run_backtest.py          # 回测 CLI
    ├── run_optimize.py          # 参数寻优 CLI
    ├── run_portfolio.py         # 多标的组合/优化 CLI
    ├── run_factor.py            # 多因子选股 CLI
    ├── run_pairs.py             # 配对交易 CLI
    ├── run_ml.py                # 机器学习策略 CLI（走步样本外）
    ├── run_sentiment.py         # 新闻情绪交易 CLI（两阶段 agent-in-the-loop）
    ├── strategies/              # 策略库（base + 7 策略 + 注册表）
    ├── backtest/                # 回测引擎、绩效指标、可视化、寻优
    ├── portfolio/               # 组合回测引擎、轮动/优化、可视化
    ├── factors/                 # 因子库、预处理合成、选股与分层回测
    ├── pairs/                   # 配对筛选、价差信号、可视化
    ├── ml/                      # 特征工程、LightGBM 走步训练、可视化
    └── sentiment/               # 新闻抓取、情绪打分契约、情绪信号回测
```

## 快速开始

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

# 参数寻优（按夏普比率排序）
uv run python run_optimize.py --symbol 600000.SH --strategy ma_cross

# 多标的组合动量轮动
uv run python run_portfolio.py --symbols 600000.SH,000001.SZ,600519.SH --strategy momentum

# 多因子选股（仅价格因子，无需财务权限）
uv run python run_factor.py --symbols 600000.SH,000001.SZ,600519.SH,000858.SZ,600809.SH --factors momentum,low_vol

# 配对交易（市场中性统计套利）
uv run python run_pairs.py --symbols 600000.SH,601398.SH

# 机器学习策略（LightGBM 方向预测 + 走步样本外，免费日 K 即可）
uv run python run_ml.py --symbol 600000.SH --count 800 --plot

# 新闻情绪交易（三步：抓新闻 → agent 打分 → 回测）
uv run python run_sentiment.py --symbol 600000.SH --stage fetch
# （agent 读 ../outputs/news_600000SH.csv，将情绪分写入 ../outputs/sentiment_600000SH.csv）
uv run python run_sentiment.py --symbol 600000.SH --stage backtest --plot
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
| [references/ml-strategy.md](references/ml-strategy.md) | 机器学习策略：技术指标特征 + LightGBM 方向预测 + 走步样本外验证 |
| [references/sentiment.md](references/sentiment.md) | 新闻情绪交易：akshare 抓新闻 + AI（agent LLM）情绪打分 + 情绪信号回测 |
| [references/use-cases.md](references/use-cases.md) | 端到端典型用例与结果解读 |

## 环境要求

- Python 3.10+（SDK 支持 3.9+）
- [uv](https://docs.astral.sh/uv/) 包管理器
- 机器学习与新闻情绪模块额外依赖 `lightgbm`、`akshare`（`uv sync` 自动安装）；macOS 上 LightGBM 需 OpenMP 运行库，若报错 `libomp.dylib` 请执行 `brew install libomp`。

## 免责声明

本项目仅用于数据分析与策略研究。回测结果基于历史数据，不代表未来收益；参数寻优存在过拟合风险，建议使用样本外数据验证。据此进行的任何投资决策风险自负。
