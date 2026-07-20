"""run_dashboard.py 回归测试：HTML 生成与 JSON 输出。"""

from __future__ import annotations

import json
import sys

import pytest

import run_dashboard


def _setup(tmp_path, monkeypatch, account_positions=None, papers=None):
    """打桩：账户/模拟盘/输出目录指向 tmp_path。"""
    acct = {"version": 1, "updated_at": None, "positions": account_positions or {}}
    monkeypatch.setattr(run_dashboard, "load_account", lambda: acct)
    monkeypatch.setattr(run_dashboard, "_outputs_dir", lambda: tmp_path)
    if papers is not None:
        monkeypatch.setattr(run_dashboard, "_load_papers", lambda: papers)


def test_dashboard_generates_html(tmp_path, monkeypatch, capsys):
    """无持仓无模拟盘时也能生成合法 HTML。"""
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_dashboard.py", "--output", str(tmp_path / "d.html")])
    run_dashboard.main()
    html = (tmp_path / "d.html").read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    assert "Alpha Forge Dashboard" in html
    assert "真实持仓账户" in html
    assert "模拟盘组合" in html


def test_dashboard_with_positions_and_papers(tmp_path, monkeypatch, capsys):
    """有持仓 + 模拟盘时 HTML 包含对应内容。"""
    positions = {"600000.SH": {"shares": 1000, "cost": 8.5, "note": "test", "added_at": ""}}
    papers = [{
        "symbol": "600519.SH", "strategy": "score", "cash": 50000.0,
        "shares": 100, "initial_capital": 100000.0,
        "trades": [{"date": "2025-01-01", "action": "买入", "shares": 100, "price": 1800.0, "cost": 5.0}],
        "last_date": "2025-06-01",
    }]
    _setup(tmp_path, monkeypatch, account_positions=positions, papers=papers)
    monkeypatch.setattr(sys, "argv", ["run_dashboard.py", "--output", str(tmp_path / "d.html")])
    run_dashboard.main()
    html = (tmp_path / "d.html").read_text(encoding="utf-8")
    assert "600000.SH" in html
    assert "600519.SH" in html
    assert "score" in html


def test_dashboard_json_output(tmp_path, monkeypatch, capsys):
    """--json 输出含 output_file 与 summary。"""
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", [
        "run_dashboard.py", "--output", str(tmp_path / "d.html"), "--json",
    ])
    run_dashboard.main()
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["command"] == "dashboard"
    assert "output_file" in payload
    assert "summary" in payload


def test_dashboard_concentration_warning(tmp_path, monkeypatch, capsys):
    """单标的占比 >40% 时 HTML 含风控提示。"""
    papers = [{
        "symbol": "AAA.SH", "strategy": "ma_cross", "cash": 0.0,
        "shares": 10000, "initial_capital": 100000.0,
        "trades": [{"date": "2025-01-01", "action": "买入", "shares": 10000, "price": 10.0, "cost": 5.0}],
        "last_date": "2025-06-01",
    }]
    _setup(tmp_path, monkeypatch, papers=papers)
    monkeypatch.setattr(sys, "argv", ["run_dashboard.py", "--output", str(tmp_path / "d.html")])
    run_dashboard.main()
    html = (tmp_path / "d.html").read_text(encoding="utf-8")
    assert "集中度" in html
