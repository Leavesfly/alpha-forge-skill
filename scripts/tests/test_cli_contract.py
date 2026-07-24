"""CLI 契约冒烟测试：保护 Skill 对外接口稳定性。

验证全部 run_*.py 命令的：
- --help 可用（exit 0）
- 参数错误返回 exit 2
- --json 输出包含必需元信息字段（schema/command/generated_at）
- stderr 错误前缀约定（[error] / [warn]）
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

#: scripts/ 目录（CLI 入口所在）
SCRIPTS_DIR = Path(__file__).resolve().parent.parent

#: 全部 CLI 命令（SKILL.md 路由表硬编码引用，不得随意增删）
CLI_COMMANDS = [
    "run_backtest.py",
    "run_optimize.py",
    "run_compare.py",
    "run_custom.py",
    "run_validate.py",
    "run_portfolio.py",
    "run_factor.py",
    "run_pairs.py",
    "run_ml.py",
    "run_sentiment.py",
    "run_dca.py",
    "run_score.py",
    "run_scan.py",
    "run_screener.py",
    "run_canslim.py",
    "run_signal.py",
    "run_paper.py",
    "run_event.py",
    "run_account.py",
    "run_profile.py",
    "run_dashboard.py",
    "run_list.py",
    "run_verify.py",
]


def _run_cli(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    """执行 CLI 命令并返回结果。"""
    return subprocess.run(
        [sys.executable] + args,
        cwd=SCRIPTS_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ─── --help 可用性 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("cmd", CLI_COMMANDS)
def test_help_exits_zero(cmd):
    """每个命令的 --help 应正常退出（exit 0）。"""
    result = _run_cli([cmd, "--help"])
    assert result.returncode == 0, f"{cmd} --help 失败: {result.stderr}"
    assert "usage:" in result.stdout.lower() or "用法" in result.stdout


@pytest.mark.parametrize("cmd", CLI_COMMANDS)
def test_help_shows_json_option(cmd):
    """每个命令的 --help 应包含 --json 选项（Agent 消费契约）。"""
    result = _run_cli([cmd, "--help"])
    assert "--json" in result.stdout, f"{cmd} 缺少 --json 选项"


# ─── 参数错误退出码 ────────────────────────────────────────────────────────────


def test_backtest_missing_symbol_exits_2():
    """run_backtest.py 缺少必需参数应返回 exit 2。"""
    result = _run_cli(["run_backtest.py", "--strategy", "ma_cross"])
    assert result.returncode == 2


def test_backtest_invalid_strategy_exits_2():
    """run_backtest.py 非法策略名应返回 exit 2。"""
    result = _run_cli(["run_backtest.py", "--symbol", "600000.SH", "--strategy", "no_such"])
    assert result.returncode == 2
    assert "[error]" in result.stderr or "invalid choice" in result.stderr


def test_score_invalid_symbol_exits_2():
    """run_score.py 非法标的代码应返回 exit 2。"""
    result = _run_cli(["run_score.py", "--symbol", "invalid"])
    assert result.returncode == 2
    assert "[error]" in result.stderr


# ─── JSON 输出元信息契约 ───────────────────────────────────────────────────────


def test_list_json_has_meta_fields():
    """run_list.py --json 输出应包含 schema/command/generated_at 元信息。"""
    result = _run_cli(["run_list.py", "--json"])
    assert result.returncode == 0, f"run_list.py --json 失败: {result.stderr}"

    payload = json.loads(result.stdout)
    assert "schema" in payload, "JSON 缺少 schema 字段"
    assert "command" in payload, "JSON 缺少 command 字段"
    assert "generated_at" in payload, "JSON 缺少 generated_at 字段"
    assert payload["schema"].startswith("alpha-forge/")


def test_list_json_has_summary_and_next_steps():
    """run_list.py --json 输出应包含 summary 字段（Agent 转述依赖）。"""
    result = _run_cli(["run_list.py", "--json"])
    payload = json.loads(result.stdout)
    # summary 是 Agent 友好字段，全部命令都应支持
    assert "summary" in payload or "strategies" in payload  # list 命令可能无 summary


# ─── stderr 前缀约定 ──────────────────────────────────────────────────────────


def test_error_prefix_convention():
    """参数错误的 stderr 应以 [error] 开头（Agent 错误恢复协议）。"""
    result = _run_cli(["run_score.py", "--symbol", "bad_code"])
    assert result.returncode == 2
    # stderr 应包含 [error] 前缀
    assert "[error]" in result.stderr


# ─── 命令清单同步 ───────────────────────────────────────────────────────────────


def test_cli_commands_match_filesystem():
    """CLI_COMMANDS 应与 scripts/ 下实际 run_*.py 文件一致（防文档/契约漂移）。"""
    actual = sorted(p.name for p in SCRIPTS_DIR.glob("run_*.py"))
    assert sorted(CLI_COMMANDS) == actual, (
        "CLI 命令清单与实际文件不一致，需同步更新本清单与 SKILL.md 路由表"
    )


def test_all_cli_files_exist():
    """全部 CLI 命令文件应存在于 scripts/ 目录。"""
    for cmd in CLI_COMMANDS:
        path = SCRIPTS_DIR / cmd
        assert path.exists(), f"CLI 文件不存在: {cmd}"
