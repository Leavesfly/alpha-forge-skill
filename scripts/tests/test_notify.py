"""webhook 通知模块与 run_signal --notify 集成回归测试。"""

from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
import pytest

import run_signal
from notify import build_payload, send_webhook
from tests.helpers import make_ohlcv


# ------------------------------------------------------------ payload 适配


def test_payload_dingtalk():
    p = build_payload("https://oapi.dingtalk.com/robot/send?access_token=x", "hi", "T")
    assert p["msgtype"] == "text"
    assert "hi" in p["text"]["content"]


def test_payload_wecom():
    p = build_payload("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x", "hi")
    assert p["msgtype"] == "text"


def test_payload_feishu():
    p = build_payload("https://open.feishu.cn/open-apis/bot/v2/hook/x", "hi")
    assert p["msg_type"] == "text"
    assert "hi" in p["content"]["text"]


def test_payload_generic():
    p = build_payload("https://example.com/hook", "hi", "T")
    assert p == {"title": "T", "text": "hi"}


def test_send_webhook_failure_returns_false(capsys):
    """无法连接的地址：返回 False 且只告警不抛异常。"""
    ok = send_webhook("http://127.0.0.1:1/never", "hi", timeout=0.5)
    assert ok is False
    assert "[warn]" in capsys.readouterr().err


# ------------------------------------------------------------ run_signal 集成


def _uptrend_df(n: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    steps = 0.002 + rng.normal(0.0, 0.005, size=n)
    return make_ohlcv(100.0 * np.exp(np.cumsum(steps)))


def _run_signal(monkeypatch, capsys, argv, df=None, sent=None):
    monkeypatch.setattr(run_signal, "fetch_ohlcv", lambda symbol, **kw: df if df is not None else _uptrend_df())
    if sent is not None:
        monkeypatch.setattr(
            run_signal, "send_webhook",
            lambda url, text, **kw: sent.append((url, text)) or True,
        )
    monkeypatch.setattr(sys, "argv", ["run_signal.py"] + argv)
    run_signal.main()
    return capsys.readouterr()


def test_signal_notify_sends(monkeypatch, capsys):
    """--notify 指定 webhook：推送内容包含标的与动作，JSON 标记 notified。"""
    sent: list = []
    out = _run_signal(
        monkeypatch, capsys,
        ["--symbols", "TEST.SH", "--strategy", "ma_cross",
         "--notify", "https://example.com/hook", "--json"],
        sent=sent,
    )
    assert len(sent) == 1
    assert "TEST.SH" in sent[0][1]
    payload = json.loads(out.out)
    assert payload["notified"] is True


def test_signal_notify_env_var(monkeypatch, capsys):
    """未传 --notify 时读 ALPHA_FORGE_WEBHOOK 环境变量。"""
    sent: list = []
    monkeypatch.setenv("ALPHA_FORGE_WEBHOOK", "https://example.com/hook")
    _run_signal(
        monkeypatch, capsys,
        ["--symbols", "TEST.SH", "--strategy", "ma_cross"],
        sent=sent,
    )
    assert len(sent) == 1


def test_signal_notify_only_changes_skips_hold(monkeypatch, capsys):
    """--notify-only-changes：动作全为持有/观望时不推送。"""
    sent: list = []
    # 恒定价格 → ma_cross 无交叉 → 观望
    flat = make_ohlcv(np.full(300, 100.0))
    out = _run_signal(
        monkeypatch, capsys,
        ["--symbols", "TEST.SH", "--strategy", "ma_cross",
         "--notify", "https://example.com/hook", "--notify-only-changes", "--json"],
        df=flat, sent=sent,
    )
    assert sent == []
    assert json.loads(out.out)["notified"] is None


def test_signal_no_notify_by_default(monkeypatch, capsys):
    """未配置 webhook 时不推送，notified 为 null。"""
    monkeypatch.delenv("ALPHA_FORGE_WEBHOOK", raising=False)
    sent: list = []
    out = _run_signal(
        monkeypatch, capsys,
        ["--symbols", "TEST.SH", "--strategy", "ma_cross", "--json"],
        sent=sent,
    )
    assert sent == []
    assert json.loads(out.out)["notified"] is None
