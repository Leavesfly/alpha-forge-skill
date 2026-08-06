"""run_doctor.py 诊断 CLI 测试（全程 mock，不走网络）。"""

from __future__ import annotations

import numpy as np
import pytest

from run_doctor import (
    SOURCE_SPECS,
    _resolve_sources,
    _upstream_notes,
    mask_key,
    probe,
    summarize,
)
from tests.helpers import make_ohlcv


class _Src:
    """可配置的源替身。"""

    def __init__(self, supported=True, error=None, rows=60):
        self._supported = supported
        self._error = error
        self._rows = rows

    def supports(self, symbol, period):
        return self._supported

    def fetch(self, symbol, period, count, adjust):
        if self._error:
            raise RuntimeError(self._error)
        return make_ohlcv(100.0 * (1.0 + 0.001) ** np.arange(self._rows))


def _use_stub(monkeypatch, name, stub_cls):
    """把某个源的类替换成替身，保留其余元信息（Key/角色/上游）。"""
    cls, key_env, role, upstream = SOURCE_SPECS[name]
    monkeypatch.setitem(SOURCE_SPECS, name, (stub_cls, key_env, role, upstream))


# ─── Key 掩码（绝不能打印完整 Key）────────────────────────────────────────────


class TestMaskKey:
    def test_none_when_absent(self):
        assert mask_key(None) is None
        assert mask_key("") is None

    def test_keeps_only_prefix(self):
        assert mask_key("tk_0431234567890abcdef") == "tk_043***"

    def test_short_key_fully_masked(self):
        assert mask_key("abc") == "***"

    def test_never_leaks_tail(self):
        masked = mask_key("tk_secret_tail_zzzz")
        assert "tail" not in masked
        assert "zzzz" not in masked


# ─── --sources 解析 ───────────────────────────────────────────────────────────


class TestResolveSources:
    def test_default_is_all(self):
        assert _resolve_sources(None) == list(SOURCE_SPECS)

    def test_subset_preserves_given_order(self):
        assert _resolve_sources("yfinance,openbb") == ["yfinance", "openbb"]

    def test_unknown_name_exits_with_hint(self):
        with pytest.raises(SystemExit, match="未知数据源"):
            _resolve_sources("nope")


# ─── probe：单源单标的体检 ────────────────────────────────────────────────────


class TestProbe:
    def test_ok_record(self, monkeypatch):
        _use_stub(monkeypatch, "akshare", lambda: _Src(rows=60))
        rec = probe("akshare", "600000.SH", "1d", 60, "forward")
        assert rec["status"] == "ok"
        assert rec["rows"] == 60
        assert rec["last_bar_date"]
        assert rec["quality"]["passed"] is True
        assert rec["elapsed_sec"] >= 0
        assert rec["error"] is None

    def test_unsupported_is_skip_not_fail(self, monkeypatch):
        """源不覆盖该市场不是故障，必须与真失败区分开。"""
        _use_stub(monkeypatch, "baostock", lambda: _Src(supported=False))
        rec = probe("baostock", "AAPL.US", "1d", 60, "forward")
        assert rec["status"] == "skip"
        assert rec["supported"] is False
        assert "不覆盖" in rec["error"]
        assert rec["rows"] is None

    def test_fail_record_keeps_reason(self, monkeypatch):
        _use_stub(monkeypatch, "yfinance", lambda: _Src(error="429 Too Many Requests"))
        rec = probe("yfinance", "AAPL.US", "1d", 60, "forward")
        assert rec["status"] == "fail"
        assert "429" in rec["error"]
        assert rec["elapsed_sec"] is not None  # 失败也记耗时（区分超时与秒拒）

    def test_api_key_masked_in_record(self, monkeypatch):
        monkeypatch.setenv("TICKFLOW_API_KEY", "tk_0431234567890")
        _use_stub(monkeypatch, "tickflow", lambda: _Src())
        rec = probe("tickflow", "600000.SH", "1d", 60, "forward")
        assert rec["needs_api_key"] == "TICKFLOW_API_KEY"
        assert rec["api_key_configured"] is True
        assert rec["api_key_masked"] == "tk_043***"
        assert "1234567890" not in str(rec)

    def test_missing_key_reported_without_crash(self, monkeypatch):
        monkeypatch.delenv("TICKFLOW_API_KEY", raising=False)
        _use_stub(monkeypatch, "tickflow", lambda: _Src())
        rec = probe("tickflow", "600000.SH", "1d", 60, "forward")
        assert rec["api_key_configured"] is False
        assert rec["api_key_masked"] is None

    def test_quality_issue_surfaces(self, monkeypatch):
        """脏数据必须体现在体检结论里，不能只看「拉到了几行」。"""

        class _Dirty(_Src):
            def fetch(self, symbol, period, count, adjust):
                df = make_ohlcv(np.full(30, 10.0))
                df.loc[5, "close"] = -1.0  # 非正价格：error 级
                return df

        _use_stub(monkeypatch, "akshare", _Dirty)
        rec = probe("akshare", "600000.SH", "1d", 30, "forward")
        assert rec["status"] == "ok"  # 拉到了数据
        assert rec["quality"]["passed"] is False  # 但数据有问题
        assert "nonpositive_price" in rec["quality"]["issues"]


# ─── 汇总与同上游标注 ─────────────────────────────────────────────────────────


def _rec(source, symbol, status, upstream):
    return {"source": source, "symbol": symbol, "status": status, "upstream": upstream}


class TestSummarize:
    def test_groups_by_market(self):
        markets = summarize(
            [
                _rec("akshare", "600000.SH", "ok", "eastmoney"),
                _rec("yfinance", "AAPL.US", "ok", "yahoo"),
                _rec("yfinance", "00700.HK", "fail", "yahoo"),
            ]
        )
        assert set(markets) == {"CN", "US", "HK"}
        assert markets["CN"]["usable_sources"] == ["akshare"]
        assert markets["HK"]["usable_count"] == 0
        assert markets["HK"]["failed_sources"] == ["yfinance"]

    def test_same_upstream_counted_once(self):
        markets = summarize(
            [
                _rec("openbb", "AAPL.US", "ok", "yahoo"),
                _rec("yfinance", "AAPL.US", "ok", "yahoo"),
                _rec("tickflow", "AAPL.US", "ok", "tickflow"),
            ]
        )
        assert markets["US"]["usable_count"] == 3
        assert markets["US"]["independent_upstreams"] == 2  # yahoo 只算一个

    def test_skip_not_counted_as_failure(self):
        markets = summarize([_rec("baostock", "AAPL.US", "skip", "baostock")])
        assert markets["US"]["usable_count"] == 0
        assert markets["US"]["failed_sources"] == []

    def test_unknown_market_bucketed(self):
        markets = summarize([_rec("tickflow", "IF2401.CFX", "ok", "tickflow")])
        assert "OTHER" in markets


class TestUpstreamNotes:
    def test_notes_when_yahoo_pair_usable(self):
        markets = summarize(
            [
                _rec("openbb", "AAPL.US", "ok", "yahoo"),
                _rec("yfinance", "AAPL.US", "ok", "yahoo"),
            ]
        )
        notes = _upstream_notes(markets)
        assert len(notes) == 1
        assert "同上游 Yahoo" in notes[0]
        assert "独立上游实为 1 个" in notes[0]

    def test_no_note_when_upstreams_independent(self):
        markets = summarize(
            [
                _rec("akshare", "600000.SH", "ok", "eastmoney"),
                _rec("baostock", "600000.SH", "ok", "baostock"),
            ]
        )
        assert _upstream_notes(markets) == []

    def test_no_note_when_only_one_yahoo_source_usable(self):
        markets = summarize(
            [
                _rec("openbb", "AAPL.US", "fail", "yahoo"),
                _rec("yfinance", "AAPL.US", "ok", "yahoo"),
            ]
        )
        assert _upstream_notes(markets) == []


# ─── CLI 参数契约 ─────────────────────────────────────────────────────────────


class TestParser:
    def test_defaults_cover_three_markets(self):
        from run_doctor import build_parser

        args = build_parser().parse_args([])
        assert args.symbols == "600000.SH,00700.HK,AAPL.US"
        assert args.period == "1d"
        assert args.json is None

    def test_json_flag_defaults_to_stdout(self):
        from run_doctor import build_parser

        assert build_parser().parse_args(["--json"]).json == "-"
