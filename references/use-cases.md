# 典型用例与新手引导（Use Cases & Onboarding）

本文档有两种读法：

- **新手**：从下面的「🧭 新手引导动线」开始，按 Level 0 → 6 逐级推进，每一步都能立刻看到结果。
- **老手**：直接看「场景导航」表，跳到需要的用例。

所有命令均在 `scripts/` 目录下运行（首次需 `cd scripts && uv sync`）。策略/参数细节见
[strategies.md](strategies.md)、[backtesting.md](backtesting.md)、[portfolio.md](portfolio.md)；
压力测试与 `--config` 见 [stress-testing.md](stress-testing.md)；信号/模拟盘见 [live-signal.md](live-signal.md)。

---

## 🧭 新手引导动线

按下面的阶梯从上到下推进，**先跑通、再理解、后拓展**，每一级都建立在上一级之上：

```
Level 0  环境就绪       →  cd scripts && uv sync              （3 分钟准备）
Level 1  第一次见效     →  两个 Hello 二选一：评分（现在能买吗）/ 回测（策略历史表现）
Level 2  选策略 / 调参  →  用例 1（一键多策略对比）、用例 2
Level 3  控风险 / 做空  →  用例 3（含压力测试）、用例 4
Level 4  多标的组合     →  用例 5、用例 6
Level 5  进阶模块       →  多因子 / 配对 / 机器学习 / 新闻情绪 / 定投 / 事件研究 / 市场扫描 / 价值筛选
Level 6  端到端闭环     →  用例 8（研究闭环）+ 决策闭环（扫描→评分→纸面跟踪）
```

| Level | 你会获得 | 对应内容 | 前置 |
|-------|---------|---------|------|
| 0 准备 | 可运行的环境 | 「开始之前」 | 无 |
| 1 入门 | 第一份评分裁决或回测报告 | 「5 分钟见效：两个 Hello」、用例 7 | 完成 L0 |
| 2 选优 | 会挑策略、会调参 | 用例 1、用例 2 | 完成 L1 |
| 3 风控 | 会控回撤、会做空 | 用例 3、用例 4 | 完成 L2 |
| 4 组合 | 会做多标的 / 跨市场轮动 | 用例 5、用例 6 | 完成 L2 |
| 5 进阶 | 因子 / 配对 / ML / 情绪 / 定投 / 事件研究 / 扫描 / 价值筛选 | 「进阶模块」 | 完成 L2（建议先过 L4） |
| 6 闭环 | 研究流水线 + 决策闭环（扫描/评分/信号/模拟盘） | 用例 8 | 完成 L2–L5 |

> 建议主线：**L0 → L1 → L2 → L3 → L4 → L6**；进阶模块（L5）可在熟悉 L2 后按兴趣穿插学习。

---

## 开始之前（Level 0：3 分钟准备）

1. **装好环境**（仅首次）：

   ```bash
   cd scripts
   uv sync
   ```

2. **要不要 API Key？** 免费服务即可获取历史日 K 线并完成**单标的回测、参数寻优、机器学习**——
   新手无需任何 Key。仅实时/分钟 K 线、股票池 `--universe`、财务因子才需要 `TICKFLOW_API_KEY`
   （详见 [SKILL.md「环境配置」](../SKILL.md)）。

3. **自检**：

   ```bash
   # 环境逐项自检（依赖/Key/缓存/字体/数据拉取，✓/✗ + 修复建议）
   uv run python run_list.py --doctor
   ```

   全部 ✓（或仅剩不影响免费日 K 主流程的警告）即环境就绪；有 ✗ 项按提示修复后重跑。

---

## 5 分钟见效：两个 Hello（Level 1，按需选一个）

两类新用户、两个入口，都只要一条命令、免费日 K 即可：

### A. Hello Score：「这只股票现在能买吗？」

想要一个直接的结论而不是一堆指标，从纪律评分开始：

```bash
cd scripts
uv run python run_score.py --symbol 600000.SH
```

输出**结论先行**：第一行就是「是 / 观察 / 否」五态裁决，后面附分层理由（哪层扣分、
为什么否决）与入场/止损/止盈交易计划价位。只要结论一行可加 `--brief`。

> 新手只需记住一条规则：**评分是纪律工具而非收益预测**——「否」多数时候只是因为
> 跌破年线（逆势不开仓），不是预言它会跌。原理与五态含义见 [scoring.md](scoring.md)。

### B. Hello Backtest：「这个策略历史上表现如何？」

想研究策略，对浦发银行用双均线策略回测最近 500 根日 K 并出图：

```bash
cd scripts
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross --plot
```

**读懂报告**——报告会同时打印「策略」与「基准 Buy & Hold」两栏，新手先盯这 3 行：

| 指标 | 含义 | 怎么看 |
|------|------|--------|
| 夏普比率 | 每承担一单位风险换来的超额收益 | **越高越好**：> 1 尚可，> 2 优秀，> 3 先怀疑过拟合 |
| 最大回撤 | 从最高点到最低点的最大跌幅 | **越小越好**：代表最坏情况能亏多少 |
| 累计收益率 | 区间总收益 | 要**和基准比**：跑不赢 Buy & Hold 就没有超额价值 |

其余指标（年化收益/波动、索提诺、卡玛、胜率、交易次数）见 [backtesting.md](backtesting.md)。
加了 `--plot` 会在 `../outputs/` 生成净值曲线 / 回撤 / 买卖点图，看图往往比看数字更直观。

> 贯穿全文的判断标准：**是否跑赢基准 + 风险调整后收益（夏普 / 卡玛）是否更好**。
>
> 两个 Hello 的关系：评分给「当下裁决」，回测验证「历史表现」，互为补充；
> 后续 Level 2–6 主线围绕回测研究展开，评分/扫描在 Level 6 与它们会合。

---

## 场景导航：我想做什么？

| 我想…… | 去看 | Level |
|--------|------|-------|
| 这只股票现在适合买吗 | 上面「Hello Score」（run_score 一条命令出裁决） | 1 |
| 快速跑通、看懂回测报告 | 上面「Hello Backtest」 | 1 |
| 今天市场上买什么 | 市场扫描（进阶模块，run_scan 漏斗筛候选） | 5 |
| 持仓了，该不该减 | `run_score.py --cost <成本价>`（[scoring.md](scoring.md)） | 1 |
| 只用免费数据做研究 | 用例 7 | 1 |
| 一只股票该用哪个策略 | 用例 1（run_compare 一键对比） | 2 |
| 给策略找最优参数 | 用例 2（多核并行 + DSR 诊断） | 2 |
| 降低回撤、追求更稳 | 用例 3 | 3 |
| 知道策略在股灾/熊市里会怎样 | 用例 3 的 `--stress` 压力测试 | 3 |
| 应对下跌、震荡行情 | 用例 4 | 3 |
| 用真实资金约束验证（整数股/一手 100 股） | `run_backtest.py --engine ledger`（[backtesting.md](backtesting.md)） | 3 |
| 多只股票组合轮动 | 用例 5 | 4 |
| A股 + 美股 + 港股混合 | 用例 6 | 4 |
| 从股票池里选股 | 多因子（进阶模块） | 5 |
| 市场中性、对冲大盘 | 配对交易（进阶模块） | 5 |
| 让模型预测涨跌方向 | 机器学习（进阶模块） | 5 |
| 用新闻情绪做信号 | 新闻情绪（进阶模块） | 5 |
| 定期定额定投一只标的 | 定投（进阶模块） | 5 |
| 看财报日/政策日前后的股价反应 | 事件研究（进阶模块） | 5 |
| 从头到尾走一遍研究 | 用例 8 | 6 |
| 策略验证完，每天看该买该卖 | 信号服务/模拟盘（[live-signal.md](live-signal.md)） | 6 |
| 按评分纪律每日纸面跟踪 | `run_paper.py --mode score`（[live-signal.md](live-signal.md)） | 6 |
| 用脚本/AI Agent 批量调用 | 下方「🤖 Agent 使用场景」 | - |
| 一套参数反复用，懒得每次敲 | `--config` TOML 配置（[stress-testing.md](stress-testing.md)） | - |

---

## 用例 1：快速评估一只股票，选出表现最好的策略

> 🎯 **Level 2 · 选优** ｜ 前置：已能跑通单次回测（Level 1）

目标：对某标的一条命令对比全部内置策略（清单用 `run_list.py` 查看），按夏普排序挑选最优。

```bash
cd scripts

# 一键对比全部策略（并排指标表，默认按夏普排序）
uv run python run_compare.py --symbol 600000.SH --count 500

# 只比感兴趣的子集 + 净值叠加图 + HTML 对比报告
uv run python run_compare.py --symbol 600000.SH --strategies ma_cross,macd,turtle \
  --plot --report
```

解读要点：
- 优先看**夏普比率**（风险调整后收益）和**最大回撤**，而非只看累计收益。
- 与表中「基准 Buy & Hold」对比，跑输基准的策略在该标的上无超额价值。
- 样本内「选冠军」存在选择性偏差，命令末尾会提示用 `run_validate.py` 复核。

---

## 用例 2：为策略寻找最优参数，再用最优参数复跑并出图

> 🎯 **Level 2 · 选优** ｜ 前置：已完成用例 1，理解夏普/回撤

目标：先网格寻优（多核并行），再用最优参数做一次带图回测。

```bash
# 第一步：寻优（默认按夏普排序，多核并行；--jobs 1 可强制串行）
uv run python run_optimize.py --symbol 600519.SH --strategy ma_cross --count 800

# 第二步：用寻优得到的最优参数复跑并出图（示例 fast=10 slow=30）
uv run python run_backtest.py --symbol 600519.SH --strategy ma_cross \
  --params fast=10 slow=30 --count 800 --plot
```

解读要点：
- 寻优结果的第一行即最优参数；命令末尾的 **DSR 过拟合诊断**会对「试了多少组参数」做惩罚，DSR < 90% 应改用 `run_validate.py` 做走步样本外验证。
- `--metric calmar` 可改为按卡玛比率（收益/回撤）排序，更看重回撤控制。

---

## 用例 3：用风控改善策略（止损 / 止盈 / 波动率目标）

> 🛡️ **Level 3 · 风控** ｜ 前置：已能对同一策略做多组对比

目标：对比同一策略在「无风控 / 止损止盈 / 波动率目标」下的表现。

```bash
# 无风控
uv run python run_backtest.py --symbol 600000.SH --strategy macd --count 500

# 加止损 5% + 止盈 15%
uv run python run_backtest.py --symbol 600000.SH --strategy macd --count 500 \
  --stop-loss 0.05 --take-profit 0.15

# 波动率目标 15%（连续仓位，按波动缩放头寸）
uv run python run_backtest.py --symbol 600000.SH --strategy macd --count 500 \
  --vol-target 0.15

# 选定风控方案后，加 --stress 看它在历史极端行情下的表现
uv run python run_backtest.py --symbol 600000.SH --strategy macd --count 1000 \
  --stop-loss 0.05 --stress
```

解读要点：
- 止损/止盈通常降低最大回撤，但可能牺牲部分收益，重点看夏普与卡玛是否改善。
- 波动率目标会把年化波动拉向目标值，适合追求稳定波动的资金。
- `--stress` 输出两张表：历史情景重放（2015 股灾/2018 熊市等，需回测区间覆盖）与蒙特卡洛冲击回撤分位数，用于风险预算参考，详见 [stress-testing.md](stress-testing.md)。

---

## 用例 4：应对下跌市——开启做空 / 多空对冲

> 🛡️ **Level 3 · 风控** ｜ 前置：理解风控参数（用例 3）

目标：在震荡或下行行情中，用做空捕捉反向趋势。

```bash
# 趋势策略开启做空（死叉时持有空头）
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross --count 500 --allow-short

# 做空 + 止损，控制反向波动风险
uv run python run_backtest.py --symbol 600000.SH --strategy macd --count 500 \
  --allow-short --stop-loss 0.08
```

解读要点：
- 做空后「交易次数」通常翻倍（多空来回），关注胜率与夏普是否同步改善。
- 均值回归类（rsi/bollinger）做空是在超买端反向，趋势类（ma_cross/macd）做空是反向趋势。

---

## 用例 5：构建多标的轮动组合并比较配置方式

> 📊 **Level 4 · 组合** ｜ 前置：会做单标的选优（用例 1）

目标：在一篮子标的上比较动量轮动、等权、风险平价三种组合。

```bash
SYMS=600000.SH,000001.SZ,600519.SH,000858.SZ,600809.SH
for r in momentum equal_weight inverse_vol; do
  echo "=== $r ==="
  uv run python run_portfolio.py --symbols $SYMS --strategy $r --count 500 \
    | grep -E "累计收益率|夏普比率|最大回撤|调仓次数"
done
```

解读要点：
- 三者都与「等权基准」对比；`inverse_vol`（风险平价）通常回撤更小。
- `momentum` 的 `--top-k` 越小越集中、波动越大；`--rebalance` 越小换手越高、成本越大。

---

## 用例 6：跨市场组合（A股 + 美股 + 港股混合轮动）

> 📊 **Level 4 · 组合** ｜ 前置：已跑通多标的轮动（用例 5）

目标：跨市场做动量轮动，分散单一市场风险（需 `TICKFLOW_API_KEY` 或免费日 K）。

```bash
uv run python run_portfolio.py \
  --symbols 600519.SH,AAPL.US,00700.HK,MSFT.US \
  --strategy momentum --lookback 60 --top-k 2 --rebalance 20 --plot
```

解读要点：
- 多标的按**共同交易日**对齐，不同市场休市日不同会缩短可回测区间，建议适当增大 `--count`。
- 图中「各标的权重」堆叠图可直观看出资金在不同市场间的轮动。

---

## 用例 7：免费服务快速研究（无需 API Key）

> 🚀 **Level 1 · 入门** ｜ 前置：完成环境安装（Level 0）

目标：仅用免费历史日 K 做研究，下载数据并回测。新手若想用「编程方式」而非命令行入门，从这里开始最省心。

```python
# scripts/quick_research.py
from datafeed import fetch_ohlcv
from strategies import get_strategy
from backtest import run_backtest, format_report

df = fetch_ohlcv("600000.SH", period="1d", count=1000)  # 免费日 K 足够
df.to_csv("600000_SH.csv", index=False)                  # 存档

result = run_backtest(df, get_strategy("kdj"), symbol="600000.SH")
print(format_report(result.metrics))
```

运行：`uv run python quick_research.py`

解读要点：
- 免费服务不支持实时/分钟 K；研究历史日线策略完全够用。
- `TickFlow.free()` 由 `fetch_ohlcv` 在无 API Key 时自动启用。

---

## 进阶模块（Level 5：多因子 / 配对 / 机器学习 / 新闻情绪 / 定投 / 事件研究 / 市场扫描 / 价值筛选）

熟悉了单标的与组合回测后，可按兴趣选学以下进阶能力。每个模块都有独立的详解文档，这里给出最小上手命令与适用场景：

| 模块 | 一条上手命令 | 适用场景 | 详解 |
|------|-------------|----------|------|
| 多因子选股 | `run_factor.py --symbols 600000.SH,000001.SZ,600519.SH,000858.SZ,600809.SH --factors momentum,low_vol --plot` | 从一篮子/股票池按因子打分选股 | [multi-factor.md](multi-factor.md) |
| 配对交易 | `run_pairs.py --symbols 600000.SH,601398.SH --plot` | 市场中性、对冲大盘的统计套利 | [pairs-trading.md](pairs-trading.md) |
| 机器学习 | `run_ml.py --symbol 600000.SH --count 800 --plot` | 可插拔模型（lgbm/ridge/logistic）预测涨跌，只在样本外计价 | [ml-strategy.md](ml-strategy.md) |
| 新闻情绪 | `run_sentiment.py --symbol 600000.SH --stage fetch` | 让 agent 读新闻打分转成信号回测 | [sentiment.md](sentiment.md) |
| 定投（定期定额） | `run_dca.py --symbol 600000.SH --plot` | 按周期定额投入，看资金加权 IRR 与一次性投入对比 | [dca.md](dca.md) |
| 事件研究 | `run_event.py --symbol 600000.SH --events 2025-04-30,2025-08-30 --plot` | 看财报日/政策日前后的平均超额反应（AAR/CAAR） | [backtesting.md](backtesting.md) |
| 市场扫描 | `run_scan.py --symbols 600000.SH,600519.SH,000001.SZ,601398.SH --top 5` | 对一篮子/股票池跑纪律评分漏斗，筛出「是/观察」候选 | [scoring.md](scoring.md) |
| 价值筛选 | `run_screener.py --max-pe 15 --min-div 3` | 低估值/高分红/高质量全市场筛选（六维硬阈值漏斗，A 股免费） | [scoring.md](scoring.md) |

（上表命令均以 `uv run python` 前缀在 `scripts/` 下运行，如 `uv run python run_factor.py ...`。）

新手提示：
- 上面的模块都能用**免费日 K** 起步（多因子的价格因子、机器学习、配对的手动一对、事件研究均无需 Key）；股票池 `--universe` 与财务因子才需要 `TICKFLOW_API_KEY`。定投同样免费日 K 即可。
- 机器学习会**只在样本外段计价**，天然规避前视；macOS 缺 libomp 时可用 `--model ridge/logistic`；任何模块出现**夏普 > 3 应优先怀疑**过拟合或数据泄露。
- 新闻情绪是 **agent-in-the-loop 三步**：抓新闻 → agent 给情绪分 → 回测，具体见 [sentiment.md](sentiment.md)。
- 定投用**资金加权 XIRR**而非时间加权收益计量，并与**一次性投入**基准对比；长期上涨品种往往一次性投入更优，定投的价值在于纪律与摊薄成本，具体见 [dca.md](dca.md)。
- 事件研究小样本噪声很大，**事件数 < 10 时结论仅供参考**；加 `--benchmark 510300.SH` 可算相对指数的超额反应。

---

## 用例 8：端到端研究流水线（数据 → 选优 → 组合）

> 🏁 **Level 6 · 闭环** ｜ 前置：已掌握选优、风控、组合（Level 2–4），了解进阶模块（Level 5）

目标：从候选池出发，先单标的选优，再组合成轮动策略，最后接到每日信号——把前面各级能力串成一条研究闭环。

1. **筛池**：三条路径任选或叠加——用 `run_screener.py` 基本面价值筛选（低估值/高质量/高分红），或用财务指标筛优质股（见 [data-fetching.md](data-fetching.md) 的「筛选优质股票」），或用 `run_scan.py` 对股票池跑纪律评分漏斗，取「是/观察」档候选。
2. **选策略**：对候选逐个 `run_compare.py` 一键对比全部策略（用例 1）。
3. **调参 + 防过拟合**：对胜出策略 `run_optimize.py` 寻优，看 DSR；再用 `run_validate.py` 做走步样本外验证（用例 2）。
4. **组合**：把入选标的放入 `run_portfolio.py` 做动量轮动，与等权基准对比（用例 5）。
5. **加风控 + 压测**：叠加 `--stop-loss` / `--vol-target` 观察回撤改善，加 `--stress` 看极端行情表现（用例 3）。
6. **出报告复核**：关键结果用 `--plot` / `--report` 出图与 HTML 报告，检查净值、回撤区间与买卖点/权重是否合理。
7. **接信号与模拟盘**（可选收尾）：验证过的策略用 `run_signal.py` 每日看调仓动作，`run_paper.py` 虚拟资金演练并追踪与回测的偏差（[live-signal.md](live-signal.md)）。

**另一条平行的决策闭环**（不写策略代码、纯纪律驱动，适合日常例行）：

```bash
# 扫描筛候选 → 单标的评分复核（含交易计划与回放验证）→ 按评分裁决纸面跟踪
uv run python run_scan.py --symbols 600000.SH,600519.SH,601398.SH --top 3
uv run python run_score.py --symbol 600519.SH --replay 120
uv run python run_paper.py --symbol 600519.SH --mode score
```

每日重复最后一条命令即可追踪评分纪律的纸面表现，详见 [scoring.md](scoring.md) 与 [live-signal.md](live-signal.md)。

解读要点：
- 每一步都以「是否跑赢基准 + 风险调整后收益」作为取舍标准。
- 全流程仅依赖历史数据，回测结论需用样本外区间二次验证后再考虑实盘；本工具不做自动下单，信号仅供研究参考。
- 重复实验建议把固定口径（成本/成交价/区间）写进 TOML，用 `--config` 保证每次口径一致。

---

## 🤖 Agent 使用场景（结构化调用指南）

本工具箱对 AI Agent 做了专门适配：**结构化 JSON 输出、stdout 纯净约定、规范退出码、
统一错误前缀**。Agent 编排多步研究流水线时按以下约定调用即可。

### 通用约定

| 约定 | 说明 |
|------|------|
| JSON 输出 | `--json` 不带值打印到 stdout（进度全部转 stderr，stdout 保证纯 JSON）；带路径写入文件。**全部 18 个命令均支持**：`run_backtest` / `run_optimize` / `run_compare` / `run_portfolio` / `run_signal` / `run_dca` / `run_score` / `run_scan` / `run_canslim` / `run_ml` / `run_pairs` / `run_factor` / `run_validate` / `run_sentiment` / `run_paper` / `run_event` / `run_list` / `run_account` |
| JSON 结构 | 顶层固定含 `schema`（当前 `alpha-forge/1`）、`command`、`generated_at` 三个元信息键，按 `command` 分发解析；字段只增不删 |
| Agent 友好字段 | 所有命令的 JSON 输出含 **`summary`**（1–2 句自然语言结论，可直接引用或改写后转述给用户）和 **`next_steps`**（结构化后续动作列表，每项含 `action`/`reason`/`command`，据此程序化链式引导） |
| 能力发现 | `run_list.py --json`（`command=list`）返回全部策略（含默认参数与参数网格）、轮动策略、因子、ML 模型与定投模式，agent 可据此动态构造后续命令 |
| 退出码 | 0=成功；1=运行错误（数据/网络/计算，含非法策略参数组合）；2=参数错误；130=用户中断。失败信息以 `[error] ` 前缀输出到 stderr |
| 配置文件 | 全部 18 个 `run_*.py` 支持 `--config <TOML>`（显式命令行参数优先；未知键报错并给出近似建议） |
| 输出命名 | 图表/报告默认落 `outputs/<命令>_<关键参数>.png|html`，同配置重跑才覆盖；`--output` 可显式指定 |
| 调试 | 设置 `ALPHA_FORGE_DEBUG=1` 可在出错时查看完整 Python 堆栈 |

用户口语意图→命令的完整路由表与转述守则见 [SKILL.md「对话意图路由」](../SKILL.md)。

### 场景 0：对话式应答（最常见：用户问一句，agent 答一段）

用户：“帮我看看茅台现在能不能买？”

```bash
# 1. 推断标的代码（茅台 → 600519.SH），跑纪律评分
uv run python run_score.py --symbol 600519.SH --json > score.json
# 2. 取关键字段组织回答：
#    .summary           自然语言结论（可直接引用或改写）
#    .verdict_cn        结论（是/观察/否/持仓需减风险/无法评分）
#    .layers[]          各层理由（哪一层拦截、为什么）
#    .alpha_score       排名分（相对强弱，非涨跌概率）
#    .plan              入场/止损/2R/3R 交易计划价位（结论为「是」时）
#    .next_steps[]      结构化后续动作（action/reason/command）
```

转述模板（结论 + 理由 + 计划 + 局限性，四段缺一不可）：

> 「按纪律评分，茅台当前结论是**观察**：动量分达标（排名分 62），但收盘价在 MA60 之下触发风险封顶。
> 若后续站回 MA60，参考计划：入场 1520、止损 1455（-2×ATR）。
> 注意：这是趋势纪律过滤而非涨跌预测，不构成投资建议。」

后续对话接口：用户说“那帮我盯着它”→ `run_paper.py --symbol 600519.SH --mode score`（每日重跑，
幂等）；用户追问“这套评分靠谱吗”→ `run_score.py --symbol 600519.SH --replay 120 --json` 用历史回放自证。

> **Agent 提示**：优先使用 `.summary` 字段作为转述起点，再根据 `.next_steps` 主动提议下一步，
> 而非等待用户追问。链式引导话术模板见 [SKILL.md「链式引导模板」](../SKILL.md)。

### 场景 A：批量回测 → 解析指标 → 选优复跑

```bash
# 1. 多策略对比，拿到按夏普排序的结构化结果
uv run python run_compare.py --symbol 600000.SH --json > compare.json
# 解析 .strategies[0].name 得到最优策略；.summary 为自然语言结论
# .next_steps 已含「寻优」和「验证」的结构化命令

# 2. 对最优策略寻优（并行），拿到 best_params 与 DSR 过拟合诊断
uv run python run_optimize.py --symbol 600000.SH --strategy ma_cross --json > opt.json
# 解析 .best_params 与 .dsr.dsr（< 0.90 应提示用户过拟合风险）
# .summary 已含 DSR 诊断结论；.next_steps 含「复跑」和「验证」命令

# 3. 用最优参数复跑并出报告（params 由 .best_params 拼出）
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross \
    --params fast=20 slow=60 --report --json > final.json
# .summary 为最终结论；.next_steps 含「对比」「寻优」「验证」三个后续动作
```

### 场景 B：批量标的每日信号巡检

```bash
uv run python run_signal.py --symbols 600000.SH,600519.SH,AAPL.US \
    --strategy macd --no-cache --json > signals.json
# 解析 .signals[]，筛选 action 为「买入/加仓」「卖出/减仓」的标的提醒用户
# .summary 已含各动作统计；.next_steps 含「模拟盘」和「评分复核」命令
# 注意 .disclaimer 字段：仅供研究参考，不构成投资建议
```

### 场景 C：TOML 配置驱动的批量实验

```bash
# 固定保真度口径写进配置，循环只改标的/策略，保证实验口径一致
cat > exp.toml <<'EOF'
count = 800
market = "astock"
exec-price = "open"
limit-board = "main"
EOF
for s in ma_cross macd turtle; do
    uv run python run_backtest.py --config exp.toml \
        --symbol 600000.SH --strategy $s --json > "bt_$s.json"
done
```

### 场景 D：错误处理分支

```bash
uv run python run_backtest.py --symbol 600000 --strategy ma_cross --json > out.json
if [ $? -ne 0 ]; then
    # stderr 中的 [error] 行即人类可读原因（此例：标的代码缺市场后缀）
    # exit 1=运行错误可换参数重试；exit 2=参数错误应修正调用
    :
fi
```

> Agent 提示：优先消费 `--json` 而非解析终端表格；表格为 rich 渲染，宽度/样式会随终端变化，
> 不保证稳定。JSON 字段遵循「只增不删」，可放心按键取值。
> **新增 `summary` 和 `next_steps` 字段**：`summary` 可直接作为转述起点，`next_steps` 可程序化驱动链式引导。
> 常见报错与解决方案对照表见 [faq.md](./faq.md)。

