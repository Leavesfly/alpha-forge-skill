"""输出文件默认命名（图表 PNG / 报告 HTML / 数据 JSON）。

命名约定：``../outputs/<前缀>_<关键参数1>_<关键参数2>.<扩展名>``，
前缀即命令名（backtest/optimize/portfolio/compare/report/event 等），
关键参数经 :func:`sanitize` 去除特殊字符（如 ``600000.SH`` -> ``600000SH``）。
同一 CLI 不同配置多次运行不会互相覆盖；相同配置重跑才覆盖
（结果本就相同，符合直觉）。用户显式传入 ``--output`` 时优先使用用户指定路径。
"""

from __future__ import annotations

import re


def sanitize(text: object) -> str:
    """保留字母/数字/下划线/连字符，去除其余字符。

    例：``600000.SH`` -> ``600000SH``；``ma_cross`` 保持不变。
    """
    return re.sub(r"[^0-9A-Za-z_-]+", "", str(text))


def default_output(prefix: str, *parts: object, ext: str = "png") -> str:
    """生成 ``../outputs/<prefix>_<part1>_<part2>.<ext>`` 形式的默认输出路径。

    空的部分会被跳过；全部为空时退化为 ``../outputs/<prefix>.<ext>``。
    ``ext`` 默认 png（图表），HTML 报告传 ``ext="html"``。
    """
    tag = "_".join(
        s for s in (sanitize(p) for p in parts if p not in (None, "")) if s
    )
    name = f"{prefix}_{tag}" if tag else prefix
    return f"../outputs/{name}.{ext}"
