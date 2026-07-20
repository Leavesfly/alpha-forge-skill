"""CLI 配置文件支持：--config <TOML> 注入参数默认值。

使用方式（对所有接入的 run_*.py 生效）：

    uv run python run_backtest.py --config my.toml --symbol 600000.SH

规则：
- TOML 顶层键即参数名（``exec-price`` / ``exec_price`` 均可）；
- 配置文件的值只作为「默认值」注入，显式命令行参数永远优先；
- 未知键直接报错，防止拼写错误静默失效。

示例 my.toml：

    strategy = "ma_cross"
    count = 800
    market = "astock"
    exec-price = "open"
    stop-loss = 0.05
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - 3.10 回退
    import tomli as tomllib


def _load_toml(path: str) -> dict:
    p = Path(path).expanduser()
    if not p.exists():
        raise SystemExit(f"配置文件不存在：{p}")
    with p.open("rb") as f:
        return tomllib.load(f)


def parse_args_with_config(
    parser: argparse.ArgumentParser, argv: list[str] | None = None
) -> argparse.Namespace:
    """解析命令行参数，支持 --config TOML 注入默认值。

    Args:
        parser: 已构建好的参数解析器（无需预先添加 --config）。
        argv: 参数列表；None 时取 sys.argv[1:]。

    Returns:
        与 ``parser.parse_args()`` 相同的 Namespace；
        优先级：显式命令行 > 配置文件 > 解析器默认值。
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    parser.add_argument(
        "--config", default=None, metavar="TOML",
        help="从 TOML 配置文件读取参数默认值（显式命令行参数优先）",
    )

    # 预扫描 --config，先注入默认值再正式解析
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", default=None)
    pre_args, _ = pre.parse_known_args(argv)

    if pre_args.config:
        raw = _load_toml(pre_args.config)
        known = {a.dest for a in parser._actions}
        overrides: dict = {}
        for key, value in raw.items():
            dest = key.replace("-", "_")
            if dest not in known:
                close = difflib.get_close_matches(dest, known, n=1)
                hint = f"，是否想写 '{close[0].replace('_', '-')}'？" if close else ""
                valid = ", ".join(
                    sorted(d.replace("_", "-") for d in known if d not in ("help",))
                )
                raise SystemExit(
                    f"[error] 配置文件包含未知参数 '{key}'{hint}\n"
                    f"本命令可用键：{valid}"
                )
            overrides[dest] = value
        parser.set_defaults(**overrides)
        # 配置文件提供了 required 参数时，解除 required 约束
        for action in parser._actions:
            if action.required and action.dest in overrides:
                action.required = False

    return parser.parse_args(argv)
