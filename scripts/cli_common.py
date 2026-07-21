"""CLI 公共工具：参数解析、输入校验、错误处理与 JSON 输出的统一封装。

所有 run_*.py 入口共享本模块，保证：
- ``--help`` 末尾附带可直接复制的运行示例（epilog）；
- 标的代码/参数格式错误给出「怎么改」的友好提示，而非 Python 堆栈；
- 可预期异常统一转为 ``[error] ...`` stderr 信息 + 非零退出码
  （exit 1=运行错误，2=参数错误，130=用户中断），便于脚本/agent 判断；
- ``--json`` 输出走统一的 stdout 纯净约定（进度转 stderr）。

设置环境变量 ``ALPHA_FORGE_DEBUG=1`` 可在出错时查看完整堆栈。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# 领域常量与标的工具：从 market.py re-export（保持向后兼容）
# ---------------------------------------------------------------------------
from market import (  # noqa: F401 - re-export 保持兼容
    ASTOCK_LOT_SIZE,
    ASTOCK_SUFFIXES,
    DEFAULT_LOT_SIZE,
    SYMBOL_FORMAT_HINT,
    default_lot_size,
    is_astock,
    validate_symbol,
)


def make_parser(description: str, doc: str | None = None) -> argparse.ArgumentParser:
    """创建统一风格的参数解析器：--help 末尾附模块 docstring 中的示例段；
    枚举参数拼错时附近似建议（如 --strategy macdd -> 「是否想写 macd？」）。"""
    return _FriendlyParser(
        description=description,
        epilog=examples_from_doc(doc),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


class _FriendlyParser(argparse.ArgumentParser):
    """在 argparse 的 invalid choice 报错后追加近似候选与查询指引。"""

    def error(self, message: str) -> None:  # noqa: A003 - argparse 约定接口
        super().error(_suggest_for_choice_error(message))


# argparse 报错格式：argument --strategy: invalid choice: 'macdd' (choose from 'ma_cross', ...)
_CHOICE_ERR_RE = re.compile(r"invalid choice: '(?P<value>[^']*)' \(choose from (?P<choices>.+)\)")


def _suggest_for_choice_error(message: str) -> str:
    """从 invalid choice 报错中提取候选集，用 difflib 给出「是否想写 X？」建议。"""
    m = _CHOICE_ERR_RE.search(message)
    if not m:
        return message
    import difflib

    choices = [c.strip().strip("'\"") for c in m.group("choices").split(",")]
    close = difflib.get_close_matches(m.group("value"), choices, n=3, cutoff=0.5)
    hints = []
    if close:
        hints.append("是否想写：" + " / ".join(close) + "？")
    hints.append("全部可选值可用 run_list.py 查看。")
    return message + "\n" + " ".join(hints)


def examples_from_doc(doc: str | None) -> str | None:
    """从模块 docstring 提取「示例：」段落，作为 --help 的 epilog。"""
    if not doc or "示例" not in doc:
        return None
    return doc[doc.index("示例"):].rstrip()


def check_symbol(symbol: str) -> str:
    """校验单个标的代码格式，非法时给出可操作的错误提示。"""
    try:
        return validate_symbol(symbol)
    except ValueError as exc:
        raise SystemExit(f"[error] {exc}") from exc


def split_symbols(text: str, min_count: int = 1, what: str = "本命令") -> list[str]:
    """解析逗号分隔的标的列表并逐个校验格式。

    Args:
        text: ``--symbols`` 原始值，如 ``600000.SH,000001.SZ``。
        min_count: 最少标的数，不足时报错并说明要求。
        what: 报错时的场景描述（如「组合回测」）。
    """
    symbols = [s.strip() for s in (text or "").split(",") if s.strip()]
    if len(symbols) < min_count:
        raise SystemExit(
            f"[error] {what}至少需要 {min_count} 个标的（--symbols 逗号分隔），"
            f"当前收到 {len(symbols)} 个。{SYMBOL_FORMAT_HINT}"
        )
    return [check_symbol(s) for s in symbols]


def parse_params(pairs: list[str] | None) -> dict:
    """将 ["fast=10", "slow=30"] 解析为 {"fast": 10, "slow": 30}。

    同时兼容空格分隔（``--params fast=10 slow=30``）与
    逗号分隔（``--params fast=10,slow=30``）两种写法。
    """
    result: dict = {}
    for token in pairs or []:
        for item in token.split(","):
            item = item.strip()
            if not item:
                continue
            if "=" not in item:
                raise SystemExit(
                    f"[error] --params 格式应为 key=value（如 fast=10 slow=30），收到：'{item}'"
                )
            key, value = item.split("=", 1)
            result[key.strip()] = _cast(value.strip())
    return result


def _cast(value: str):
    """尝试转为 int/float，失败则保留字符串。"""
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            continue
    return value


def make_logger(json_stdout: bool) -> Callable[..., None]:
    """返回 log 函数：--json 输出到 stdout 时，进度/报告一律转 stderr。"""

    def log(*a) -> None:
        print(*a, file=sys.stderr if json_stdout else sys.stdout)

    return log


def log_next_steps(log: Callable[..., None], *steps: str) -> None:
    """统一格式的「下一步」指引：把学习路径内嵌在使用路径里。

    各 CLI 在结果输出末尾调用，提示用户典型的后续动作（寻优/验证/模拟盘等）。"""
    if steps:
        log("\n下一步：" + "；".join(steps))


def build_next_steps(*steps: dict) -> list[dict]:
    """构建结构化下一步动作列表，嵌入 --json 输出供 Agent 程序化链式引导。

    每个 step 为 dict，含 action（动作标识）、reason（为何建议）、command（可执行命令）。
    Agent 可据此在转述结尾主动提议后续操作，而非依赖解析 stderr 文本。

    用法::

        next_steps = build_next_steps(
            {"action": "optimize", "reason": "寻找最优参数", "command": "run_optimize.py --symbol 600000.SH --strategy ma_cross"},
            {"action": "validate", "reason": "样本外验证", "command": "run_validate.py --symbol 600000.SH --strategy ma_cross"},
        )
    """
    return list(steps)


def emit_json(dest: str, payload: dict, log: Callable[..., None]) -> None:
    """按统一约定输出 JSON：dest 为 '-' 时打印到 stdout，否则写入文件。"""
    from report import to_json

    text = to_json(payload)
    if dest == "-":
        print(text)  # stdout 仅留给 JSON
    else:
        path = Path(dest).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        log(f"JSON 已保存：{path}")


def add_json_arg(parser: argparse.ArgumentParser) -> None:
    """添加统一的 --json 参数（不带值打印 stdout，带路径写文件）。"""
    parser.add_argument(
        "--json",
        nargs="?",
        const="-",
        default=None,
        metavar="PATH",
        help="输出结构化 JSON：不带值打印到 stdout（进度信息转 stderr），带路径则写入文件",
    )


# ---------------------------------------------------------------------------
# 参数组注册：消除 run_*.py 中大量重复的 add_argument 定义
# ---------------------------------------------------------------------------


def add_cost_args(parser: argparse.ArgumentParser) -> None:
    """添加交易成本参数：--commission / --slippage。"""
    parser.add_argument("--commission", type=float, default=0.0005, help="单边手续费率")
    parser.add_argument("--slippage", type=float, default=0.0005, help="单边滑点率")


def add_market_args(parser: argparse.ArgumentParser) -> None:
    """添加市场/成交规则参数：--market / --exec-price / --limit-board。"""
    parser.add_argument(
        "--market",
        choices=["generic", "astock"],
        default="generic",
        help="成本预设：generic(默认) / astock(A股卖出印花税 + 双边过户费)",
    )
    parser.add_argument(
        "--exec-price",
        choices=["close", "open"],
        default="close",
        help="成交价约定：close(收盘成交,默认) / open(次日开盘成交,更贴近现实)",
    )
    parser.add_argument(
        "--limit-board",
        choices=["main", "star", "chinext", "st"],
        default=None,
        help="启用 A 股涨跌停/停牌规则并指定板块(main 10%%/star,chinext 20%%/st 5%%)",
    )


def add_risk_args(parser: argparse.ArgumentParser) -> None:
    """添加风控/仓位管理参数：--allow-short / --stop-loss / --take-profit /
    --vol-target / --vol-window / --max-leverage。"""
    parser.add_argument("--allow-short", action="store_true", help="开启做空（策略输出 -1）")
    parser.add_argument("--stop-loss", type=float, default=None, help="止损比例，如 0.05 表示浮亏 5%%")
    parser.add_argument("--take-profit", type=float, default=None, help="止盈比例，如 0.10 表示浮盈 10%%")
    parser.add_argument("--vol-target", type=float, default=None, help="年化目标波动率，如 0.15（开启连续仓位）")
    parser.add_argument("--vol-window", type=int, default=20, help="波动率滚动窗口，默认 20")
    parser.add_argument("--max-leverage", type=float, default=1.0, help="仓位上限，默认 1.0")


def init_log(args: argparse.Namespace) -> tuple[bool, Callable[..., None]]:
    """从已解析参数初始化日志：返回 (json_stdout, log) 二元组。

    消除各 run_*.py 中重复的 ``json_stdout = args.json == "-"`` 样板。
    """
    json_stdout = getattr(args, "json", None) == "-"
    return json_stdout, make_logger(json_stdout)


def build_cost_and_rules(args: argparse.Namespace):
    """从 CLI 参数构造交易成本模型与 A 股交易规则。

    消除 run_backtest/run_optimize/run_compare/run_validate/run_paper 中
    重复的 CostModel.preset + TradingRules.astock 构造逻辑。

    Returns:
        (cost_model, trading_rules) 二元组；trading_rules 为 None 表示不施加成交约束。
    """
    from backtest.costs import CostModel
    from backtest.rules import TradingRules

    cost_model = CostModel.preset(
        getattr(args, "market", "generic"),
        commission=getattr(args, "commission", 0.0005),
        slippage=getattr(args, "slippage", 0.0005),
    )
    limit_board = getattr(args, "limit_board", None)
    trading_rules = TradingRules.astock(limit_board) if limit_board else None
    return cost_model, trading_rules


def run_cli(main: Callable[[], None]) -> None:
    """统一 CLI 入口：把可预期异常转为友好 stderr 信息与规范退出码。

    退出码约定：0=成功，1=运行错误（数据/网络/计算），2=参数错误，130=用户中断。
    """
    from errors import AlphaForgeError, DataFetchError, InsufficientDataError, ValidationError

    try:
        main()
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        sys.exit(130)
    except SystemExit as exc:
        # 规范化退出码：字符串消息（参数错误）→ exit 2；数字码保留
        if exc.code is None or isinstance(exc.code, str):
            if exc.code:
                print(exc.code, file=sys.stderr)
            sys.exit(2)
        raise
    except ValidationError as exc:
        # 领域参数/格式错误 → exit 2
        if os.environ.get("ALPHA_FORGE_DEBUG"):
            raise
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(2)
    except (DataFetchError, InsufficientDataError) as exc:
        # 领域数据错误 → exit 1
        if os.environ.get("ALPHA_FORGE_DEBUG"):
            raise
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(1)
    except AlphaForgeError as exc:
        # 其他领域异常（未来扩展）→ exit 1
        if os.environ.get("ALPHA_FORGE_DEBUG"):
            raise
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        # 参数/格式错误 → exit 2（向后兼容旧代码）
        if os.environ.get("ALPHA_FORGE_DEBUG"):
            raise
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(2)
    except (RuntimeError, OSError) as exc:
        # 运行错误（数据/网络/IO）→ exit 1（向后兼容旧代码）
        if os.environ.get("ALPHA_FORGE_DEBUG"):
            raise
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # 未预期异常：保留类型名便于反馈
        if os.environ.get("ALPHA_FORGE_DEBUG"):
            raise
        print(f"[error] 未预期异常 {type(exc).__name__}: {exc}", file=sys.stderr)
        print("（设置环境变量 ALPHA_FORGE_DEBUG=1 重跑可查看完整堆栈）", file=sys.stderr)
        sys.exit(1)
