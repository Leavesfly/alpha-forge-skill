# 数据获取参考

本文档详细说明如何用 TickFlow SDK 获取多市场行情、K 线、财务数据，以及常见数据分析场景。回测相关能力见 [backtesting.md](backtesting.md)。

## 运行环境

所有示例均可在项目的 `scripts/` 目录下运行（该环境已预装 tickflow、pandas、numpy、matplotlib）：

```bash
cd scripts
uv sync                       # 首次安装依赖
uv run python your_script.py  # 运行你的脚本
```

需要实时行情 / 分钟 K 线时，请先配置环境变量 `TICKFLOW_API_KEY`（见 SKILL.md 环境配置）。

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
