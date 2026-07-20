"""run_score.py --fetch-events（事件风险 agent-in-the-loop 第一步）测试。

新闻抓取经 monkeypatch 替换为合成数据，不走网络。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import run_score
from run_score import fetch_event_material, load_risk_events


@pytest.fixture
def fake_news(monkeypatch):
    news = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-02"]),
            "title": ["公司拟减持公告", "半年报预告", "半年报预告"],
            "content": ["...", "...", "..."],
            "source": ["东财", "东财", "东财"],
            "url": ["u1", "u2", "u2"],
        }
    )
    monkeypatch.setattr("sentiment.news.fetch_stock_news", lambda symbol: news)
    return news


def test_fetch_event_material_writes_files(fake_news, monkeypatch, tmp_path):
    """产出素材 CSV 与待标注模板（risk 列留空，重复标题去重）。"""
    # 把 outputs 目录重定向到临时目录（通过 __file__ 所在目录的 parent 计算，
    # 用 monkeypatch 替换 Path 解析不可行，这里直接检查真实 outputs 产物后清理）
    logs: list[str] = []
    fetch_event_material("600000.SH", logs.append, None, None)

    out_dir = Path(run_score.__file__).resolve().parent.parent / "outputs"
    events = out_dir / "events_600000SH.csv"
    risk = out_dir / "risk_600000SH.csv"
    try:
        assert events.exists() and risk.exists()
        material = pd.read_csv(events)
        assert list(material.columns) == ["date", "title", "source", "url"]
        assert len(material) == 3

        template = pd.read_csv(risk)
        assert list(template.columns) == ["date", "risk", "note"]
        # 同日期同标题去重后 2 行；risk 列为空待 agent 填写
        assert len(template) == 2
        assert template["risk"].isna().all()

        # 空 risk 模板可被 load_risk_events 读取，engine 会忽略空值行
        records = load_risk_events(str(risk))
        assert all(r["risk"] == "" for r in records)
        assert any("标注" in line or "risk" in line for line in logs)
    finally:
        events.unlink(missing_ok=True)
        risk.unlink(missing_ok=True)
