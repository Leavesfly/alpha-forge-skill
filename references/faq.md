# Troubleshooting / FAQ

常见报错与解决方案对照表。排错建议先跑环境自检 `uv run python run_list.py --doctor`
（逐项检查依赖/Key/缓存/字体/数据拉取并附修复建议）；通用开关：`ALPHA_FORGE_DEBUG=1` 可在出错时打印完整堆栈。

## 快速对照表

| 症状 / 报错关键词 | 原因 | 解决方案 |
|------------------|------|----------|
| `TICKFLOW_API_KEY` 未配置 / 401 | 实时行情、分钟 K、股票池、财务数据需要完整服务 | 前往 [tickflow.org](https://tickflow.org) 申请 Key 并 `export TICKFLOW_API_KEY="..."`；仅做历史日 K 回测可不配置（免费服务） |
| `libomp.dylib` 加载失败（macOS） | LightGBM 依赖 OpenMP 运行库 | `brew install libomp`；或改用 `--model ridge/logistic`（纯 sklearn，无此依赖） |
| 标的代码不识别 / 无数据 | 缺市场后缀或格式错误 | 统一使用 **代码.市场后缀**：`600000.SH`、`000001.SZ`、`AAPL.US`、`00700.HK`；后缀大写 |
| 拉到 0 根 K 线 / `没有数据源支持` | 标的不存在、周期不支持或数据源受限 | 检查代码拼写；分钟 K 需 API Key；akshare 兜底仅支持 A 股日/周/月 K；`ALPHA_FORGE_DATA_SOURCE=tickflow\|akshare` 可强制单源排查 |
| 数据陈旧（缓存命中旧行情） | K 线本地缓存未过期 | 加 `--no-cache` 强制重拉；或 `ALPHA_FORGE_NO_CACHE=1` 全局关闭；TTL 分级：日线默认 1 天、分钟线 30 分钟，`ALPHA_FORGE_CACHE_TTL`（秒）可显式覆盖 |
| 网络抖动偶发失败 | 单次请求超时/连接重置 | 内置自动重试（默认 2 次，退避 1s/2s），`ALPHA_FORGE_RETRIES` 可调（0 关闭）；重试仍失败自动降级下一数据源，且存在过期缓存时回退使用 |
| `ValueError: 双均线参数要求 fast < slow` 等 | 策略参数组合非法 | 按提示修正参数；各策略约束见 [strategies.md](strategies.md)（如 donchian/turtle 要求 exit <= entry、cci 要求 entry < exit） |
| `未知策略 'xxx'` | 策略名拼写错误 | `uv run python run_list.py` 查看全部 14 个策略名与参数 |
| `历史长度不足`（run_ml / run_validate） | K 线根数撑不起训练窗/走步折 | 增大 `--count`（建议 ≥ 800）或减小 `--train-window` / 折数 |
| `command not found: uv` | 未安装 uv 或 PATH 未包含 | 安装：`curl -LsSf https://astral.sh/uv/install.sh \| sh`；或把 `~/.local/bin` 加入 PATH |
| `ModuleNotFoundError`（pandas/tickflow 等） | 依赖未安装 | `cd scripts && uv sync`（跑测试需 `uv sync --group dev`） |
| 未找到打分文件（run_sentiment backtest） | 跳过了 fetch/打分步骤 | 先 `--stage fetch` 生成模板，由 agent 填分后再 `--stage backtest`；无 agent 时加 `--use-lexicon` 词典兜底 |
| 模拟盘结果与预期不符 | 改了 `--params` 但沿用旧状态文件 | `run_paper.py ... --reset` 重置模拟盘状态 |
| `无法评分`（run_score / run_scan） | K 线不足 250 根，无法稳定计算年线/动量 | 增大 `--count`（建议 ≥ 500）；次新股上市未满一年属正常现象，不强行给分 |
| 财务因子被跳过（run_factor） | 无 API Key 或账号无财务数据权限 | 配置 Key 并开通权限；或仅用价格因子 `--factors momentum,low_vol` |
| 图表中文显示为方框 | 系统缺中文字体 | 安装任一中文字体（如 PingFang/微软雅黑/Noto Sans CJK）后重跑 |
| 退出码 2 | 命令行参数错误 | 看 stderr 的 `[error] ` 提示与 `--help` 示例段修正参数 |
| 退出码 1 | 运行期错误（数据/网络等） | 按 stderr 修复建议排查；可设 `ALPHA_FORGE_DEBUG=1` 看堆栈 |

## 高频问题

### Q: 完全离线能用吗？

不能拉新数据，但命中本地缓存的标的可以继续回测（拉取失败会自动回退过期缓存并告警）。建议联网时先跑一遍目标标的把缓存焐热。

### Q: 为什么我的策略夏普 > 3？

优先怀疑过拟合或数据泄露，而不是策略有效。用 `run_validate.py`（走步样本外 + PBO）与 `run_optimize.py` 的 DSR 诊断验证；`run_ml.py` 默认自带线性基线对照。

### Q: JSON 输出里混入了进度文字？

不会。所有命令 `--json` 不带值时 stdout 保证纯 JSON，进度与告警全部走 stderr；管道消费请只读 stdout。

### Q: 如何查询当前版本支持哪些策略/因子/模型？

`uv run python run_list.py --json`，一条命令返回全部策略（含默认参数与参数网格）、轮动策略、因子、ML 模型与定投模式。

### Q: 缓存文件放在哪里？可以删吗？

项目根 `.cache/`（已在 `.gitignore`）。可随时整目录删除，下次运行自动重建；`ALPHA_FORGE_CACHE_DIR` 可改位置。

### Q: 评分结论总是「否」，是不是坏了？

大概率是预期行为。四层否决式评分本就苛刻：只要收盘价在 MA200 之下就直接「否」（逆势不开仓），
熊市/震荡市里大面积否决正是纪律的体现。看输出中的「分层理由」确认是哪一层拦截；
用 `--replay 120` 可回放历史结论分布，验证引擎并非永远否决。阈值为纪律预设而非拟合产物，见 [scoring.md](scoring.md)。

### Q: 评分里的「无法评分」和「持仓需减风险」是什么意思？

「无法评分」= K 线不足 250 根（增大 `--count` 即可）；「持仓需减风险」仅在传入持仓
（`--cost`/`--shares`，或自动探测到模拟盘持仓）且触发否决时出现——空仓视角的「否」对持仓者意味着减风险而非禁止买入。

### Q: 港股/美股评分时基准拉不到会怎样？

自动降级：相对强度分项置 0，权重重分配为动量 0.90 / 趋势效率 0.10，并在输出中标注基准缺失；
结论仍有效，只是少了「跑赢大盘」这一维度。也可用 `--benchmark` 指定替代基准。

### Q: 增量缓存会不会漏数据或用到错价？

增量更新只拉尾部小段，并用 5 根重叠 K 线做复权一致性校验（0.1% 容差）：对不上（如除权除息导致
历史价修订）会自动回退全量重拉；缺口过大也回退全量。不放心可设 `ALPHA_FORGE_INCR_CACHE=0` 关闭，或 `--no-cache` 强制重拉。
