"""回测图表默认文件名生成。

按关键参数（标的/策略/股票池等）生成语义化文件名，避免同一 CLI 多次运行时
图表互相覆盖；相同配置重跑才会覆盖（结果本就相同，符合直觉）。
用户显式传入 ``--output`` 时优先使用用户指定路径。
"""

from __future__ import annotations

import re


def sanitize(text: object) -> str:
    """保留字母/数字/下划线/连字符，去除其余字符。

    例：``600000.SH`` -> ``600000SH``；``ma_cross`` 保持不变。
    """
    return re.sub(r"[^0-9A-Za-z_-]+", "", str(text))


def default_output(prefix: str, *parts: object) -> str:
    """生成 ``../outputs/<prefix>_<part1>_<part2>.png`` 形式的默认图表路径。

    空的部分会被跳过；全部为空时退化为 ``../outputs/<prefix>.png``。
    """
    tag = "_".join(
        s for s in (sanitize(p) for p in parts if p not in (None, "")) if s
    )
    name = f"{prefix}_{tag}" if tag else prefix
    return f"../outputs/{name}.png"
