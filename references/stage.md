# 个股阶段定位（Stage Analysis）

回答的不是「能不能买」，而是**现在走到哪了**：把标的定位到「台阶式循环」的某一段。

```
低位平台 ──突破──> 上升推进 ──> 高位平台 ──跌破──> 下降趋势 ──> （重新筑底）
   base     breakout    advance     top      breakdown    decline
```

CLI：[`run_stage.py`](../scripts/run_stage.py)，引擎：[`stage/`](../scripts/stage/)
（`box.py` 箱体原语 · `engine.py` 七态判定 · `present.py` 渲染 · `plot.py` 可视化）。

## 为什么需要它（与既有能力的分工）

| 模块 | 回答的问题 | 局限 |
|---|---|---|
| `research/regime.py`（run_score 输出的 regime） | 波动/趋势的**统计属性**：趋势上行/下行/震荡/高波动 | **没有「位置」概念**——低位平台与高位平台都会被判为「震荡」，而二者一个是买入点前夜、一个是卖出前夜 |
| `scoring/`（run_score.py 买点三灯） | **能不能买**：价/势/时三维 + 决策矩阵七态 | 需基本面/估值/基准，较重；不描述标的在循环中的位置 |
| `examples/wisdom_rule.toml`（run_custom.py） | 这套打法**赚不赚钱**：机械信号 + 回测净值 | 输出 0/1 仓位，不输出「现在是什么状态」 |
| **`stage/`（本模块）** | **现在在循环哪一段** + 关键价位 | 纯价量、不含估值判断；描述性统计，有滞后 |

四者各自独立、按需单独运行：阶段说「在哪」，三灯说「能不能买」，wisdom 规则说「历史上这么做行不行」。**阶段与三灯互不串联**：问阶段就只跑 run_stage，问买卖就只跑 run_score，两者同时被问到才分别跑并把结论分开陈述。

## 七态

| stage | 中文 | 判定要点 | 应对姿态 |
|---|---|---|---|
| `base` | 筑底整理 | 箱体成立（位置分位 ≤0.4 为低位筑底 `high`，否则为中位整理 `medium`） | 观察等待，记下上沿作触发价 |
| `breakout` | 突破确认 | 近 `confirm_days` 内收盘有效上穿「前置箱体」上沿 | 关键点出现，跟进验证量能持续性 |
| `advance` | 上升推进 | MA20>MA60>MA200 + 站上 MA60 + ER≥0.25 | 持有/顺势加码，跟踪 MA20 |
| `top` | 高位派发 | 箱体成立 + 位置分位 ≥0.6 + 箱体前 120 日升幅 ≥30% | 警戒不加仓，跌破下沿即离场 |
| `breakdown` | 破位下行 | 近 `confirm_days` 内有效下破前置箱体下沿；箱体不成立时以「首次跌破下行的 MA60」兜底 | 回避/减风险，先离场后判断 |
| `decline` | 下降趋势 | MA20<MA60 + 位于 MA60 下方 + ER≥0.25 | 回避不抄底，等新平台 |
| `unknown` | 无法判定 | K 线 < 250 根 | 加大 `--count` 重试 |

**兜底诚实**：箱体不成立且无明确趋势时归 `base` 且 `confidence="low"`、`rule` 标注
「结构不清」、`box.valid=false`——不编造状态。

> 为何 `base` 的中文叫「筑底整理」而不是「低位筑底」：它是一个**伞形态**，涵盖
> 低位箱体、中位箱体、结构不清三种子情况。若一律叫「低位」，位置分位 50% 的
> 标的会被误读为底部区域。具体处于哪种子情况，看 `rule`、`confidence` 与
> `price_position`；CLI 的 `summary` 也会相应措辞为「低位筑底 / 中位整理 / 结构不清」。

## 判定原语

### 1. 箱体（平台）识别

箱体成立需**同时**满足四条，缺一不可（否则趋势段会被误认成平台）：

| 条件 | 默认阈值 | 常量 |
|---|---|---|
| 相对高度 `(上沿-下沿)/中轴` | ≤ 15% | `MAX_BOX_HEIGHT` |
| 窗口 Kaufman 效率比 | ≤ 0.30 | `MAX_BOX_ER` |
| 上沿触及次数 | ≥ 2 | `MIN_TOUCHES` |
| 下沿触及次数 | ≥ 2 | `MIN_TOUCHES` |

触及容差 2%（`TOUCH_TOLERANCE`）；有效突破/破位价再加 0.5% 缓冲（`BREAK_BUFFER`），
过滤贴沿假动作。

> **为什么必须 `exclude_tail`**：突破发生后，「近 60 日最高价」就是突破本身创出的
> 新高，用含突破 K 线的窗口算上沿会让「收盘上穿上沿」恒为假。故引擎算两个箱体：
> `box_recent`（含最新 K 线，用于给当前上下沿价位与 base/top 判定）与
> `box_prior`（`exclude_tail=confirm_days`，用于判突破/破位）。
> 这是本模块最容易踩的坑，已有回归测试锁定。

### 2. 位置分位（regime 缺失的那一维）

`price_position` = 当前价在近 250 日 `min-max` 区间中的位置（0~1）。
低位平台与高位平台的箱体几何可以完全相同，**只有位置分位能区分二者**。

### 3. 趋势结构

MA20/MA60/MA200 排列、MA60 斜率（20 日）、60 日效率比。

### 4. 量能

放量确认（突破当根量 ≥ 1.5× 其 20 日均量）**只调整置信度、不改变阶段**：
放量 → `high`，无量 → `medium` 并在证据链标注「可信度打折」。

### 5. 前置升幅

高位派发要求平台之前确有一段升幅（箱体中轴 / 箱体前 120 根收盘 - 1 ≥ 30%），
否则只是高位横盘的假象，降级为 `base`。

## 判定优先级

箱体类状态先于趋势类（箱体成立已隐含低效率比，二者天然互斥）：

```
1. K 线不足 250                              -> unknown
2. 近 N 日上穿前置箱体上沿                    -> breakout
3. 近 N 日下破前置箱体下沿                    -> breakdown
4. 当前箱体成立 + 位置≥0.6 + 前置升幅≥30%     -> top
5. 当前箱体成立                               -> base（低位 high / 中位 medium）
6. 箱体不成立 + 首次跌破下行 MA60             -> breakdown（兜底）
7. MA 多头排列 + 站上 MA60 + ER≥0.25         -> advance
8. MA 空头排列 + MA60 下方 + ER≥0.25         -> decline
9. 其余                                       -> base + confidence=low
```

突破与破位同时出现时取**更近**的那次（后发生的事件覆盖先前的）。
第 6 条必须排在箱体态之后：否则低位平台上 MA60 微幅下行 + 价格下穿均线会被误判破位。

## 运行

```bash
# 当前阶段 + 关键价位
uv run python run_stage.py --symbol 600000.SH

# 只要结论
uv run python run_stage.py --symbol AAPL.US --brief

# 阶段迁移轨迹（最近 120 日逐日重算，无前视）+ 阶段着色图
uv run python run_stage.py --symbol 600519.SH --history 120 --plot

# 平台周期更长时放宽箱体窗口
uv run python run_stage.py --symbol 000001.SZ --window 90 --json
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--count` | 1250 | K 线数量（约 5 年）；有效判定至少需 250 根 |
| `--window` | 60 | 箱体窗口；平台周期更长可放宽到 90/120 |
| `--confirm-days` | 5 | 突破/破位检验窗口 |
| `--history [N]` | 120 | 阶段迁移轨迹（逐日重算） |

## JSON 输出字段

```jsonc
{
  "stage": "breakout",            // 七态机器码
  "stage_cn": "突破确认",
  "confidence": "high",           // high/medium/low
  "rule": "近 N 日上穿前置箱体上沿",  // 命中的判定规则（可解释）
  "price_position": 0.42,         // 近 250 日区间位置
  "box": { "valid": true, "high": 105.0, "low": 96.0, "mid": 100.5,
           "height_pct": 0.09, "er": 0.08,
           "touches_high": 6, "touches_low": 5,
           "window": 60, "exclude_tail": 5, "reason": "" },
  "trigger": { "breakout_price": 105.53, "breakdown_price": 95.52,
               "distance_to_breakout_pct": 0.012,
               "distance_to_breakdown_pct": -0.084, "box_valid": true },
  "structure": { "close": 104.2, "ma20": 101.3, "ma60": 99.8, "ma200": 97.1,
                 "ma60_slope": 0.021, "er": 0.31,
                 "breakout_vol_ratio": 2.7 },
  "evidence": [ { "kind": "box|break|trend|position|volume|prior_gain",
                  "text": "…", "value": 105.0 } ],
  "posture": { "posture": "关键点已出现", "note": "…" },
  "history": { "days": 120, "series": [...], "transitions": [...], "current": "breakout" },
  "summary": "…（可直接转述的白话结论）",
  "next_steps": [ { "action": "score", "reason": "…", "command": "…" } ]
}
```

`next_steps` 只含阶段模块自身的动作：无条件的 `history`（回看迁移轨迹确认稳定性）
与条件项 `stage == breakout` → wisdom 规则回测。**不包含任何 run_score 步骤**——
阶段定位与三灯是两个互不干扰的独立能力，不引导链式调用。

## 局限（必须向用户说明）

- **描述性统计，不是预测**：与 regime 一样存在滞后，状态只能事后确认；
  箱体刚形成的边界期判定可能抖动，用 `--history` 观察稳定性。
- **阈值是纪律预设值**：15%/0.30/2 次/0.4/0.6/30%/1.5× 均未经样本外验证，
  与三灯阈值同等定位；不同市场/标的的平台形态差异大，可用 `--window` 调整。
- **只看价量**：不含基本面与估值判断——`base` 不等于「便宜」，
  `advance` 不等于「值得买」。若用户另行关心估值与硬伤，可**单独**跑三灯的「价」灯（不要与阶段结论混述）。
- **不做全市场扫描**：单标的定位为主，批量筛选请用 `run_screener.py` / `run_scan.py`。
