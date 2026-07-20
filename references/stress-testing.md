# 压力测试与配置文件参考

## 压力测试（risk/stress.py）

回答两个问题：策略在**历史极端行情**中表现如何？如果未来出现**单日暴跌/波动翻倍**，回撤会恶化到什么程度？

```bash
# 单标的回测 + 压力测试（结果同步写入 --report 的 HTML）
uv run python run_backtest.py --symbol 600000.SH --strategy ma_cross --stress --report

# 组合回测 + 压力测试
uv run python run_portfolio.py --symbols 600000.SH,000001.SZ,600519.SH --strategy momentum --stress
```

### 历史情景重放

若回测区间覆盖以下预置窗口，输出该窗口内策略的期间收益、最大回撤与恢复天数：

| 情景 | 区间 |
|------|------|
| 2015 股灾 | 2015-06-12 ~ 2015-08-26 |
| 2016 熔断 | 2016-01-01 ~ 2016-01-31 |
| 2018 熊市 | 2018-01-24 ~ 2018-12-28 |
| 2020-03 流动性危机 | 2020-02-20 ~ 2020-03-23 |
| 2022 加息熊市 | 2022-01-01 ~ 2022-10-31 |
| 2024-02 微盘股踩踏 | 2024-01-15 ~ 2024-02-08 |

编程调用可传自定义情景：`historical_scenarios(returns, scenarios=[("名称","起","止"), ...])`。

### 蒙特卡洛冲击

对策略收益序列做 1000 次自助抽样（seed 固定可复现），输出最大回撤分布的 p50/p95/p99：

- **bootstrap 基线**：有放回重抽样，衡量路径风险（同样的日收益换个顺序，回撤可能差很多）；
- **单日冲击 -5% / -10%**：随机位置注入极端单日亏损；
- **波动 x2**：收益围绕均值放大 2 倍，模拟波动政权切换。

解读：p95 是「20 次里最差的 1 次」的回撤水平；若 p95 已超出你的心理承受线，应降低仓位（如 `--vol-target`）。

### 局限

压力测试基于策略历史收益序列，隐含「策略行为模式不变」假设；不能覆盖策略在极端行情下**信号本身失效**的情形（如流动性枯竭导致无法成交），结论用于风险预算参考而非损失上界。

## 配置文件（--config）

全部 13 个 `run_*.py` 均支持从 TOML 注入参数默认值：

```bash
uv run python run_backtest.py --config examples/backtest.toml
# 显式命令行参数永远优先于配置文件
uv run python run_backtest.py --config examples/backtest.toml --strategy macd
```

```toml
# examples/backtest.toml
symbol = "600000.SH"
strategy = "ma_cross"
count = 800
market = "astock"
exec-price = "open"
limit-board = "main"
stop-loss = 0.05
```

- 键名与 CLI 参数一致（`exec-price` / `exec_price` 均可）；
- 未知键直接报错，并给出近似键建议与本命令可用键列表，防止拼写错误静默失效；
- 优先级：显式命令行 > 配置文件 > 内置默认值。
