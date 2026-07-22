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

资金/风险比例的取值优先级：**显式 CLI 参数 > 用户风险画像 > 内置默认**。
用户可用 [`run_profile.py`](../scripts/run_profile.py) 登记风险偏好（保守/平衡/激进三档），
之后评分的建议仓位会自动因人而异（详见下文「用户风险画像联动」）。

### 市场状态上下文

评分输出同时附带**市场状态**（趋势上行/下行/震荡/高波动，由趋势效率 ER +
波动率分位判定，见 `research/regime.py`）。状态是描述性上下文，**不参与评分裁决**；
JSON 输出在 `regime` 字段（含 `regime_cn` / `suited_family` / `advice`）。

### 估值历史分位（可选，`--valuation-pct`）

绝对估值阈值（PE<20）无法区分行业差异——银行 PE 5 和成长股 PE 30 不可比。
估值历史分位回答的是「**相对于这只股票自身，当前估值贵不贵**」：

| 分位 | 含义 |
|------|------|
| PE 分位 20% | 当前 PE 处于近 N 年最低 20% 区间，相对低估 |
| PB 分位 80% | 当前 PB 处于近 N 年最高 20% 区间，相对高估 |

数据源：
- **A 股**：akshare `stock_a_lg_indicator`（乐咕乐股，免费，日频 PE/PB/PS，精确）
- **港美股**：yfinance 历史价格 / 当前 TTM EPS/BVPS 近似推算（精度有限，标注近似）

分位计算采用中位秩（并列值各计一半），避免估值恒定时分位恒为 0 或 1。
输出估值标签：低估（<20%）/ 偏低（20-40%）/ 合理（40-60%）/ 偏高（60-80%）/ 高估（>80%）。

```bash
# 附加估值历史分位（默认近 5 年）
uv run python run_score.py --symbol 600519.SH --valuation-pct

# 自定义回看年数
uv run python run_score.py --symbol 600519.SH --valuation-pct --valuation-lookback 10
```

JSON 输出在 `valuation` 字段（含 `pe_percentile` / `pb_percentile` / `valuation_label` / `source`）。

### 宏观环境上下文（可选，`--macro`）

现有市场状态识别基于价格（效率比 + 波动率），是纯技术面视角。
宏观环境上下文补充**宏观面视角**：

| 指标 | 数据源 | 用途 |
|------|--------|------|
| 10 年期国债收益率 | akshare `bond_zh_us_rate` | 利率趋势（上行→收紧 / 下行→宽松） |
| CPI 同比 | akshare `macro_china_cpi` | 通胀压力（>3% 偏高 / <0 通缩） |
| PMI | akshare `macro_china_pmi` | 经济景气（>50 扩张 / <50 收缩） |

三者组合给出宏观 regime 标签（描述性上下文，**不参与评分裁决**）：

| PMI | 利率趋势 | 宏观 regime | 含义 |
|-----|----------|-------------|------|
| ≥50 | 上行 | 经济扩张 | 顺周期受益，注意估值压力 |
| ≥50 | 下行/平稳 | 宽松有利 | 最有利环境，流动性支撑估值 |
| <50 | 上行 | 滞胀压力 | 最不利环境，股债双杀风险 |
| <50 | 下行/平稳 | 收缩衰退 | 等待政策转向信号 |

CPI 作为辅助修正：CPI>3% 时加重通胀担忧（扩张→过热提示）。

```bash
# 附加宏观环境上下文
uv run python run_score.py --symbol 600000.SH --macro

# 估值分位 + 宏观环境一起看
uv run python run_score.py --symbol 600519.SH --valuation-pct --macro
```

JSON 输出在 `macro_regime` / `macro_regime_cn` / `macro_advice` / `macro_snapshot` 字段。

> 注：宏观数据仅覆盖中国（akshare 免费接口），港美股标的同样参考中国宏观
> （A 股/港股直接相关；美股间接参考）。数据拉取失败时静默跳过，不中断评分流程。

## 结构化证据链（Evidence Pack，Agent 可引用）

`--json` 输出除 `layers`（各层人类可读理由）外，额外含 **`evidence`** 数组——
把每层的关键判断提炼为**机器可读、可编号引用**的证据条目，供 Agent 深度解读时
**引用证据而非自行推断**，从机制上避免事实性错误（如错报均线位置）。

每条证据含：

| 字段 | 含义 |
|------|------|
| `id` | 编号（E01, E02…），转述时可引用（如「因 E02 被否决」） |
| `layer` | 产生层（alpha/veto/confirm/timing/fundamental/event_risk） |
| `indicator` | 指标机器名（如 `close_vs_ma200`、`rsi14`、`macd_cross`） |
| `value` | 实际值 |
| `threshold` | 对比阈值（无则 null） |
| `triggered` | 是否触发了状态变更 |
| `impact` | 影响类型（veto/cap_watch/downgrade/none） |
| `claim` | 一句话自然语言断言（Agent 可直接引用） |

示例（结论被 MA200 否决时）：

```json
{
  "id": "E02", "layer": "veto", "indicator": "close_vs_ma200",
  "value": 7.82, "threshold": 8.15, "triggered": true, "impact": "veto",
  "claim": "收盘 7.82 < MA200(8.15)，长期趋势破坏，直接否决"
}
```

## 用户风险画像联动（个性化风控）

[`run_profile.py`](../scripts/run_profile.py) 登记的风险画像（`outputs/profile.json`）
让建议仓位因人而异：

- 未显式传 `--capital`/`--risk-pct` 时，自动读取画像的 `capital`/`risk_pct`；
- 三档预设（`conservative`/`balanced`/`aggressive`）自动填充建议的
  `risk_pct`/`max_drawdown`/`max_single_position`；
- `--json` 输出含 `profile` 上下文字段（`risk_tolerance`/`capital`/`risk_pct`/`max_drawdown`）。

```bash
# 登记为平衡型投资者，可用资金 20 万
uv run python run_profile.py --set --risk-tolerance balanced --capital 200000

# 之后评分自动用画像参数计算建议仓位（无需再传 --capital/--risk-pct）
uv run python run_score.py --symbol 600000.SH
```

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
| `--count` | 1250 | 评分至少需 250 根有效 K 线 |
| `--brief` | 关 | 只输出结论与计划价位 |
| `--replay [N]` | 关（N 默认 250） | 逐日回放 + 21/63 日前瞻收益事件研究 |
| `--calibrate [N]` | 关（N 默认 250） | 阈值自校准：网格搜索最优 alpha_score 入场阈值 |
| `--calibrate-horizon` | 21 | 校准前瞻窗口（交易日） |
| `--risk-file` | 无 | 事件风险 CSV（`date,risk,note`），high 只降级 |
| `--fetch-events` | 关 | 抓新闻素材并生成待标注风险模板（事件风险闭环第一步，仅 A 股） |
| `--cost` / `--shares` | 无 | 持仓成本/数量；缺省时依次探测账户 `account.json`、`outputs/paper_*.json` |
| `--no-fundamental` | 关 | 跳过基本面否决层（ST/连续亏损/资不抵债检查）；默认启用 |
| `--capital` / `--risk-pct` | 画像 > 10 万 / 0.01 | 建议仓位的可用资金与单笔风险预算；缺省读用户画像，无画像用默认（capital=0 关闭） |
| `--valuation-pct` | 关 | 附加估值历史分位：当前 PE/PB 在近 N 年历史中的位置 |
| `--valuation-lookback` | 5 | 估值分位回看年数 |
| `--macro` | 关 | 附加宏观环境上下文：国债利率/CPI/PMI 组合判断宏观 regime |

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

筛选基于公开财务快照（最近报告期），不构成投资建议。

### 默认阈值与两个已知副作用

默认启用五个维度：PE<20、PB<3、ROE>10%、**负债率<70%**、市值>30亿
（股息率/增速默认关闭，阈值设 0 即关闭任意维度）：

- **负债率<70% 会整体剔除银行/保险/券商**（金融业资产负债率普遍 85%~93%），
  而 A 股低 PE 低 PB 池子恰以金融股为主力。需纳入金融股时加 `--max-debt 0`。
- **静态低 PE 存在周期陷阱**：盈利周期顶部的煤炭/航运/养殖类利润高→PE 假性低，
  建议对周期行业加 `--valuation-pct` 用估值历史分位交叉验证。

### 估值历史分位增强（`--valuation-pct`）

绝对阈值（PE<20）无法区分行业差异。启用估值分位增强后，对通过初筛的候选标的
逐只拉取近 N 年 PE/PB 历史，计算当前分位并调整综合评分：

- **低分位（便宜）加分**：分位 0% → +10 分
- **高分位（贵）减分**：分位 100% → -10 分
- 分位 50% 不调整

```bash
# A 股全市场筛选 + 估值分位增强
uv run python run_screener.py --valuation-pct

# 自定义回看年数
uv run python run_screener.py --valuation-pct --valuation-lookback 10
```

> 注：估值分位增强需逐只拉取历史数据，速度较慢，建议对初筛后的少量候选使用。

典型工作流：
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
