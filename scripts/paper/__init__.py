"""模拟盘子包：状态管理、组合聚合与评分模式。

从 run_paper.py 拆分而来，保持对外契约不变。
"""

from __future__ import annotations

from .state import load_state, save_state, state_path
from .summary import run_summary

__all__ = [
    "state_path",
    "load_state",
    "save_state",
    "run_summary",
]
