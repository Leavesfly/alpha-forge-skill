"""领域异常层次与 run_cli 退出码映射测试。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from errors import (
    AlphaForgeError,
    DataFetchError,
    InsufficientDataError,
    ValidationError,
)

SCRIPTS_DIR = Path(__file__).resolve().parent.parent


# ─── 异常层次结构 ─────────────────────────────────────────────────────────────


def test_hierarchy():
    """所有领域异常继承 AlphaForgeError，AlphaForgeError 继承 Exception。"""
    assert issubclass(ValidationError, AlphaForgeError)
    assert issubclass(DataFetchError, AlphaForgeError)
    assert issubclass(InsufficientDataError, AlphaForgeError)
    assert issubclass(AlphaForgeError, Exception)


def test_catch_base_catches_all():
    """捕获 AlphaForgeError 可兜底所有领域异常。"""
    for exc_cls in (ValidationError, DataFetchError, InsufficientDataError):
        with pytest.raises(AlphaForgeError):
            raise exc_cls("test")


# ─── run_cli 退出码映射 ───────────────────────────────────────────────────────


def _run_snippet(code: str) -> subprocess.CompletedProcess:
    """执行一段使用 run_cli 的 Python 代码，返回进程结果。"""
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=SCRIPTS_DIR,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_validation_error_exits_2():
    """ValidationError 映射为 exit 2。"""
    result = _run_snippet(
        "from cli_common import run_cli; "
        "from errors import ValidationError; "
        "run_cli(lambda: (_ for _ in ()).throw(ValidationError('参数不合法')))"
    )
    assert result.returncode == 2
    assert "[error] 参数不合法" in result.stderr


def test_data_fetch_error_exits_1():
    """DataFetchError 映射为 exit 1。"""
    result = _run_snippet(
        "from cli_common import run_cli; "
        "from errors import DataFetchError; "
        "run_cli(lambda: (_ for _ in ()).throw(DataFetchError('数据源不可用')))"
    )
    assert result.returncode == 1
    assert "[error] 数据源不可用" in result.stderr


def test_insufficient_data_error_exits_1():
    """InsufficientDataError 映射为 exit 1。"""
    result = _run_snippet(
        "from cli_common import run_cli; "
        "from errors import InsufficientDataError; "
        "run_cli(lambda: (_ for _ in ()).throw(InsufficientDataError('K线不足')))"
    )
    assert result.returncode == 1
    assert "[error] K线不足" in result.stderr


def test_value_error_still_exits_2():
    """向后兼容：原生 ValueError 仍映射为 exit 2。"""
    result = _run_snippet(
        "from cli_common import run_cli; "
        "run_cli(lambda: (_ for _ in ()).throw(ValueError('旧式参数错误')))"
    )
    assert result.returncode == 2
    assert "[error] 旧式参数错误" in result.stderr


def test_runtime_error_still_exits_1():
    """向后兼容：原生 RuntimeError 仍映射为 exit 1。"""
    result = _run_snippet(
        "from cli_common import run_cli; "
        "run_cli(lambda: (_ for _ in ()).throw(RuntimeError('旧式运行错误')))"
    )
    assert result.returncode == 1
    assert "[error] 旧式运行错误" in result.stderr
