"""输出文件默认命名（图表 PNG / 报告 HTML / 数据 JSON）与输出目录管理。

命名约定：``../outputs/<前缀>_<关键参数1>_<关键参数2>.<扩展名>``，
前缀即命令名（backtest/optimize/portfolio/compare/report/event 等），
关键参数经 :func:`sanitize` 去除特殊字符（如 ``600000.SH`` -> ``600000SH``）。
同一 CLI 不同配置多次运行不会互相覆盖；相同配置重跑才覆盖
（结果本就相同，符合直觉）。用户显式传入 ``--output`` 时优先使用用户指定路径。
"""

from __future__ import annotations

import re
from pathlib import Path

from envconfig import get_env_config


def sanitize(text: object) -> str:
    """保留字母/数字/下划线/连字符，去除其余字符。

    例：``600000.SH`` -> ``600000SH``；``ma_cross`` 保持不变。
    """
    return re.sub(r"[^0-9A-Za-z_-]+", "", str(text))


def outputs_dir() -> Path:
    """返回项目统一输出目录（与 scripts/ 平级的 outputs/）。

    支持环境变量 ``ALPHA_FORGE_OUTPUT_DIR`` 覆盖（测试/多环境隔离）。
    目录不存在时自动创建。
    """
    override = get_env_config().output_dir
    if override:
        path = Path(override).expanduser()
    else:
        path = Path(__file__).resolve().parent.parent / "outputs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_output(prefix: str, *parts: object, ext: str = "png") -> str:
    """生成 ``outputs/<prefix>_<part1>_<part2>.<ext>`` 形式的绝对输出路径。

    空的部分会被跳过；全部为空时退化为 ``outputs/<prefix>.<ext>``。
    ``ext`` 默认 png（图表），HTML 报告传 ``ext="html"``。
    返回绝对路径（基于 outputs_dir()），消除对 CWD 的隐式假设。
    """
    tag = "_".join(
        s for s in (sanitize(p) for p in parts if p not in (None, "")) if s
    )
    name = f"{prefix}_{tag}" if tag else prefix
    return str(outputs_dir() / f"{name}.{ext}")
