# 机器学习策略参考

用可插拔的机器学习模型（LightGBM / Ridge / Logistic）学习「技术指标特征」与
「未来收益方向」的关系，通过**走步（walk-forward）重训练**并**只在样本外（OOS）段计价**，
天然规避前视与未来数据泄露。定位为参考文章
"黄灯区"的机器学习流派：AI 能显著降低门槛，但**最容易自己骗自己**，因此本模块把
"样本外验证"做成不可绕过的默认行为。

> 相关能力：数据获取见 [data-fetching.md](data-fetching.md)，回测引擎与绩效指标见
> [backtesting.md](backtesting.md)。

## 设计原理

### 1. 特征工程（`ml/features.py`）

从 OHLCV 构造一组**因果特征**（时点 t 只依赖 ≤ t 的数据）：

| 特征族 | 说明 |
|--------|------|
| 多窗口动量 `roc_{1,5,10,20,60}` | 不同回看期的收益率 |
| 滚动波动 `vol_{5,10,20}` | 收益率滚动标准差 |
| 均线比 `ma_ratio_{5,10,20,60}` | 收盘价相对均线的偏离 |
| RSI `rsi_{6,14}` | Wilder 平滑 RSI（归一到 0~1） |
| MACD `macd_hist` | (DIF - DEA) / 收盘价 |
| 量能 `vol_chg_{5,20}` | 成交量相对均量变化（有量时启用） |
| 波幅 `hl_range(_ma5)` | (高-低)/收盘（有高低价时启用） |

### 2. 标签（`ml/model.py::build_target` / `ml/labeling.py`）

- **fixed（默认）**：未来 `horizon` 期收益方向作为二分类标签（1=上涨，0=下跌/持平）。
- **triple（`--label triple`，三重障碍，López de Prado AFML）**：为每个 bar 设
  止盈线（`pt_mult`×滚动波动率）、止损线（`sl_mult`×波动率）与最长持有期
  （`horizon`）三道退出，按先触发者定标签，比固定持有期更贴近真实交易的
  止盈止损行为。

标签使用未来价格，**仅用于训练**；走步逻辑确保训练标签在测试期开始前已完全实现。

### 3. 走步样本外验证（核心）

```
时间轴 →
[  预热  ][   训练窗(rolling)   ]→ horizon 滞后 →[ 测试块 ]→[ 测试块 ]→ ...
                                                  ↑ 只有这里之后的净值被统计（OOS）
```

- 每个测试块开始前，仅用「其之前、且目标已实现（i + horizon < 测试起点）」的滚动窗口训练；
- 首个预测点之前信号一律置 0，保证净值曲线**全部是样本外**；
- 训练样本不足或单一类别时跳过该块（信号保持 0）。

### 4. 可插拔模型（`--model`）

| 模型名 | 实现 | 特征重要度 | 适用 |
|--------|------|-----------|------|
| `lgbm`（默认） | LightGBM 小容量梯度提升树 | gain | 非线性关系，需 libomp |
| `ridge` | RidgeClassifier + 标准化 | \|coef\| | 线性基线，轻量稳健 |
| `logistic` | LogisticRegression + 标准化 | \|coef\| | 线性基线，输出真概率 |

线性模型（ridge/logistic）由 scikit-learn 提供，内置 StandardScaler 流水线；
ridge 的 `decision_function` 经 sigmoid 映射为概率。macOS 上 LightGBM 不可用
（缺 libomp）时，可直接改用 `--model ridge/logistic`。

### 5. 信号生成（中性带阈值 / 置信度仓位）

模型输出上涨概率 `proba_up`，默认离散信号：

- `proba_up > 0.5 + threshold` → 做多（1）
- `proba_up < 0.5 - threshold` 且 `--allow-short` → 做空（-1），否则空仓（0）
- 落在中性带内 → 空仓（0），弱信号不入场以抑制噪声

加 `--prob-sizing` 后改为**置信度连续仓位**：从起步线 `0.5 + threshold` 开始，
概率越高仓位线性放大，至 1.0 满仓（做空方向对称）。置信度低时轻仓试错、
置信度高时重仓，比一刀切的 0/1 信号更平滑。

信号交由回测引擎 `shift(1)` 次日生效，进一步规避前视。

## 防过拟合与回测铁律

- **走步 OOS 是默认且唯一的计价口径**：报告里的净值/夏普等均为样本外结果。
- **线性基线对照**：`--model lgbm` 时默认加跑一次 Ridge 基线（`--no-baseline` 跳过）；
  若 LightGBM 的 OOS 夏普未跑赢线性基线，CLI 会打印**过拟合警告**——复杂模型没有带来
  真实增量，应回退线性模型或重做特征。
- 模型**刻意小容量**（`num_leaves=15, max_depth=4, min_child_samples=30, reg_lambda=1`），降低过拟合。
- **警惕高夏普**：样本外夏普 > 3 时 CLI 会打印怀疑提示——优先排查数据泄露、过拟合或样本偏差，而非当作策略有效。
- 新闻/参数越复杂越容易"回测美如画、实盘现原形"，务必以 OOS 结论为准。

## CLI 用法

> **macOS 前置**：LightGBM 依赖 OpenMP 运行库。若报错 `Library not loaded: @rpath/libomp.dylib`，
> 请执行 `brew install libomp` 后重试（Linux 通常自带，无需额外安装）。

```bash
cd scripts   # 首次先 uv sync

# 基础：走步训练 + 样本外回测 + 出图
uv run python run_ml.py --symbol 600000.SH --count 800 --plot

# 更长历史、允许做空
uv run python run_ml.py --symbol AAPL.US --count 1000 --horizon 5 --allow-short

# 调训练窗与中性带阈值
uv run python run_ml.py --symbol 600519.SH --count 800 --train-window 300 --threshold 0.08

# 线性模型（无 libomp 环境可用）+ 置信度连续仓位
uv run python run_ml.py --symbol 600000.SH --model ridge --prob-sizing

# LightGBM 与 Ridge 基线对照（默认行为；--no-baseline 跳过基线）
uv run python run_ml.py --symbol 600000.SH --model lgbm

# 三重障碍标签：止盈/止损/最长持有期先触发者定标签
uv run python run_ml.py --symbol 600000.SH --label triple --pt-mult 2 --sl-mult 1

# meta-labeling：二级模型过滤一级策略（ma_cross）的假信号，对比过滤前后 OOS 绩效
uv run python run_ml.py --symbol 600000.SH --meta ma_cross --count 800
```

### 参数说明

| 参数 | 默认 | 说明 |
|------|------|------|
| `--model` | lgbm | 预测模型：`lgbm` / `ridge` / `logistic` |
| `--label` | fixed | 标签模式：`fixed`（固定持有期方向）/ `triple`（三重障碍） |
| `--pt-mult` / `--sl-mult` | 2.0 / 1.0 | triple 止盈/止损障碍宽度（×滚动波动率） |
| `--meta` | 无 | meta-labeling 模式：指定一级策略名（如 ma_cross），二级模型过滤其假信号 |
| `--prob-sizing` | 关 | 按预测置信度线性映射为连续仓位 |
| `--no-baseline` | 关 | 跳过 Ridge 线性基线对照（仅 lgbm 时默认加跑） |
| `--horizon` | 5 | 预测的未来收益周期数（标签前瞻步长） |
| `--train-window` | 250 | 走步滚动训练样本数 |
| `--test-window` | 20 | 每次走步向前预测的周期数 |
| `--threshold` | 0.05 | 中性带宽度，proba 偏离 0.5 超过才入场 |
| `--allow-short` | 关 | 预测下跌时输出 -1 |
| `--count` | 800 | K 线数量，越多样本外越充分 |

> 历史不足会报错：至少需要 `warmup(60) + train_window + horizon + test_window` 根 K 线。
> 免费服务的历史日 K 足以运行本模块，无需 API Key。

## meta-labeling（`--meta <策略>`）

二级模型**不预测方向**，只学习「一级策略已给出的信号按三重障碍执行是否赚钱」：

1. 计算一级策略（如 ma_cross）全历史信号；
2. 在信号非零的 bar 上用三重障碍规则生成 meta 标签（1=该信号赚钱）；
3. 走步训练二级分类器（特征 = 技术指标 + 信号方向）；
4. 样本外仅当二级置信度 > 0.5 + threshold 才放行一级信号，其余置 0；
5. 同时回测原始与过滤后两套信号（均仅计价 OOS 段），对比过滤是否真有增益。

> 过滤只会把信号置 0，不会无中生有；meta-labeling 只在一级策略本身有正期望时
> 才可能增益，未提升时应优先换一级策略而非调二级模型。

## 编程方式调用

```python
from datafeed import fetch_ohlcv
from ml import run_ml_strategy
from backtest import format_report

df = fetch_ohlcv("600000.SH", period="1d", count=800)
result = run_ml_strategy(
    df, symbol="600000.SH", horizon=5, allow_short=False,
    model="ridge", prob_sizing=True,   # 可插拔模型 + 置信度仓位
)
print(format_report(result.backtest.metrics))          # 样本外绩效
print(result.feature_importance.head(10))              # 特征重要度
print("OOS 起点:", result.oos_start_label, "模型:", result.model_name)
```

## 输出

- 文本报告：样本外绩效（累计/年化收益、夏普、回撤等）、基准对比、特征重要度 Top10。
- 图表（`--plot`）：净值 vs 基准（标注 OOS 起点）、特征重要度柱状图，输出到项目根目录 `outputs/ml_<标的>.png`（与 `scripts/` 平级）。
