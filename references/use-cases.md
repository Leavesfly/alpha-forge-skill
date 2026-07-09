# 典型用例（Use Cases）

本文档把数据获取、策略、回测、风控、寻优与组合能力串成常见工作流。所有命令均在
`scripts/` 目录下运行（首次需 `cd scripts && uv sync`）。策略/参数细节见
[strategies.md](strategies.md)、[backtesting.md](backtesting.md)、[portfolio.md](portfolio.md)。

---

## 用例 1：快速评估一只股票，选出表现最好的策略

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

目标：仅用免费历史日 K 做研究，下载数据并回测。

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

## 用例 8：端到端研究流水线（数据 → 选优 → 组合）

目标：从候选池出发，先单标的选优，再组合成轮动策略。

1. **筛池**：用财务指标筛出优质股（见 [data-fetching.md](data-fetching.md) 的「筛选优质股票」）。
2. **单标的选优**：对候选逐个 `run_optimize.py` 找到有效策略与参数。
3. **组合**：把入选标的放入 `run_portfolio.py` 做动量轮动，与等权基准对比。
4. **加风控**：对单标的策略叠加 `--stop-loss` / `--vol-target`，观察回撤是否改善。
5. **出图复核**：关键结果用 `--plot` 出图，检查净值曲线、回撤区间与买卖点/权重是否合理。

解读要点：
- 每一步都以「是否跑赢基准 + 风险调整后收益」作为取舍标准。
- 全流程仅依赖历史数据，回测结论需用样本外区间二次验证后再考虑实盘。
