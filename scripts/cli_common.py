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

# 标的代码统一格式：代码.市场后缀（如 600000.SH / AAPL.US / 00700.HK / cu2501.SHF）
_SYMBOL_RE = re.compile(r"^[0-9A-Za-z]+\.[A-Za-z]{2,4}$")

SYMBOL_FORMAT_HINT = (
    "标的代码格式应为「代码.市场后缀」，如 600000.SH（A股）/ AAPL.US（美股）/ "
    "00700.HK（港股）/ cu2501.SHF（期货）；完整后缀见 references/data-fetching.md。"
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
    sym = (symbol or "").strip()
    if not _SYMBOL_RE.match(sym):
        raise SystemExit(f"[error] 标的代码不合法：'{symbol}'。{SYMBOL_FORMAT_HINT}")
    return sym


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


def run_cli(main: Callable[[], None]) -> None:
    """统一 CLI 入口：把可预期异常转为友好 stderr 信息与规范退出码。

    退出码约定：0=成功，1=运行错误（数据/网络/计算），2=参数错误，130=用户中断。
    """
    try:
        main()
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        sys.exit(130)
    except SystemExit:
        raise
    except (RuntimeError, ValueError, OSError) as exc:
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
