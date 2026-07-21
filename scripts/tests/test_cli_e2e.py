"""CLI 端到端测试：验证退出码规范与 JSON schema 契约。

不依赖网络：通过 monkeypatch 替换 datafeed.fetch_ohlcv 为合成数据，
验证 CLI 层面的退出码、JSON 输出格式与 Agent 消费契约。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from tests.helpers import make_ohlcv

SCRIPTS_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- 退出码测试


def test_exit_code_2_on_invalid_symbol():
    """参数错误（非法标的代码）应退出码 2。"""
    result = subprocess.run(
        [sys.executable, "run_backtest.py", "--symbol", "INVALID", "--strategy", "ma_cross"],
        cwd=SCRIPTS_DIR,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "[error]" in result.stderr


def test_exit_code_2_on_invalid_strategy():
    """参数错误（非法策略名）应退出码 2。"""
    result = subprocess.run(
        [sys.executable, "run_backtest.py", "--symbol", "600000.SH", "--strategy", "nonexist"],
        cwd=SCRIPTS_DIR,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


def test_exit_code_2_on_missing_required_arg():
    """缺少必需参数应退出码 2。"""
    result = subprocess.run(
        [sys.executable, "run_backtest.py"],
        cwd=SCRIPTS_DIR,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


# ---------------------------------------------------------------- JSON schema 测试


def _make_mock_df(n=300):
    """构造测试用 OHLCV 数据。"""
    rng = np.random.default_rng(42)
    steps = rng.normal(loc=0.0005, scale=0.02, size=n)
    close = 100.0 * np.exp(np.cumsum(steps))
    return make_ohlcv(close)


def test_json_schema_backtest(capsys, monkeypatch):
    """run_backtest.py --json 输出应包含固定元信息字段。"""
    mock_df = _make_mock_df()
    monkeypatch.setattr("datafeed.fetch_ohlcv", lambda *a, **kw: mock_df)
    monkeypatch.setattr(
        "sys.argv",
        ["run_backtest.py", "--symbol", "600000.SH", "--strategy", "ma_cross", "--json"],
    )

    from run_backtest import main as backtest_main

    # main() 正常执行不抛异常
    backtest_main()

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert "schema" in payload
    assert payload["command"] == "backtest"


def test_json_output_has_required_fields(capsys, monkeypatch):
    """验证 --json 输出包含 schema/command/generated_at/summary/next_steps。"""
    mock_df = _make_mock_df()
    monkeypatch.setattr("datafeed.fetch_ohlcv", lambda *a, **kw: mock_df)
    monkeypatch.setattr(
        "sys.argv",
        ["run_backtest.py", "--symbol", "600000.SH", "--strategy", "ma_cross", "--json"],
    )

    from run_backtest import main as backtest_main

    # main() 正常执行不抛异常
    backtest_main()

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    # 固定元信息字段（Agent 消费契约）
    assert "schema" in payload
    assert "command" in payload
    assert "generated_at" in payload
    assert payload["command"] == "backtest"

    # Agent 友好字段
    assert "summary" in payload
    assert isinstance(payload["summary"], str)
    assert "next_steps" in payload
    assert isinstance(payload["next_steps"], list)
    for step in payload["next_steps"]:
        assert "action" in step
        assert "reason" in step
        assert "command" in step


def test_json_output_score_has_verdict(capsys, monkeypatch):
    """run_score.py --json 输出应包含评分结论字段。"""
    mock_df = _make_mock_df(300)
    monkeypatch.setattr("datafeed.fetch_ohlcv", lambda *a, **kw: mock_df)
    monkeypatch.setattr(
        "sys.argv",
        ["run_score.py", "--symbol", "600000.SH", "--json"],
    )

    from run_score import main as score_main

    score_main()

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["command"] == "score"
    assert "verdict" in payload
    assert payload["verdict"] in ("yes", "watch", "no", "reduce_risk", "unrated")
    assert "verdict_cn" in payload
    assert "summary" in payload


# ---------------------------------------------------------------- run_list 能力发现


def test_run_list_json(capsys, monkeypatch):
    """run_list.py --json 应输出策略清单。"""
    monkeypatch.setattr("sys.argv", ["run_list.py", "--json"])

    from run_list import main as list_main

    list_main()

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert "strategies" in payload
    assert len(payload["strategies"]) >= 14  # 内置 14 个策略
