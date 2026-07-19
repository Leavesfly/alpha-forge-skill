# 典型用例与新手引导（Use Cases & Onboarding）

本文档有两种读法：

- **新手**：从下面的「🧭 新手引导动线」开始，按 Level 0 → 6 逐级推进，每一步都能立刻看到结果。
- **老手**：直接看「场景导航」表，跳到需要的用例。

所有命令均在 `scripts/` 目录下运行（首次需 `cd scripts && uv sync`）。策略/参数细节见
[strategies.md](strategies.md)、[backtesting.md](backtesting.md)、[portfolio.md](portfolio.md)。

---

## 🧭 新手引导动线

按下面的阶梯从上到下推进，**先跑通、再理解、后拓展**，每一级都建立在上一级之上：

```
Level 0  环境就绪       →  cd scripts && uv sync              （3 分钟准备）
Level 1  第一个回测     →  Hello Backtest + 读懂报告          （5 分钟见效）
Level 2  选策略 / 调参  →  用例 1、用例 2
Level 3  控风险 / 做空  →  用例 3、用例 4
Level 4  多标的组合     →  用例 5、用例 6
Level 5  进阶模块       →  多因子 / 配对 / 机器学习 / 新闻情绪 / 定投
Level 6  端到端流水线   →  用例 8（把上面串成研究闭环）
```

| Level | 你会获得 | 对应内容 | 前置 |
|-------|---------|---------|------|
| 0 准备 | 可运行的环境 | 「开始之前」 | 无 |
| 1 入门 | 第一份回测报告，会读指标 | 「5 分钟跑通第一个回测」、用例 7 | 完成 L0 |
| 2 选优 | 会挑策略、会调参 | 用例 1、用例 2 | 完成 L1 |
| 3 风控 | 会控回撤、会做空 | 用例 3、用例 4 | 完成 L2 |
| 4 组合 | 会做多标的 / 跨市场轮动 | 用例 5、用例 6 | 完成 L2 |
| 5 进阶 | 因子 / 配对 / ML / 情绪 | 「进阶模块」 | 完成 L2（建议先过 L4） |
| 6 闭环 | 完整研究流水线 | 用例 8 | 完成 L2–L5 |

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

3. **自检**（能打印出报告即环境就绪）：

   ```bash
   uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross --count 200
   ```

---

## 5 分钟跑通第一个回测（Level 1：Hello Backtest）

**一条命令**，对浦发银行用双均线策略回测最近 500 根日 K，并出图：

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

---

## 场景导航：我想做什么？

| 我想…… | 去看 | Level |
|--------|------|-------|
| 快速跑通、看懂报告 | 上面「Hello Backtest」 | 1 |
| 只用免费数据做研究 | 用例 7 | 1 |
| 一只股票该用哪个策略 | 用例 1 | 2 |
| 给策略找最优参数 | 用例 2 | 2 |
| 降低回撤、追求更稳 | 用例 3 | 3 |
| 应对下跌、震荡行情 | 用例 4 | 3 |
| 多只股票组合轮动 | 用例 5 | 4 |
| A股 + 美股 + 港股混合 | 用例 6 | 4 |
| 从股票池里选股 | 多因子（进阶模块） | 5 |
| 市场中性、对冲大盘 | 配对交易（进阶模块） | 5 |
| 让模型预测涨跌方向 | 机器学习（进阶模块） | 5 |
| 用新闻情绪做信号 | 新闻情绪（进阶模块） | 5 |
| 定期定额定投一只标的 | 定投（进阶模块） | 5 |
| 从头到尾走一遍研究 | 用例 8 | 6 |

---

## 用例 1：快速评估一只股票，选出表现最好的策略

> 🎯 **Level 2 · 选优** ｜ 前置：已能跑通单次回测（Level 1）

目标：对某标的用多个策略回测，横向对比后挑选最优。

```bash
cd scripts
for s in ma_cross macd rsi bollinger momentum donchian kdj; do
  echo "=== $s ==="
  uv run python run_backtest.py --symbol 600000.SH --strategy $s --count 500 \
    | grep -E "累计收益率|夏普比率|最大回撤"
done
```

解读要点：
- 优先看**夏普比率**（风险调整后收益）和**最大回撤**，而非只看累计收益。
- 与报告中的「基准 Buy & Hold」对比，跑输基准的策略在该标的上无超额价值。

---

## 用例 2：为策略寻找最优参数，再用最优参数复跑并出图

> 🎯 **Level 2 · 选优** ｜ 前置：已完成用例 1，理解夏普/回撤

目标：先网格寻优，再用最优参数做一次带图回测。

```bash
# 第一步：寻优（默认按夏普排序）
uv run python run_optimize.py --symbol 600519.SH --strategy ma_cross --count 800

# 第二步：用寻优得到的最优参数复跑并出图（示例 fast=10 slow=30）
uv run python run_backtest.py --symbol 600519.SH --strategy ma_cross \
  --params fast=10 slow=30 --count 800 --plot
```

解读要点：
- 寻优结果的第一行即最优参数；注意**过拟合**风险，建议改用更长区间或另一只标的做样本外验证。
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
```

解读要点：
- 止损/止盈通常降低最大回撤，但可能牺牲部分收益，重点看夏普与卡玛是否改善。
- 波动率目标会把年化波动拉向目标值，适合追求稳定波动的资金。

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

## 进阶模块（Level 5：多因子 / 配对 / 机器学习 / 新闻情绪 / 定投）

熟悉了单标的与组合回测后，可按兴趣选学以下进阶能力。每个模块都有独立的详解文档，这里给出最小上手命令与适用场景：

| 模块 | 一条上手命令 | 适用场景 | 详解 |
|------|-------------|----------|------|
| 多因子选股 | `run_factor.py --symbols 600000.SH,000001.SZ,600519.SH,000858.SZ,600809.SH --factors momentum,low_vol --plot` | 从一篮子/股票池按因子打分选股 | [multi-factor.md](multi-factor.md) |
| 配对交易 | `run_pairs.py --symbols 600000.SH,601398.SH --plot` | 市场中性、对冲大盘的统计套利 | [pairs-trading.md](pairs-trading.md) |
| 机器学习 | `run_ml.py --symbol 600000.SH --count 800 --plot` | 用 LightGBM 学特征预测涨跌方向 | [ml-strategy.md](ml-strategy.md) |
| 新闻情绪 | `run_sentiment.py --symbol 600000.SH --stage fetch` | 让 agent 读新闻打分转成信号回测 | [sentiment.md](sentiment.md) |
| 定投（定期定额） | `run_dca.py --symbol 600000.SH --plot` | 按周期定额投入，看资金加权 IRR 与一次性投入对比 | [dca.md](dca.md) |

（上表命令均以 `uv run python` 前缀在 `scripts/` 下运行，如 `uv run python run_factor.py ...`。）

新手提示：
- 上面 4 个模块都能用**免费日 K** 起步（多因子的价格因子、机器学习、配对的手动一对均无需 Key）；股票池 `--universe` 与财务因子才需要 `TICKFLOW_API_KEY`。定投同样免费日 K 即可。
- 机器学习会**只在样本外段计价**，天然规避前视；任何模块出现**夏普 > 3 应优先怀疑**过拟合或数据泄露。
- 新闻情绪是 **agent-in-the-loop 三步**：抓新闻 → agent 给情绪分 → 回测，具体见 [sentiment.md](sentiment.md)。
- 定投用**资金加权 XIRR**而非时间加权收益计量，并与**一次性投入**基准对比；长期上涨品种往往一次性投入更优，定投的价值在于纪律与摊薄成本，具体见 [dca.md](dca.md)。

---

## 用例 8：端到端研究流水线（数据 → 选优 → 组合）

> 🏁 **Level 6 · 闭环** ｜ 前置：已掌握选优、风控、组合（Level 2–4），了解进阶模块（Level 5）

目标：从候选池出发，先单标的选优，再组合成轮动策略——把前面各级能力串成一条研究闭环。

1. **筛池**：用财务指标筛出优质股（见 [data-fetching.md](data-fetching.md) 的「筛选优质股票」）。
2. **单标的选优**：对候选逐个 `run_optimize.py` 找到有效策略与参数（用例 2）。
3. **组合**：把入选标的放入 `run_portfolio.py` 做动量轮动，与等权基准对比（用例 5）。
4. **加风控**：对单标的策略叠加 `--stop-loss` / `--vol-target`，观察回撤是否改善（用例 3）。
5. **出图复核**：关键结果用 `--plot` 出图，检查净值曲线、回撤区间与买卖点/权重是否合理。

解读要点：
- 每一步都以「是否跑赢基准 + 风险调整后收益」作为取舍标准。
- 全流程仅依赖历史数据，回测结论需用样本外区间二次验证后再考虑实盘。
