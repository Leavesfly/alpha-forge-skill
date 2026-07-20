"""统一持仓账户（account.py / run_account.py）回归测试。

账户文件通过环境变量 ALPHA_FORGE_ACCOUNT_FILE 隔离到临时目录，
不触碰真实 outputs/account.json。
"""

from __future__ import annotations

import json

import pytest

import account as account_mod
from account import (
    get_position,
    held_symbols,
    load_account,
    remove_position,
    set_position,
)


@pytest.fixture(autouse=True)
def _isolated_account(tmp_path, monkeypatch):
    """每个用例使用独立的临时账户文件。"""
    monkeypatch.setenv("ALPHA_FORGE_ACCOUNT_FILE", str(tmp_path / "account.json"))


def test_empty_account_defaults():
    acct = load_account()
    assert acct["positions"] == {}
    assert get_position("600000.SH") is None
    assert held_symbols() == []


def test_set_get_remove_roundtrip():
    set_position("600000.SH", 1000, 8.5, note="测试")
    pos = get_position("600000.SH")
    assert pos["shares"] == 1000
    assert pos["cost"] == 8.5
    assert pos["note"] == "测试"
    assert pos["source"] == "account"
    assert held_symbols() == ["600000.SH"]

    remove_position("600000.SH")
    assert get_position("600000.SH") is None


def test_set_updates_existing_and_keeps_added_at():
    set_position("600519.SH", 100, 1500.0)
    first = load_account()["positions"]["600519.SH"]
    set_position("600519.SH", 200, 1450.0)
    updated = load_account()["positions"]["600519.SH"]
    assert updated["shares"] == 200
    assert updated["cost"] == 1450.0
    assert updated["added_at"] == first["added_at"]  # 更新不重置登记时间


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        set_position("600000.SH", 0, 8.5)
    with pytest.raises(ValueError):
        set_position("600000.SH", 100, -1)
    with pytest.raises(ValueError):
        remove_position("000001.SZ")  # 不存在的持仓


def test_corrupted_file_raises_actionable_error():
    path = account_mod.account_path()
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="run_account.py"):
        load_account()


def test_persisted_json_structure():
    set_position("AAPL.US", 10, 180.0)
    raw = json.loads(account_mod.account_path().read_text(encoding="utf-8"))
    assert raw["version"] == account_mod.ACCOUNT_VERSION
    assert raw["updated_at"]
    assert raw["positions"]["AAPL.US"]["shares"] == 10


def test_score_detect_account_position(monkeypatch):
    """run_score 的账户探测应优先返回登记持仓。"""
    from run_score import detect_account_position

    set_position("600000.SH", 500, 9.9)
    logs: list[str] = []
    pos = detect_account_position("600000.SH", logs.append)
    assert pos["cost"] == 9.9
    assert pos["source"] == "account"
    assert any("账户持仓" in line for line in logs)
    assert detect_account_position("999999.SH", logs.append) is None
