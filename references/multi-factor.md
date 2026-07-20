# 多因子选股参考

本文档说明 `scripts/factors/` 的多因子选股模型：五类因子打分合成、分位选股，
并复用组合引擎做分层回测验证因子有效性（Alpha）。单标的策略与组合轮动分别见
[strategies.md](strategies.md)、[portfolio.md](portfolio.md)。

## 数据依赖与权限

- **价格因子（动量、低波动）**：仅需收盘价，免费日 K 即可。
- **基本面因子（价值、质量、规模）**：需 `TICKFLOW_API_KEY` 且账号具备**财务数据查询权限**。
  无权限 / 未配置 / 接口异常时，`fetch_fundamentals` 返回 None，相关因子**自动跳过**并提示，
  模型仅用可用因子继续运行。
- 股票池由 `tf.universes.get(name)['symbols']` 提供（如 `CN_Equity_A` / `US_Equity` / `HK_Equity`）。

## 架构总览

```
股票池/标的 (datafeed.fetch_universe / fetch_prices / fetch_fundamentals)
  -> 因子计算 (factors.library)      各因子 -> 日期×标的 矩阵（越大越好）
  -> 预处理合成 (factors.preprocess) MAD去极值 + z-score + 等权合成综合得分
  -> 选股+分层 (factors.model)       Top分位组合 + N层，复用 portfolio 引擎回测
  -> 可视化 (factors.plot)           Top vs 基准、分层净值、分层收益柱状
```

## 因子清单（`factors/library.py`）

所有因子统一为「数值越大越好」，横截面打分时高分对应偏好方向。

| 因子名 | 类别 | 方向处理 | 数据源 |
|--------|------|----------|--------|
| `momentum` | price | 过去 lookback 期收益率 | 收盘价 |
| `low_vol` | price | -收益率滚动标准差（低波异象） | 收盘价 |
| `reversal` | price | -近 1 月（≤21 期）收益（短期反转，超跌得高分） | 收盘价 |
| `sharpe_mom` | price | 滚动均值收益/滚动波动（风险调整动量，涨得稳者高分） | 收盘价 |
| `consistency` | price | 滚动窗口内上涨天数占比（趋势一致性） | 收盘价 |
| `value_ep` | value | EP=1/PE（仅正 PE） | 财务 |
| `value_bp` | value | BP=1/PB（仅正 PB） | 财务 |
| `quality_roe` | quality | ROE（正向） | 财务 |
| `quality_debt` | quality | -资产负债率（低负债得高分） | 财务 |
| `size` | size | -log(总市值)（小市值得高分） | 财务 |

字段按候选名（忽略大小写）动态探测，缺失则该因子跳过。

## 打分与合成（`factors/preprocess.py`）

1. **去极值**：横截面 MAD，中位数 ± n×1.4826×MAD 截断。
2. **标准化**：横截面 z-score。
3. **合成**：各因子标准化后按权重（默认等权）求「有效因子加权平均 z 分」，对缺失鲁棒。

## 选股与分层回测（`factors/model.py`）

`run_factor_model(prices, fundamentals, factors, top_quantile, layers, lookback, lag_days, rebalance, ...)`

- 每个调仓日计算综合得分，选前 `top_quantile` 分位等权建仓 → 复用 `portfolio.run_portfolio_backtest`。
- 分层：按得分从高到低分 `layers` 层，各层等权分别回测。理想情况下 L1（最高分层）表现最好、逐层递减，说明因子有区分度。
- 返回 `FactorResult`：Top 组合结果、各分层结果、最新一期选股清单、被跳过的因子。

## 时点对齐与前视规避

- 财务因子按报告期（`period_end`）+ 滞后（`--lag-days`，默认 60 日）前向填充到交易日，模拟财报公告延迟，避免用未公布数据。
- 价格因子用 `pct_change` / 滚动窗口，均为当日可得。
- 组合引擎对权重统一 `shift(1)`（当日选股次日建仓）。

## CLI 用法

```bash
cd scripts && uv sync

# 股票池前 30 只，全因子（财务因子需权限，否则自动降级为价格因子）
uv run python run_factor.py --universe CN_Equity_A --limit 30

# 仅价格因子（无需财务权限）：动量+低波，也可加反转/风险调整动量/一致性
uv run python run_factor.py \
  --symbols 600000.SH,000001.SZ,600519.SH,000858.SZ,600809.SH,601318.SH \
  --factors momentum,low_vol --plot
uv run python run_factor.py \
  --symbols 600000.SH,000001.SZ,600519.SH,000858.SZ,600809.SH \
  --factors momentum,reversal,sharpe_mom

# 指定选股分位与分层数
uv run python run_factor.py --universe CN_Equity_A --limit 50 --top-quantile 0.2 --layers 5
```

参数：`--universe`/`--symbols`（二选一）、`--limit`、`--factors`（默认全部）、`--top-quantile`（默认 0.2）、
`--layers`（默认 5）、`--lookback`、`--lag-days`、`--rebalance`、`--period`、`--count`、
`--commission`/`--slippage`、`--plot`/`--output`。

输出：Top 组合 vs 等权基准绩效、各分层累计收益（单调性检验）、最新选股清单。

## 编程方式调用

```python
from datafeed import fetch_prices, fetch_fundamentals, fetch_universe
from factors import run_factor_model

symbols = fetch_universe("CN_Equity_A", limit=30)
prices = fetch_prices(symbols, period="1d", count=500)
fundamentals = fetch_fundamentals(list(prices.columns))  # 无权限返回 None
result = run_factor_model(prices, fundamentals, top_quantile=0.2, layers=5)
print(result.factors_used, result.skipped)
print(result.latest_picks)
```

## 注意事项与局限

- 基本面因子依赖财务数据权限；无权限时仅价格因子可用（严格意义上不构成完整基本面多因子）。
- 采用固定目标权重的简化再平衡（与组合引擎一致），未建模日内漂移与冲击成本。
- 未做行业/市值中性化，因子暴露可能受行业与规模影响（后续可选增强）。
- 财务历史期数有限会缩短有效回测区间；多标的按共同交易日对齐。
- 回测结果不代表未来收益，因子存在失效与拥挤风险，需样本外验证。
