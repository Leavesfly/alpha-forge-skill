# 新闻情绪交易参考

让 AI 实时读新闻给出情绪判断，再把情绪转化为持仓信号回测。这是参考文章"黄灯区"里
最"AI 原生"的流派：**处理海量文本、判断情绪倾向**曾是机构专属，如今由 agent 的 LLM
直接完成，无需本地 NLP 模型或额外 LLM Key。

> 相关能力：数据获取见 [data-fetching.md](data-fetching.md)，回测引擎见 [backtesting.md](backtesting.md)。

## 数据源与限制

- **数据源**：akshare `ak.stock_news_em`（东方财富个股新闻），免费、无需 Key。
- **仅支持 A 股**（SH/SZ/BJ）；非 A 股标的会直接报错。
- **历史深度有限**：单次仅返回**最近约 100 条**新闻，无法按任意历史区间深挖。
  因此本模块定位为「**近端短窗口 + AI 原生工作流**」演示，而非长周期策略验证；
  回测窗口偏短，结论请谨慎对待。

## agent-in-the-loop 三步工作流

情绪判断由 agent 的 LLM 完成，脚本与 agent 通过 CSV 文件契约衔接：

```
1) fetch     脚本抓新闻 → ../outputs/news_<标的>.csv
                        → ../outputs/sentiment_<标的>.csv（待填模板）
                            │
2) (agent)   agent 读 news_<标的>.csv，逐条判断情绪，
             把分数写入 sentiment_<标的>.csv 的 score 列
                            │
3) backtest  脚本读打分 → 聚合日度情绪 → {-1,0,1} 信号 → 回测/出图
```

### 文件契约

**`news_<标的>.csv`**（脚本产出，供 agent 阅读）：

| 列 | 说明 |
|----|------|
| `date` | 新闻发布时间 |
| `title` | 新闻标题 |
| `content` | 新闻正文 |
| `source` | 来源 |
| `url` | 链接 |

**`sentiment_<标的>.csv`**（agent 填写，脚本读取）：

| 列 | 说明 |
|----|------|
| `date` | 与新闻一一对应（已预填，勿改顺序） |
| `score` | 情绪分，取值 **[-1, 1]** |

score 语义：`+1` 极度利好 / `+0.5` 偏利好 / `0` 中性或无关 / `-0.5` 偏利空 / `-1` 极度利空；
无法判断记 `0`。文件头以 `#` 开头的说明行会被自动跳过。

## 信号逻辑

1. 逐条情绪分按「日」聚合为日度情绪均值；
2. 对齐到交易日，无新闻日按情绪持续效应**前向填充 `hold` 天**；
3. 滚动平滑（`smooth` 窗口）后用**中性带阈值**转持仓：
   - 情绪 `> entry` → 做多（1）
   - 情绪 `< -entry` 且 `--allow-short` → 做空（-1），否则空仓（0）
   - 介于其间 → 空仓（0）
4. 信号交由回测引擎 `shift(1)` 次日生效，规避前视。

## CLI 用法

```bash
cd scripts   # 首次先 uv sync

# 第一步：抓新闻 + 生成待填打分模板
uv run python run_sentiment.py --symbol 600000.SH --stage fetch

# 第二步：agent 阅读 ../outputs/news_600000SH.csv，把情绪分写入 ../outputs/sentiment_600000SH.csv

# 第三步：读取打分，回测并出图
uv run python run_sentiment.py --symbol 600000.SH --stage backtest --plot

# 允许做空（极端利空做空）
uv run python run_sentiment.py --symbol 600000.SH --stage backtest --allow-short --plot

# 无 agent 时用关键词词典兜底端到端跑通（质量有限，仅演示）
uv run python run_sentiment.py --symbol 600000.SH --stage backtest --use-lexicon --plot
```

### 参数说明

| 参数 | 默认 | 说明 |
|------|------|------|
| `--stage` | 必填 | `fetch`（抓新闻+模板）或 `backtest`（打分回测） |
| `--entry` | 0.2 | 开仓情绪阈值（\|情绪\| 超过才入场） |
| `--hold` | 5 | 新闻情绪持续天数（无新闻日前向填充上限） |
| `--smooth` | 3 | 日度情绪滚动平滑窗口 |
| `--allow-short` | 关 | 极端利空时输出 -1 |
| `--use-lexicon` | 关 | 用关键词词典兜底打分（无 agent 参与时） |
| `--count` | 250 | 回测 K 线数量 |

## 回测铁律

- 新闻历史仅约 100 条、回测窗口偏短，**样本外意义有限**，避免据此做强结论；
- 夏普比率 > 3 时 CLI 会打印怀疑提示——优先排查数据泄露、样本偏差或新闻覆盖不足。

## 输出

- 文本报告：情绪策略绩效、基准对比、新闻条数与有新闻天数。
- 图表（`--plot`）：净值 vs 基准、价格与买卖点、日度情绪柱状，输出到项目根目录 `outputs/sentiment_<标的>.png`（与 `scripts/` 平级）。
