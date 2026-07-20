"""所有 CLI 入口的 --help 完整性检查。

约定：每个 run_*.py 的 --help 必须包含「示例」段（epilog 来自模块 docstring），
参数说明与 build_parser 中的 help 保持同步；本用例防止新增 CLI 时遗漏。
"""

from __future__ import annotations

import importlib

import pytest

# 全部 13 个 CLI 入口模块
CLI_MODULES = [
    "run_backtest",
    "run_optimize",
    "run_compare",
    "run_validate",
    "run_portfolio",
    "run_factor",
    "run_pairs",
    "run_ml",
    "run_sentiment",
    "run_dca",
    "run_signal",
    "run_paper",
    "run_event",
]


@pytest.mark.parametrize("name", CLI_MODULES)
def test_help_contains_examples(name):
    mod = importlib.import_module(name)
    parser = mod.build_parser()
    text = parser.format_help()
    assert "示例" in text, f"{name} 的 --help 缺少示例段（epilog）"
    assert "usage" in text.lower()


@pytest.mark.parametrize("name", CLI_MODULES)
def test_all_args_have_help(name):
    """每个参数都必须写 help 说明。"""
    mod = importlib.import_module(name)
    parser = mod.build_parser()
    missing = [
        a.dest
        for a in parser._actions
        if a.dest != "help" and not (a.help or "").strip()
    ]
    assert not missing, f"{name} 以下参数缺少 help 说明：{missing}"


def test_datafeed_rejects_bad_symbol():
    """数据层在网络请求前就应拦截非法标的代码。"""
    from datafeed import fetch_ohlcv

    with pytest.raises(RuntimeError, match="标的代码不合法"):
        fetch_ohlcv("600000", period="1d", count=10)
