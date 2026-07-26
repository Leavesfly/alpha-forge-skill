"""批量预同步测试：SyncReport 统计、失败容忍与股票池解析降级。"""

from __future__ import annotations

import pandas as pd
import pytest

from data.sync import SyncReport, sync_symbols
from run_sync import resolve_universe
from tests.helpers import make_ohlcv


def test_sync_symbols_empty_list():
    report = sync_symbols([])
    assert report.total == 0
    assert report.synced == 0
    assert report.failed == []


def test_sync_symbols_mixed_success_and_failure(monkeypatch):
    """成功/失败混合：失败记入清单不中断，统计正确。"""
    def fake_fetch(symbol, period="1d", count=1250, adjust="forward"):
        if symbol == "BAD.SH":
            raise RuntimeError("boom")
        return make_ohlcv([100, 101, 102])

    monkeypatch.setattr("datafeed.fetch_ohlcv", fake_fetch)
    report = sync_symbols(
        ["600000.SH", "BAD.SH", "000001.SZ"], count=3, workers=2
    )
    assert report.total == 3
    assert report.synced == 2
    assert len(report.failed) == 1
    assert report.failed[0][0] == "BAD.SH"
    assert "boom" in report.failed[0][1]
    assert report.elapsed_seconds >= 0.0


def test_sync_symbols_progress_log(monkeypatch):
    """完成最后一只时应输出进度（done == total 分支）。"""
    monkeypatch.setattr(
        "datafeed.fetch_ohlcv",
        lambda symbol, period="1d", count=1250, adjust="forward": make_ohlcv([1, 2]),
    )
    lines: list[str] = []
    sync_symbols(["600000.SH", "000001.SZ"], workers=1, log=lines.append)
    assert any("2/2" in line for line in lines)


def test_sync_report_dataclass_defaults():
    report = SyncReport()
    assert report.total == 0 and report.failed == []


# ---------------------------------------------------------------- 股票池解析


def test_resolve_universe_no_key_unsupported_raises(monkeypatch):
    """无 Key 且非 A 股/美股池（如港股）：明确报错要求配置 Key。"""
    monkeypatch.delenv("TICKFLOW_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TICKFLOW_API_KEY"):
        resolve_universe("HK_Equity", None, lambda *a: None)


def test_resolve_universe_no_key_us_falls_back_to_snapshot(monkeypatch):
    """无 Key 的美股池：降级东财美股快照，按市值降序取代码。"""
    monkeypatch.delenv("TICKFLOW_API_KEY", raising=False)
    snapshot = pd.DataFrame({
        "code": ["SMALL.US", "BIG.US"], "total_mv": [10.0, 30000.0],
    })
    monkeypatch.setattr("screener.data.fetch_us_snapshot", lambda log=None: snapshot)
    symbols = resolve_universe("US_Equity", None, lambda *a: None)
    assert symbols == ["BIG.US", "SMALL.US"]  # 市值降序


def test_resolve_universe_no_key_us_falls_back_to_sp500(monkeypatch):
    """无 Key 的美股池且快照不可用：再降级 S&P 500 名单；都不可用时报错。"""
    monkeypatch.delenv("TICKFLOW_API_KEY", raising=False)
    monkeypatch.setattr("screener.data.fetch_us_snapshot", lambda log=None: None)
    monkeypatch.setattr(
        "screener.data.fetch_sp500_symbols", lambda log=None: ["AAPL.US", "MSFT.US"]
    )
    assert resolve_universe("US_Equity", 1, lambda *a: None) == ["AAPL.US"]

    monkeypatch.setattr("screener.data.fetch_sp500_symbols", lambda log=None: None)
    with pytest.raises(RuntimeError, match="S&P 500"):
        resolve_universe("US_Equity", None, lambda *a: None)


def test_resolve_universe_no_key_cn_falls_back_to_snapshot(monkeypatch):
    """无 Key 的 A 股池：降级 akshare 快照并转带后缀代码。"""
    monkeypatch.delenv("TICKFLOW_API_KEY", raising=False)
    snapshot = pd.DataFrame({"code": ["600000", "000001", "430047"]})
    monkeypatch.setattr(
        "screener.data.fetch_astock_snapshot", lambda log=None: snapshot
    )
    symbols = resolve_universe("CN_Equity_A", None, lambda *a: None)
    assert symbols == ["600000.SH", "000001.SZ", "430047.BJ"]


def test_resolve_universe_no_key_snapshot_failure_raises(monkeypatch):
    """快照拉取失败：给出可操作错误。"""
    monkeypatch.delenv("TICKFLOW_API_KEY", raising=False)
    monkeypatch.setattr(
        "screener.data.fetch_astock_snapshot", lambda log=None: None
    )
    with pytest.raises(RuntimeError, match="快照"):
        resolve_universe("CN_Equity_A", None, lambda *a: None)


def test_resolve_universe_limit(monkeypatch):
    """--limit 截断池内数量。"""
    monkeypatch.delenv("TICKFLOW_API_KEY", raising=False)
    snapshot = pd.DataFrame({"code": ["600000", "000001", "300750"]})
    monkeypatch.setattr(
        "screener.data.fetch_astock_snapshot", lambda log=None: snapshot
    )
    symbols = resolve_universe("CN_Equity_A", 2, lambda *a: None)
    assert len(symbols) == 2
