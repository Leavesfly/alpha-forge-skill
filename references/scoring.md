# 纪律评分与市场扫描参考

纪律评分回答的不是「这家公司好不好」，而是：**按当前价量结构、市场环境和风险约束，
现在是否适合参与**。它是项目「数据 → 研究 → 决策 → 跟踪」闭环中的**决策层**，
对应 CLI：[`run_score.py`](../scripts/run_score.py)（单股评分）与
[`run_scan.py`](../scripts/run_scan.py)（市场扫描）。

设计借鉴 [worth-buy-stocks](https://github.com/starriv/worth-buy-stocks) 的
**分层否决式架构**，并扩展为 A 股 / 港股 / 美股多市场通用（基准自动按市场选择）。

## 设计理念：分层否决 + 动态自适应

评分不是把一堆指标加权成一个黑箱总分，而是多层流水线，每层职责单一：

| 层 | 依据 | 作用 | 能做什么 | 不能做什么 |
| --- | --- | --- | --- | --- |
| ⓪ 基本面否决（可选） | ST/*ST · 连续 4 季亏损 · 资不抵债 · 由盈转亏 | 硬伤一票否决 | 否决或封顶「观察」 | 不加分 |
| ① ALPHA 加权 | 风险调整动量 55% · 相对基准强度 35% · Kaufman 趋势效率 10% | 形成排名分（0~100） | 决定初始结论 | — |
| ② 风险否决 | MA60 / MA200 · 周线 MA30 结构 · 基准 MA200（大盘 risk-off） | 封顶或否决，避免逆势开仓 | 把结论降为「否」或封顶「观察」 | 不能加分 |
| ③ 技术确认 | MACD 死叉 · RSI 过热 · KDJ 死叉 · 量价背离 | 只拦截买入 | 把「是」降为「观察」 | 不能加分、不改排名分 |
| ④ 入场时机 | 偏离 MA20 过热 · 有序回调确认 | 调整入场结论 | 把「是」降为「观察」 | 不改排名分 |

两类**独立约束**同样只有单向权力：

- **事件风险**（`--risk-file`）：近 30 天 high 风险事件把「是」降「观察」；**利好不加分**。
- **持仓状态**（`--cost` / 模拟盘自动探测）：只改操作建议（可产生「持仓需减风险」），不改排名分。

这种「单向门」设计从机制上防止因子互相污染——弱势标的不会因为一条利好被抬成「是」。

### 动态自适应阈值

确认层/时机层的固定阈值受**波动率缩放因子 vol_k** 动态调整：

- `vol_k = 当前 20 日年化波动率 / 历史中位波动率`，clamp 到 [0.8, 1.4]
- 高波动（vol_k > 1）→ 放宽阈值，避免正常波动被误杀
- 低波动（vol_k < 1）→ 收紧阈值，小偏离更有意义

受影响阈值：RSI 过热（78×vol_k）、MA20 偏离（15%×vol_k）、量能背离（0.7×vol_k）。
snapshot 中记录 `vol_k` 供审计。

### ADX 趋势强度感知

引入 ADX(14) 判断趋势强度，避免强趋势中 RSI/KDJ 超买信号钝化导致误杀：

| ADX 范围 | 行为 |
| --- | --- |
| ≥ 30（强趋势） | RSI 阈值额外 +5，KDJ 死叉仅记录不拦截 |
| ≥ 25（中等趋势） | RSI 阈值额外 +3 |
| < 25 或无数据 | 保持当前严格行为 |

snapshot 中记录 `adx14` 供审计。

### 基本面否决层（可选）

默认启用（`--no-fundamental` 可跳过），在 ALPHA 层之前执行：

| 规则 | 结果 |
| --- | --- |
| ST/*ST 标的 | 直接否决（退市风险） |
| 每股净资产 < 0 | 直接否决（资不抵债） |
| 最近 4 季 EPS 均 < 0 | 直接否决（连续亏损） |
| 由盈转亏（第 3 季 > 0，近 2 季 < 0） | 封顶「观察」 |

数据源：复用 `canslim.fundamentals`（A 股 akshare / 港美股 yfinance），获取失败时静默跳过不中断评分。

## 结论五态

| 结论码 | 中文 | 语义 |
| --- | --- | --- |
| `yes` | 是 | 排名分 ≥60 且全部否决/确认/时机检查通过，可按交易计划参与 |
| `watch` | 观察 | 有排名分但被某层封顶/降级，等待结构修复或回踩 |
| `no` | 否 | 收盘低于 MA200（逆势）或排名分 <45（动能不足） |
| `reduce_risk` | 持仓需减风险 | 持仓状态下结论为「否」：按纪律应减仓/离场，不等回本 |
| `unrated` | 无法评分 | 有效 K 线 <250 根，核心数据不足时不用猜测补齐 |

## 排名分计量

- **风险调整动量**：60 日收益 / 年化波动，经 `50×(1+tanh(x))` 压缩到 0~100；
- **相对基准强度**：60 日超额收益（vs 基准），`50×(1+tanh(5×excess))`；
- **趋势效率**：Kaufman ER20 = |20 日净变动| / 20 日逐日变动绝对值之和，×100。

基准按市场后缀自动选择：A 股（SH/SZ/BJ）→ `510300.SH`，港股 → `02800.HK`，
美股 → `SPY.US`；`--benchmark` 可覆盖。期货等无基准市场（或基准拉取失败）自动降级：
相对强度权重并入动量（0.90/0/0.10）并在理由中标注。

## 交易计划（风险管理参考，非订单指令）

仅结论为「是 / 观察」时输出：

```
入场参考 = 最新收盘          回踩参考 = MA20
止损     = 入场 − 2×ATR14    R = 入场 − 止损
止盈     = 入场 + 2R / 3R    追价上限 = 入场 + 0.5×ATR14
```

### 建议仓位（风险预算法，回答「买多少」）

交易计划附带建议仓位：**股数 = 资金 × 风险比例 / R**（按一手向下取整，
市值不超过资金）。含义：若买入后触发止损，亏损约为资金的 `--risk-pct`（默认 1%）。
`--capital`（默认 10 万，0 关闭）与 `--risk-pct` 控制；JSON 输出在 `plan.sizing`
（`suggested_shares` / `position_value` / `position_pct` / `risk_amount`）。

### 市场状态上下文

评分输出同时附带**市场状态**（趋势上行/下行/震荡/高波动，由趋势效率 ER +
波动率分位判定，见 `research/regime.py`）。状态是描述性上下文，**不参与评分裁决**；
JSON 输出在 `regime` 字段（含 `regime_cn` / `suited_family` / `advice`）。

## 命令行用法

```bash
cd scripts   # 首次先执行 uv sync

# 单股评分（A 股基准自动取 510300.SH，免费日 K 即可）
uv run python run_score.py --symbol 600000.SH

# 简短模式：只要结论与计划价位
uv run python run_score.py --symbol AAPL.US --brief

# 结合持仓成本（结论可能变为「持仓需减风险」）；未显式给出时自动探测：
# 优先统一账户（run_account.py 登记），其次模拟盘状态文件
uv run python run_score.py --symbol 600000.SH --cost 8.50 --shares 1000

# 自定义建议仓位的资金与风险预算
uv run python run_score.py --symbol 600000.SH --capital 200000 --risk-pct 0.02

# 历史回放验证 + 结论着色图
uv run python run_score.py --symbol 600519.SH --count 800 --replay --plot

# 阈值自校准：回放驱动网格搜索最优 alpha_score 入场阈值
uv run python run_score.py --symbol 600519.SH --count 800 --calibrate --calibrate-horizon 21

# 结构化 JSON（stdout 纯 JSON，含 data_hash 复现校验）
uv run python run_score.py --symbol 600000.SH --json > score.json

# 全市场扫描：打开股票池扫描（需 API Key）或手动标的列表（免费日 K）
uv run python run_scan.py --symbols 600000.SH,600519.SH,000858.SZ,AAPL.US
uv run python run_scan.py --universe CN_Equity_A --limit 100 --pool 30 --top 10 --json

# 按评分裁决纸面执行（决策→跟踪闭环，详见 live-signal.md）
uv run python run_paper.py --symbol 600000.SH --mode score
```

### 主要参数（run_score.py）

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--benchmark` | 按市场自动 | 相对强度与大盘环境的基准 |
| `--count` | 500 | 评分至少需 250 根有效 K 线 |
| `--brief` | 关 | 只输出结论与计划价位 |
| `--replay [N]` | 关（N 默认 250） | 逐日回放 + 21/63 日前瞻收益事件研究 |
| `--calibrate [N]` | 关（N 默认 250） | 阈值自校准：网格搜索最优 alpha_score 入场阈值 |
| `--calibrate-horizon` | 21 | 校准前瞻窗口（交易日） |
| `--risk-file` | 无 | 事件风险 CSV（`date,risk,note`），high 只降级 |
| `--fetch-events` | 关 | 抓新闻素材并生成待标注风险模板（事件风险闭环第一步，仅 A 股） |
| `--cost` / `--shares` | 无 | 持仓成本/数量；缺省时依次探测账户 `account.json`、`outputs/paper_*.json` |
| `--no-fundamental` | 关 | 跳过基本面否决层（ST/连续亏损/资不抵债检查）；默认启用 |
| `--capital` / `--risk-pct` | 10 万 / 0.01 | 建议仓位的可用资金与单笔风险预算（capital=0 关闭） |

### 主要参数（run_scan.py）

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--symbols` / `--universe` | — | 扫描范围（二选一） |
| `--pool` | 不过滤 | 流动性初筛：按近 20 日均成交额保留前 N 名 |
| `--top` | 20 | 达标候选最多输出数 |
| `--min-score` | 60 | 达标候选最低排名分 |
| `--exclude-held` | 关 | 排除统一账户已持标的；缺省仅标注「已持有」 |

被否决/降级候选**单独列出主要原因**（信息不丢失）；拉取失败标的跳过不中断。

## 回放验证（评分是否有用，用数据说话）

`--replay` 对最近 N 个交易日逐日只用截至当日数据重算结论（final-only，无前视），
把「非是 → 是」的信号日作为事件，复用事件研究模块计算 **21/63 日前瞻绝对与
相对基准收益（CAAR）**。事件日取信号的**次一交易日**，不含信号当日收益。

诚实约定：**非重叠样本 < 10 时结果标注 inconclusive**，不能据此确认或否定评分
有效性——这正是 worth-buy-stocks 用同款流程对自身评分得出 inconclusive 的做法。

## 事件风险打分流程（agent-in-the-loop，A 股）

三步交互，`--fetch-events` 一步完成素材准备：

```bash
# 1. 抓新闻素材并生成待标注模板（events_<标的>.csv + risk_<标的>.csv）
uv run python run_score.py --symbol 600000.SH --fetch-events

# 2. agent 阅读 ../outputs/events_600000SH.csv，在 ../outputs/risk_600000SH.csv 的
#    risk 列逐行填入 high/medium/low（无风险的行删除，note 可改写为风险要点）：
#    date,risk,note      risk ∈ {high, medium, low}
#    2026-07-10,high,监管立案调查

# 3. 带风险文件重新评分
uv run python run_score.py --symbol 600000.SH --risk-file ../outputs/risk_600000SH.csv
```

注意：风险标注只识别**降级风险**（诉讼、监管、商誉减值、财报暴雷预警等）；
利好、目标价上调与估值叙事**不加分**。

## 与其他模块的关系

| 模块 | 关系 |
| --- | --- |
| `run_backtest.py` | 评分给「当下裁决」，回测验证「策略历史表现」，互为补充 |
| `run_signal.py` | 信号服务输出单策略信号；评分输出综合多层裁决 + 具体价位 |
| `run_paper.py` | `--mode score` 直接按评分裁决纸面执行（是=建仓、否/减风险=离场、观察=持有）；反向地，评分也自动探测模拟盘持仓（双向联动） |
| `run_event.py` | 回放验证复用其 AAR/CAAR 事件研究引擎 |

## 价值筛选（run_screener.py）——互补能力

与纪律评分/市场扫描定位不同，[`run_screener.py`](../scripts/run_screener.py) 回答的是「**哪些标的被低估**」：

| 命令 | 定位 | 数据依据 | 输出 |
|------|------|----------|------|
| `run_score.py` | 现在能不能买（趋势动量纪律） | 价量结构 + 市场环境 | 结论五态 + 交易计划 |
| `run_scan.py` | 批量纪律过滤（强势标的） | 同上，批量执行 | 达标/降级候选分列 |
| `run_screener.py` | 哪些被低估（基本面价值发现） | PE/PB/ROE/负债/分红/增速 | 综合评分排序候选 |
| `run_factor.py` | 相对好坏（截面排名） | 多因子打分 | 分位选股 + 分层回测 |

筛选基于公开财务快照（最近报告期），不构成投资建议。典型工作流：
`run_screener.py`（发现低估候选）→ `run_score.py`（技术面复核）→ `run_paper.py --mode score`（纸面跟踪）。

## 局限与免责声明

- 评分阈值（均线窗口、RSI 界限、偏离分档、tanh 标尺）为**纪律预设值**，
  未经过样本外验证与完整熊市周期检验；`--replay` 是自证工具而非有效性背书。
  `--calibrate` 可回放驱动网格搜索最优入场阈值（胜率/平均前瞻收益），
  但校准结果同样受样本内偏差影响，调整阈值需自知偏离原著标准的风险。
- 动态阈值与 ADX 感知是「放宽/收紧」而非「取消」，纪律底线不变；
  vol_k 与 ADX 本身也有滞后性，状态切换只能事后确认。
- 基本面否决层依赖外部数据源（akshare/yfinance），数据延迟或缺失时自动跳过，
  不构成对基本面的全面审计。
- 评分是**执行纪律的统一标尺**，不应理解为已验证的选股 alpha；
  扫描是纪律过滤，不是收益预测。
- 计划价位来自 ATR 与均线结构，用于风险管理，**不是订单指令**；不构成投资建议。
