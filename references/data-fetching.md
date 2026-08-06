# 数据获取参考

本文档详细说明如何获取多市场行情、K 线、财务数据，以及常见数据分析场景。
**取数首选 `run_data.py`**（见下节）：多源自动降级、本地缓存、质量校验、来源审计一次到位；
只有 CLI 未包装的能力（实时快照、标的池、标的信息等）才直接用 TickFlow SDK。
回测相关能力见 [backtesting.md](backtesting.md)。

## 运行环境

所有示例均可在项目的 `scripts/` 目录下运行（该环境已预装 tickflow、pandas、numpy、matplotlib）：

```bash
cd scripts
uv sync                       # 首次安装依赖
uv run python your_script.py  # 运行你的脚本
```

需要实时行情 / 分钟 K 线时，请先配置环境变量 `TICKFLOW_API_KEY`（见 SKILL.md 环境配置）。

## 取数 CLI（run_data.py）

把数据层能力暴露为统一命令。**不要为了取数自己写 Python 调 SDK**——裸调会绕过
多源降级（主源挂了就断）、本地缓存（重复联网）、质量校验、交易日历新鲜度判定，
`ALPHA_FORGE_OFFLINE=1` 也不生效。

### 支持的数据种类

| `--kind` | 内容 | 数据来源与缓存 |
|---|---|---|
| `klines`（默认） | OHLCV K 线 | 五源降级链 + K 线缓存（TTL 见下）+ 质量校验 |
| `dividends` | 每股分红历史 | A 股 akshare / 港美股 openbb，缓存 7 天 |
| `valuation` | PE/PB 估值分位 | A 股精确、港美股近似，缓存 24 小时 |
| `fundamentals` | 财务指标 | 需 `TICKFLOW_API_KEY` 且账号有财务权限 |
| `macro` | 宏观快照（10Y 国债/CPI/PMI） | 经 akshare，缓存 12 小时，无需标的 |

### 命令示例

```bash
# 日 K：终端看末 10 行 + 来源与质量结论
uv run python run_data.py --symbols 600000.SH

# 结构化 JSON（Agent 消费）
uv run python run_data.py --symbols 600000.SH --count 250 --json

# 多标的 + 导出 CSV（不带路径则写 outputs/data_klines_600000SH_AAPLUS.csv）
uv run python run_data.py --symbols 600000.SH,AAPL.US --csv

# 绕过缓存强制直连数据源（排查缓存陈旧、或想记录 actual_source 时用）
uv run python run_data.py --symbols 600000.SH --no-cache

# 分钟 K（需 Key 且账号有分钟级权限）
uv run python run_data.py --symbols 600000.SH --period 5m --count 120

# 非 K 线数据
uv run python run_data.py --symbols 600000.SH --kind dividends --json
uv run python run_data.py --symbols 600000.SH --kind valuation --years 5
uv run python run_data.py --kind macro --json
```

主要参数：`--symbols`（逗号分隔，`--kind macro` 时可省）、`--kind`、`--period`、
`--count`（默认 250）、`--adjust`（默认前复权）、`--years`（估值回看年数）、
`--no-cache`、`--tail`（终端展示行数，`0` 关表格）、`--csv [PATH]`、`--json`。

### JSON 里该看什么

每个标的一条 `results[]`，除 `rows` / `first_date` / `last_date` / `records` 外，
以下三个字段是**判断这份数据能不能信**的依据：

| 字段 | 含义 | 怎么用 |
|---|---|---|
| `actual_source` | 实际命中的源（`tickflow` / `openbb` / `baostock` / `akshare` / `yfinance`） | 转述数据时说清出处；同一标的换源后数值口径可能微差 |
| `cache_hit` + `cache_meta` | 是否读的本地缓存、缓存抓取时间与末根 K 线日期 | `cache_hit=true` 且 `last_bar_date` 明显落后交易日历时，加 `--no-cache` 重取 |
| `quality` | 本次**实际返回行**的校验结论（`passed` / `summary` / `issues[]`） | `passed=false` 应提示用户，并可跑 `run_verify.py` 交叉验证 |

顶层另有 `failed`（取数失败标的数）与 `quality_failed`（质量不通过标的数），
`errors[]` 保留每个失败标的的原始异常文本。退出码按项目统一约定：

- `0`：全部成功；
- `1`：**部分**标的失败——仍输出完整 JSON（成功的在 `results[]`、失败的在 `errors[]`）；
  但若**全数失败**或整批型数据（`fundamentals` / `macro`）根本不可用，则只在 stderr
  报 `[error] ...`（带可操作的替代方案）而**不输出 JSON**，Agent 需读退出码而非直接解析 stdout；
- `2`：参数错误（如 `--kind klines` 没给 `--symbols`）。

注意 `actual_source` 只在**本次真的联网取数**时由源侧回传；命中缓存时回读缓存旁挂的
meta。字段上线前写入的旧缓存没有这项记录，终端会如实显示「未记录（旧缓存，可加
`--no-cache` 重拉以记录来源）」，而不是笼统写「未知」。

### 与其他数据 CLI 的分工

| 目的 | 命令 |
|---|---|
| 取数（日常） | `run_data.py` |
| 全链路体检：五源逐一直连、谁通谁不通、独立上游几个 | `run_doctor.py`（见「数据源体检」） |
| 同一标的两源交叉验证、逐列偏差 | `run_verify.py --symbols <代码>` |
| 批量预热缓存、缓存占用与清理 | `run_sync.py`（见「本地优先工作流」） |

## 标的代码格式

所有查询使用统一格式：**代码.市场后缀**（例如：`600000.SH`）。

### 常用市场后缀

| 后缀 | 市场 | 说明 |
|------|------|------|
| **SH** | 上海证券交易所 | 沪市 A 股、ETF、债券等 |
| **SZ** | 深圳证券交易所 | 深市 A 股、创业板、ETF 等 |
| **BJ** | 北京证券交易所 | 北交所股票 |
| **SHF** | 上海期货交易所 | 上期所期货 |
| **DCE** | 大连商品交易所 | 大商所期货 |
| **ZCE** | 郑州商品交易所 | 郑商所期货 |
| **CFX** | 中国金融期货交易所 | 中金所股指/国债期货 |
| **INE** | 上海国际能源交易中心 | 原油等期货 |
| **GFE** | 广州期货交易所 | 广期所期货 |
| **US** | 美股 | 美国证券市场 |
| **HK** | 港股 | 香港联交所 |

### 标的代码示例

- A 股：`600000.SH`（浦发银行）、`000001.SZ`（平安银行）、`920662.BJ`（北交所股票）
- 美股：`AAPL.US`（苹果）、`TSLA.US`（特斯拉）、`MSFT.US`（微软）
- 港股：`00700.HK`（腾讯控股）、`09988.HK`（阿里巴巴）
- ETF：`510300.SH`（沪深300ETF）、`159915.SZ`（创业板ETF）
- 指数：`000001.SH`（上证指数）、`399006.SZ`（创业板指数）
- 期货：`au2604.SHF`（黄金期货）、`i2605.DCE`（铁矿石期货）

### 目前支持状态

- **A 股（SH / SZ / BJ）**：已支持。可查实时行情、日 K、分钟 K、日内分时、财务数据、标的池（如 `CN_Equity_A`）等。
- **国内期货（SHF / DCE / ZCE / CFX / INE / GFE）**：支持主力合约查询。按合约代码 + 后缀查询（如 `au2604.SHF`）。
- **美股（US）**：已支持。实时行情、全量历史日 K 线（支持前复权/后复权）、除权因子、标的池（`US_Equity`）。
- **港股（HK）**：已支持。实时行情、全量历史日 K 线（支持前复权/后复权）、除权因子、标的池（`HK_Equity`）。

### 数据源与兜底降级

回测类 CLI（经 `datafeed.fetch_ohlcv`）采用多数据源链（按顺序尝试，前一源失败自动降级并在 stderr 告警）：

- **港股/美股主力 OpenBB**：日/周/月 K（Open Data Platform，免费无需 Key，
  如 `AAPL.US`、`00700.HK`；数据类型可扩展财务/期权/宏观等；不支持后复权）；
- **主源 TickFlow**：多市场全周期（A 股主力，港美股排在 OpenBB 之后兜底）；
- **兜底 baostock / akshare**：仅 A 股日/周/月 K（免费无需 Key）；
- **兜底 yfinance**：仅港股/美股日/周/月 K（免费无需 Key；不支持后复权）；
- 环境变量 `ALPHA_FORGE_DATA_SOURCE=tickflow|openbb|baostock|akshare|yfinance` 可强制只用单源；不同源的本地缓存互不混用（缓存键含源标签）。
- 依赖注意：openbb 内部走 anyio portal，`anyio<4` 会导致所有请求报
  `This portal is not running`，项目已在 pyproject 中约束 `anyio>=4.0`。

各源均输出列名归一的升序 DataFrame（`trade_date/open/high/low/close/volume`），回测链路无需感知差异。

**诚实提示（港美股「三源」的真实冗余度）**：OpenBB 走的就是 yfinance provider，
两者同上游 Yahoo——实测同一标的逐列偏差 0.0000%。Yahoo 限流时它们会**同时**失效，
港美股的独立上游实为 2 个（Yahoo + TickFlow）。用 `run_verify.py` 交叉验证选这两个源时，
报告会在 warnings 中显式标注「参考价值有限」。

### 数据源体检（run_doctor.py）

拉不到数据、或想知道此刻哪些源真的可用时：

```bash
# 三市场代表标的（600000.SH / 00700.HK / AAPL.US）× 全源真实拉取
uv run python run_doctor.py --json

# 只查指定标的/指定源
uv run python run_doctor.py --symbols AAPL.US --sources openbb,yfinance
```

绕过缓存直连每个源，逐项报告：`supports()` 判定、成功/失败与失败原因、耗时、
返回行数、末根 K 线日期、质量校验结论，并按市场汇总可用源数与**独立上游数**。
API Key 只从系统环境变量读取，输出仅保留前 6 位（`tk_043***`），不写任何文件。
与 `run_list.py --doctor` 的分工：后者查本机环境（依赖/字体/缓存目录），前者查外部数据源。

### 数据质量校验

五个源的公共出口统一做 OHLCV 质量校验（`scripts/data/quality.py`），只校验**实际返回的行**：

- **error 级**：日期重复、OHLC 含 NaN、价格 ≤ 0、OHLC 关系不自洽（如 `high < close`）；
- **warn 级**：单周期涨跌幅超板块阈值（主板 11% / 科创创业 21% / 北交所 31% / 港美股 50%，
  提示疑似未复权拆股）、相邻 K 线日期缺口过大（疑似缺失交易日）。

默认**只告警放行**（stderr，单只标的脏数据不中断全市场扫描），报告挂在
`df.attrs["quality"]`。环境变量：

- `ALPHA_FORGE_STRICT_DATA=1`：error 级问题直接抛 `DataQualityError`（实盘信号建议开启）；
- `ALPHA_FORGE_NO_QUALITY_CHECK=1`：完全跳过校验（性能逃生阀）。

### 新鲜度判定与源级熔断

- **交易日历新鲜度**：缓存是否陈旧优先按「上次抓取后是否又有交易日收盘」判定
  （`scripts/data/calendar.py`，A 股经 akshare 交易日列表、港美股内置表 + 规则推导），
  修掉纯墙钟 TTL 的漏洞：周一盘中拿到周日抓的缓存曾被判「新鲜」而漏掉当日 K 线。
  日历不可用时自动回退原 TTL；停牌/退市标的每个交易日只探一次，不会反复重拉。
- **源级熔断**：某源本进程内连续失败达 `ALPHA_FORGE_SOURCE_FAILFAST`（默认 3，设 0 关闭）
  次后，后续标的直接跳过它，不再逐只付重试与退避代价；任一次成功即清零。
  保底规则：若熔断后候选源为空，忽略熔断状态使用完整链（避免全市场扫描一次性全灭）。
- **实际命中源审计**：auto 模式下缓存 meta 记录 `actual_source`；增量更新前比对源是否一致，
  不一致直接全量重拉（不同源复权基准不同）。

### 港美股基本面与分红（OpenBB）

K 线之外，港美股的基本面数据也经 OpenBB（`scripts/data/openbb.py` 适配层，免费无需 Key）：

- **季度/年度 EPS**（CAN SLIM C/A 检查）：openbb 利润表主力，失败降级 yfinance 直连；
- **研发强度**（费雪筛选）：openbb 年度利润表主力；
- **估值分位 PE/PB Band**：openbb 关键指标主力 + 项目缓存周 K（不重复拉取）；
- **每股分红历史**（DCA 显式分红建模 `--dividends auto`）：此前仅 A 股，现已覆盖港美股；
- 筛选器深度指标保留 yfinance `.info` 主力（单次调用字段最全），失败时降级 openbb 关键指标。

### 本地优先工作流（先同步、后研究）

全市场扫描/批量回测前，可用 `run_sync.py` 一次性把股票池 K 线预热到本地缓存，
把数据工程从策略研究中剥离：

```bash
# 指定标的批量同步（默认日 K 1250 根，约 5 年）
uv run python run_sync.py --symbols 600000.SH,000001.SZ

# 全市场 A 股预同步（无 TICKFLOW_API_KEY 时自动经 akshare 快照取代码）
uv run python run_sync.py --universe CN_Equity_A --limit 500 --workers 2

# 美股池预同步（无 Key 时经东财美股快照取代码、按市值降序截断，
# 顺带预热筛选器 Phase 1 快照缓存；快照不可用时降级 S&P 500 名单）
uv run python run_sync.py --universe US_Equity --limit 500
```

- **离线模式**：同步完成后设 `ALPHA_FORGE_OFFLINE=1`，之后回测/扫描只读本地缓存
  （跳过 TTL 新鲜度检查，完全不联网）；无缓存的标的会报错并提示先同步。
- **快照/名单/基本面指标也本地优先**：筛选器的 A 股全市场快照、美股全市场
  快照、S&P 500 名单（TTL 7 天）与逐只财务指标均经同一缓存根的 `tables/`
  子目录落盘（TTL 默认 1 天，`ALPHA_FORGE_CACHE_TTL` 可调）：重复筛选（换
  预设/调阈值）命中缓存后秒级完成；远端限流/断连时自动回退陈旧缓存
  （stderr 告警标注抓取时间），离线模式同样只读本地。
- **批量面板读取**：`from data import load_panel` 直接从本地缓存装载多标的宽表
  （索引=日期，列=标的，返回 `(panel, missing)`），不查 TTL 不走网络，适合横截面研究。
- **缓存目录三级优先**（数据独立于 skill 生命周期，重装不丢）：
  `ALPHA_FORGE_CACHE_DIR` > 项目内旧 `.cache/klines`（存在且非空，老用户零迁移）
  > `~/.alpha-forge/klines`（新默认）；实际生效目录可用 `run_list.py --doctor` 查看。
- 同步走现有多源降级链，重复执行为增量更新（缓存新鲜直接跳过）；分钟级周期
  不建议全市场同步（需 API Key 且量大）；`--workers` 调高自担免费源限频风险。
- **缓存治理**：`run_sync.py --cache-usage` 查看条目数/占用空间/最旧条目；
  `run_sync.py --prune-days 180 [--dry-run]` 清理超过 N 天未更新的条目
  （按 meta 的 `fetched_at` 而非文件 mtime；退市标的的文件否则永久残留）。

## 常用 K 线周期

| 类型 | 周期代码 | 说明 |
|------|----------|------|
| 日内 | `1m` | 1 分钟 K 线 |
| 日内 | `5m` | 5 分钟 K 线 |
| 日内 | `15m` | 15 分钟 K 线 |
| 日内 | `30m` | 30 分钟 K 线 |
| 日内 | `60m` | 60 分钟 K 线 |
| 日线及以上 | `1d` | 日 K 线 |
| 日线及以上 | `1w` | 周 K 线 |
| 日线及以上 | `1M` | 月 K 线 |
| 日线及以上 | `1Q` | 季 K 线 |
| 日线及以上 | `1Y` | 年 K 线 |

## 使用示例

以下为 TickFlow SDK 直接用法，适用于 CLI 未包装的能力（实时快照、标的池、标的信息、
多期财务明细等）。**裸 SDK 没有多源降级、没有本地缓存、不做质量校验，也不受
`ALPHA_FORGE_OFFLINE` 约束**；取 K 线/分红/估值/财务请优先走上面的 `run_data.py`，
在自己的分析脚本里则用 `from datafeed import fetch_ohlcv`（同样带降级与缓存）。

### 1. 获取实时行情

```python
from tickflow import TickFlow

tf = TickFlow()

# 按标的代码查询（支持 A 股、港股、美股混合查询）
quotes = tf.quotes.get(symbols=["600000.SH", "000001.SZ", "AAPL.US", "00700.HK"])
for q in quotes:
    print(f"{q['symbol']}: {q['last_price']}")

# 按标的池查询
quotes_df = tf.quotes.get(universes=["CN_Equity_A"], as_dataframe=True)  # 全部 A 股
print(quotes_df.head())

# 获取美股/港股行情
us_quotes = tf.quotes.get(universes=["US_Equity"], as_dataframe=True)
hk_quotes = tf.quotes.get(universes=["HK_Equity"], as_dataframe=True)
```

### 2. 获取历史 K 线

```python
from tickflow import TickFlow

tf = TickFlow()

# 单只股票日 K 线（最近 100 天）
df = tf.klines.get("600000.SH", period="1d", count=100, as_dataframe=True)
print(df.tail())

# 批量获取多只股票的 K 线
symbols = ["600000.SH", "000001.SZ", "600519.SH"]
dfs = tf.klines.batch(symbols, period="1d", count=100, as_dataframe=True, show_progress=True)
print(dfs["600000.SH"].tail())
```

### 3. 获取日内分时数据

```python
from tickflow import TickFlow

tf = TickFlow()

# 获取当日 1 分钟 K 线
df = tf.klines.intraday("600000.SH", as_dataframe=True)
print(f"今日已有 {len(df)} 根分钟 K 线")
print(df.tail())

# 获取当日 5 分钟 K 线
df_5m = tf.klines.intraday("600000.SH", period="5m", as_dataframe=True)
print(df_5m.tail())

# 批量获取
symbols = ["600000.SH", "000001.SZ"]
dfs = tf.klines.intraday_batch(symbols, as_dataframe=True, show_progress=True)
```

### 4. 查询标的信息

```python
from tickflow import TickFlow

tf = TickFlow()

# 查询单个或多个标的信息
instruments = tf.instruments.batch(symbols=["600000.SH", "000001.SZ"])
for inst in instruments:
    print(f"{inst['symbol']}: {inst['name']}")
```

### 5. 获取财务数据

```python
from tickflow import TickFlow

tf = TickFlow()

# 利润表
income_df = tf.financials.income(["000001.SZ", "600519.SH"], as_dataframe=True)
print("=== 利润表（最近5期） ===")
print(income_df.tail())

# 资产负债表
balance_df = tf.financials.balance_sheet(["000001.SZ"], as_dataframe=True)
print("\n=== 资产负债表（最近3期） ===")
print(balance_df.tail(3))

# 现金流量表
cashflow_df = tf.financials.cash_flow(["000001.SZ"], as_dataframe=True)
print("\n=== 现金流量表（最近3期） ===")
print(cashflow_df.tail(3))

# 核心财务指标
metrics_df = tf.financials.metrics(["000001.SZ"], as_dataframe=True)
print("\n=== 核心财务指标（最近3期） ===")
print(metrics_df.tail(3))
```

### 6. 仅获取最新一期财务数据

```python
from tickflow import TickFlow

tf = TickFlow()

# 获取多只股票的最新财务数据
symbols = ["600519.SH", "000001.SZ", "600000.SH"]
latest = tf.financials.income(symbols, latest=True)

for symbol, records in latest.items():
    if records:
        record = records[0]
        revenue = record.get('revenue', 0) / 1e8  # 转换为亿元
        net_income = record.get('net_income', 0) / 1e8
        print(f"{symbol} 最新一期:")
        print(f"  营收: {revenue:.2f} 亿元")
        print(f"  净利润: {net_income:.2f} 亿元")
        print(f"  报告期: {record.get('period_end')}")
```

## 实用场景示例

### 下载历史数据进行回测

```python
from tickflow import TickFlow

tf = TickFlow.free()  # 免费服务足够

# 获取近 1000 天的日 K 线
df = tf.klines.get("600000.SH", period="1d", count=1000, as_dataframe=True)

# 保存为 CSV
df.to_csv("600000_SH_daily.csv", index=False)
print(f"已保存 {len(df)} 条数据")
```

### 实时监控股票价格

```python
import time
from tickflow import TickFlow

tf = TickFlow()

symbols = ["600000.SH", "000001.SZ", "AAPL.US", "00700.HK"]

while True:
    quotes = tf.quotes.get(symbols=symbols)
    for q in quotes:
        change_pct = q['ext']['change_pct'] * 100
        print(f"{q['ext']['name']} ({q['symbol']}): {q['last_price']} ({change_pct:+.2f}%)")
    print("-" * 60)
    time.sleep(5)
```

### 批量下载多只股票数据

```python
from tickflow import TickFlow

tf = TickFlow()

# 批量下载 K 线数据（支持多市场混合）
symbols = ["600000.SH", "000001.SZ", "AAPL.US", "00700.HK"]
dfs = tf.klines.batch(symbols, period="1d", count=1000, as_dataframe=True, show_progress=True)

# 逐个保存
for symbol, df in dfs.items():
    filename = f"{symbol.replace('.', '_')}_daily.csv"
    df.to_csv(filename, index=False)
    print(f"已保存 {symbol}: {len(df)} 条数据 -> {filename}")
```

### 筛选优质股票（基于财务指标）

```python
from tickflow import TickFlow

tf = TickFlow()

# 获取 A 股标的池
symbols  = tf.universes.get("CN_Equity_A")['symbols'][:200]

# 获取最新财务指标
metrics = tf.financials.metrics(symbols, latest=True, as_dataframe=True)

# 筛选条件：ROE > 15%、净利率 > 10%、负债率 < 60%
high_quality = metrics[
    (metrics['roe'] > 15) &
    (metrics['net_margin'] > 10) &
    (metrics['debt_to_asset_ratio'] < 60)
]

print(f"筛选出 {len(high_quality)} 只优质股票")
print(high_quality[['symbol', 'roe', 'net_margin', 'debt_to_asset_ratio', 'eps_diluted']].head(10))
```

### 分析财务数据趋势

```python
from tickflow import TickFlow

tf = TickFlow()

# 获取某只股票的历史财务数据
symbol = "600519.SH"
income_df = tf.financials.income([symbol], as_dataframe=True)

# 按报告期排序
income_df = income_df.sort_values('period_end')

# 计算营收和净利润同比增长率
income_df['revenue_growth'] = income_df['revenue'].pct_change(4) * 100  # 同比（4个季度）
income_df['net_income_growth'] = income_df['net_income'].pct_change(4) * 100

# 显示最近8个季度的数据
print(f"{symbol} 营收与净利润趋势（最近8个季度）：")
result = income_df[['period_end', 'revenue', 'revenue_growth', 'net_income', 'net_income_growth']].tail(8)
result['revenue'] = result['revenue'] / 1e8  # 转换为亿元
result['net_income'] = result['net_income'] / 1e8
print(result)
```

### 同行业股票财务对比

```python
from tickflow import TickFlow

tf = TickFlow()

# 对比同行业多只股票的财务指标
symbols = ["600519.SH", "000858.SZ", "600809.SH"]  # 白酒股
latest_metrics = tf.financials.metrics(symbols, latest=True)

print("=== 白酒股财务指标对比 ===")
for symbol, records in latest_metrics.items():
    if records:
        r = records[0]
        print(f"\n{symbol}:")
        print(f"  ROE: {r.get('roe', 0):.2f}%")
        print(f"  净利率: {r.get('net_margin', 0):.2f}%")
        print(f"  EPS: {r.get('eps_diluted', 0):.2f}")
        print(f"  负债率: {r.get('debt_to_asset_ratio', 0):.2f}%")
        print(f"  报告期: {r.get('period_end')}")
```

### 计算技术指标

```python
from tickflow import TickFlow

tf = TickFlow()

# 获取 K 线数据
df = tf.klines.get("600000.SH", period="1d", count=100, as_dataframe=True)

# 计算移动平均线
df["ma5"] = df["close"].rolling(5).mean()
df["ma20"] = df["close"].rolling(20).mean()

# 计算 MACD
exp1 = df["close"].ewm(span=12, adjust=False).mean()
exp2 = df["close"].ewm(span=26, adjust=False).mean()
df["macd"] = exp1 - exp2
df["signal"] = df["macd"].ewm(span=9, adjust=False).mean()

# 显示最新数据
print(df[["trade_date", "close", "ma5", "ma20", "macd", "signal"]].tail(10))
```

### 综合分析（行情 + K线 + 财务）

```python
from tickflow import TickFlow

tf = TickFlow()

symbol = "600519.SH"

# 1. 获取实时行情
quote = tf.quotes.get(symbols=[symbol])[0]
print(f"=== {quote['ext']['name']} ({symbol}) ===")
print(f"最新价: {quote['last_price']:.2f}")
print(f"涨跌幅: {quote['ext']['change_pct']*100:+.2f}%")

# 2. 获取 K 线并计算技术指标
df = tf.klines.get(symbol, period="1d", count=60, as_dataframe=True)
df["ma20"] = df["close"].rolling(20).mean()
latest_close = df.iloc[-1]['close']
latest_ma20 = df.iloc[-1]['ma20']
print(f"\n20日均线: {latest_ma20:.2f}")
print(f"位置: {'站上' if latest_close > latest_ma20 else '跌破'} MA20")

# 3. 获取最新财务指标
metrics = tf.financials.metrics([symbol], latest=True)
if metrics[symbol]:
    m = metrics[symbol][0]
    print(f"\n=== 最新财务指标 ===")
    print(f"ROE: {m.get('roe', 0):.2f}%")
    print(f"净利率: {m.get('net_margin', 0):.2f}%")
    print(f"EPS: {m.get('eps_diluted', 0):.2f}")
    print(f"报告期: {m.get('period_end')}")
```

### 构建自选股监控系统

```python
import time
from tickflow import TickFlow

tf = TickFlow()

# 自选股列表（支持 A 股、港股、美股混合）
watchlist = ["600519.SH", "000858.SZ", "AAPL.US", "00700.HK"]

# 获取财务指标（一次性加载）
latest_metrics = tf.financials.metrics(watchlist, latest=True)

print("=== 自选股监控系统 ===\n")

# 实时监控循环
while True:
    quotes = tf.quotes.get(symbols=watchlist)

    for q in quotes:
        symbol = q['symbol']
        name = q['ext']['name']
        price = q['last_price']
        change = q['ext']['change_pct'] * 100

        # 显示财务指标
        roe = 0
        if symbol in latest_metrics and latest_metrics[symbol]:
            roe = latest_metrics[symbol][0].get('roe', 0)

        status = "涨" if change > 0 else "跌"
        print(f"[{status}] {name:6s} {price:8.2f} ({change:+6.2f}%) | ROE: {roe:5.2f}%")

    print("-" * 70)
    time.sleep(10)  # 每10秒刷新
```

## 数据获取注意事项

- 免费服务仅提供历史日 K 线，不含实时行情和分钟 K 线（`TickFlow.free()`）。
- 完整服务通过环境变量 `TICKFLOW_API_KEY` 配置（`TickFlow()`）。
- 支持 A 股、港股、美股、国内期货等多市场，标的代码可混合查询。
- 美股/港股支持前复权、后复权 K 线和除权因子。
- 单次单标的最多获取 10000 根 K 线。
- 批量接口（`batch`、`intraday_batch`）适合大量标的数据获取。
- 使用 `as_dataframe=True` 参数可直接返回 pandas DataFrame。
