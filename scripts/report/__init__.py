"""报告生成：结构化 JSON、自包含 HTML 研究报告与 rich 终端渲染。"""

from __future__ import annotations

from .console import ProgressBar, frame_table, metrics_table, print_text
from .html import render_backtest_report, render_compare_report
from .serialize import (
    SCHEMA_VERSION,
    attach_meta,
    frame_records,
    result_to_dict,
    to_json,
)

__all__ = [
    "render_backtest_report",
    "render_compare_report",
    "result_to_dict",
    "to_json",
    "attach_meta",
    "frame_records",
    "SCHEMA_VERSION",
    "metrics_table",
    "frame_table",
    "ProgressBar",
    "print_text",
]
