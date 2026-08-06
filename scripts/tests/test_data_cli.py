"""run_data.py 数据取用 CLI 测试（全程 mock，不走网络）。

重点覆盖两件容易出错的事：
1. cache_hit / actual_source 的判定（Agent 据此判断数据新鲜度与来源可信度）；
2. 质量校验作用在**实际返回的数据**上，且失败不静默（quality_failed 计数）。
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

import run_data
from data.cache import CacheConfig, read_meta
from tests.helpers import make_ohlcv


def _args(**over) -> object:
    """构造与 build_parser 默认值一致的参数对象。"""
    base = run_data.build_parser().parse_args(["--symbols", "600000.SH"])
    for key, value in over.items():
        setattr(base, key, value)
    return base


# ─── cache.read_meta（数据层新增的审计入口）──────────────────────────────────────


class TestReadMeta:
    def test_none_when_no_cache(self, tmp_path):
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600)
        assert read_meta("600000.SH", "1d", "forward", "auto", cfg) is None

    def test_reads_written_meta(self, tmp_path):
        from data.cache import load_klines

        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600)

        def fetch():
            df = make_ohlcv([1.0, 2.0, 3.0])
            df.attrs["actual_source"] = "baostock"
            return df

        load_klines(fetch, "600000.SH", "1d", count=3, adjust="forward", config=cfg)
        meta = read_meta("600000.SH", "1d", "forward", "auto", cfg)
        assert meta["actual_source"] == "baostock"
        assert meta["rows"] == 3
        assert meta["last_bar_date"]

    def test_adjust_alias_normalized(self, tmp_path):
        """qfq 与 forward 是同一份缓存，不能因别名读不到。"""
        from data.cache import load_klines

        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600)
        load_klines(
            lambda: make_ohlcv([1.0, 2.0]), "600000.SH", "1d",
            count=2, adjust="forward", config=cfg,
        )
        assert read_meta("600000.SH", "1d", "qfq", "auto", cfg) is not None

    def test_corrupt_meta_returns_none(self, tmp_path):
        cfg = CacheConfig(cache_dir=tmp_path, ttl_seconds=3600)
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "600000_SH__1d__forward__auto.meta.json").write_text(
            "{not json", encoding="utf-8"
        )
        assert read_meta("600000.SH", "1d", "forward", "auto", cfg) is None


# ─── K 线取数记录 ───────────────────────────────────────────────────────────────


class TestKlineRecord:
    def test_source_fetch_reports_actual_source(self, monkeypatch):
        """联网直取：actual_source 来自 attrs，cache_hit 为假。"""

        def _fetch(symbol, period, count, adjust, use_cache):
            df = make_ohlcv(np.linspace(10.0, 11.0, count))
            df.attrs["actual_source"] = "openbb"
            return df

        monkeypatch.setattr("datafeed.fetch_ohlcv", _fetch)
        monkeypatch.setattr(run_data, "read_meta", lambda *a, **k: None)

        record, df = run_data._kline_record("600000.SH", _args(count=30))
        assert record["actual_source"] == "openbb"
        assert record["cache_hit"] is False
        assert record["rows"] == 30
        assert record["quality"]["passed"] is True
        assert len(record["records"]) == 30
        assert len(df) == 30

    def test_cache_hit_detected_by_unchanged_fetched_at(self, monkeypatch):
        """meta 的 fetched_at 没变 → 本次没联网，判为缓存命中。"""
        monkeypatch.setattr(
            "datafeed.fetch_ohlcv",
            lambda *a, **k: make_ohlcv(np.linspace(10.0, 11.0, 30)),
        )
        monkeypatch.setattr(
            run_data, "read_meta",
            lambda *a, **k: {"fetched_at": 111.0, "actual_source": "baostock",
                             "rows": 1250, "last_bar_date": "2026-08-05",
                             "fetched_date": "2026-08-05 15:35:35"},
        )
        record, _ = run_data._kline_record("600000.SH", _args(count=30))
        assert record["cache_hit"] is True
        assert record["actual_source"] == "baostock"  # 回读 meta 得到
        assert record["cache_meta"]["rows"] == 1250

    def test_refetch_detected_by_changed_fetched_at(self, monkeypatch):
        """attrs 丢失也不能误判：fetched_at 变了就说明本次真拉了网络。"""
        monkeypatch.setattr(
            "datafeed.fetch_ohlcv",
            lambda *a, **k: make_ohlcv(np.linspace(10.0, 11.0, 30)),  # 无 attrs
        )
        stamps = iter([{"fetched_at": 100.0}, {"fetched_at": 200.0, "actual_source": "akshare"}])
        monkeypatch.setattr(run_data, "read_meta", lambda *a, **k: next(stamps))

        record, _ = run_data._kline_record("600000.SH", _args(count=30))
        assert record["cache_hit"] is False
        assert record["actual_source"] == "akshare"

    def test_no_cache_skips_meta_lookup(self, monkeypatch):
        """--no-cache 时不该去读缓存 meta（那份数据与本次无关）。"""
        calls: list[int] = []

        def _read_meta(*a, **k):
            calls.append(1)
            return {"fetched_at": 1.0}

        monkeypatch.setattr(
            "datafeed.fetch_ohlcv",
            lambda *a, **k: make_ohlcv(np.linspace(10.0, 11.0, 5)),
        )
        monkeypatch.setattr(run_data, "read_meta", _read_meta)
        record, _ = run_data._kline_record("600000.SH", _args(count=5, no_cache=True))
        assert calls == []
        assert record["cache_hit"] is False
        assert record["cache_meta"] is None

    def test_missing_meta_never_reports_cache_hit(self, monkeypatch):
        """落盘失败/NO_CACHE 时两次 read_meta 都是 None，时间戳「没变」不能算命中。"""
        monkeypatch.setattr(
            "datafeed.fetch_ohlcv",
            lambda *a, **k: make_ohlcv(np.linspace(10.0, 11.0, 30)),  # 无 attrs
        )
        monkeypatch.setattr(run_data, "read_meta", lambda *a, **k: None)
        record, _ = run_data._kline_record("600000.SH", _args(count=30))
        assert record["cache_hit"] is False
        assert record["cache_meta"] is None

    def test_quality_issue_surfaced(self, monkeypatch):
        """脏数据必须体现在记录里，不能只报"取到了 N 行"。"""

        def _fetch(symbol, period, count, adjust, use_cache):
            df = make_ohlcv(np.full(20, 10.0))
            df.loc[5, "close"] = -1.0  # error 级
            return df

        monkeypatch.setattr("datafeed.fetch_ohlcv", _fetch)
        monkeypatch.setattr(run_data, "read_meta", lambda *a, **k: None)
        record, _ = run_data._kline_record("600000.SH", _args(count=20))
        assert record["quality"]["passed"] is False
        assert any(i["code"] == "nonpositive_price" for i in record["quality"]["issues"])

    def test_date_range_reported(self, monkeypatch):
        monkeypatch.setattr(
            "datafeed.fetch_ohlcv",
            lambda *a, **k: make_ohlcv([1.0, 2.0, 3.0], start="2024-03-01"),
        )
        monkeypatch.setattr(run_data, "read_meta", lambda *a, **k: None)
        record, _ = run_data._kline_record("600000.SH", _args(count=3))
        assert record["first_date"] == "2024-03-01"
        assert record["last_date"] == "2024-03-05"  # 3 个工作日


# ─── 非 K 线数据 ────────────────────────────────────────────────────────────────


class TestNonKline:
    def test_dividends_record(self, monkeypatch):
        series = pd.Series([0.32, 0.41], index=pd.DatetimeIndex(["2025-07-16", "2026-07-16"]))
        monkeypatch.setattr("data.dividends.fetch_dividends", lambda s: series)
        record, df = run_data._dividends_record("600000.SH")
        assert record["rows"] == 2
        assert record["total_dps"] == pytest.approx(0.73)
        assert record["records"][0] == {"date": "2025-07-16", "dps": 0.32}
        assert list(df.columns) == ["date", "dps"]

    def test_valuation_record(self, monkeypatch):
        from data.valuation import ValuationPercentile

        vp = ValuationPercentile(
            symbol="600000.SH", pe_current=6.15, pb_current=0.41,
            pe_percentile=0.6, pb_percentile=0.5, n_samples=1211,
            lookback_years=5.0, source="akshare",
        )
        monkeypatch.setattr(
            "data.valuation.fetch_valuation_percentile", lambda s, y: vp
        )
        record, df = run_data._valuation_record("600000.SH", 5)
        assert record["payload"]["pe_current"] == 6.15
        assert df is None

    def test_valuation_unavailable_raises_actionable_error(self, monkeypatch):
        from errors import DataFetchError

        monkeypatch.setattr(
            "data.valuation.fetch_valuation_percentile", lambda s, y: None
        )
        with pytest.raises(DataFetchError, match="估值分位数据不可用"):
            run_data._valuation_record("600000.SH", 5)

    def test_fundamentals_no_permission_raises_with_alternative(self, monkeypatch):
        """无财务权限时要指路免费替代方案，而不是只说失败。"""
        from errors import DataFetchError

        monkeypatch.setattr("data.fundamentals.fetch_fundamentals", lambda s: None)
        with pytest.raises(DataFetchError, match="run_screener"):
            run_data._fundamentals_record(["600000.SH"])

    def test_macro_all_missing_raises(self, monkeypatch):
        from data.macro import MacroSnapshot
        from errors import DataFetchError

        monkeypatch.setattr(
            "data.macro.fetch_macro_snapshot",
            lambda: MacroSnapshot(errors=["akshare 不可用"]),
        )
        with pytest.raises(DataFetchError, match="akshare 不可用"):
            run_data._macro_record()

    def test_macro_partial_is_usable(self, monkeypatch):
        from data.macro import MacroSnapshot

        monkeypatch.setattr(
            "data.macro.fetch_macro_snapshot",
            lambda: MacroSnapshot(bond_yield_10y=2.65, errors=["CPI 缺失"]),
        )
        record, _ = run_data._macro_record()
        assert record["payload"]["bond_yield_10y"] == 2.65


# ─── CSV 导出 ───────────────────────────────────────────────────────────────────


class TestCsvExport:
    def test_single_symbol_to_explicit_path(self, tmp_path, capsys):
        target = tmp_path / "out.csv"
        frames = {"600000.SH": make_ohlcv([1.0, 2.0, 3.0])}
        path = run_data._export_csv(frames, _args(csv=str(target)), lambda *a: None)
        assert path == str(target)
        assert len(pd.read_csv(target)) == 3

    def test_multi_symbol_merged_with_symbol_column(self, tmp_path):
        frames = {
            "600000.SH": make_ohlcv([1.0, 2.0]),
            "AAPL.US": make_ohlcv([3.0, 4.0]),
        }
        target = tmp_path / "merged.csv"
        run_data._export_csv(frames, _args(csv=str(target)), lambda *a: None)
        table = pd.read_csv(target)
        assert len(table) == 4
        assert set(table["symbol"]) == {"600000.SH", "AAPL.US"}

    def test_auto_path_lands_in_outputs_dir(self, tmp_path, monkeypatch):
        """--csv 不带值时走项目统一输出目录（绝对路径，不依赖 CWD）。"""
        import envconfig

        monkeypatch.setenv("ALPHA_FORGE_OUTPUT_DIR", str(tmp_path))
        envconfig.reset_env_config()
        try:
            frames = {"600000.SH": make_ohlcv([1.0, 2.0])}
            path = run_data._export_csv(frames, _args(csv="auto"), lambda *a: None)
        finally:
            envconfig.reset_env_config()
        assert path == str(tmp_path / "data_klines_600000SH.csv")
        assert len(pd.read_csv(path)) == 2

    def test_scalar_kind_skips_export(self, tmp_path, capsys):
        logs: list[str] = []
        path = run_data._export_csv(
            {"600000.SH": make_ohlcv([1.0])},
            _args(kind="valuation", csv="auto"),
            lambda *a: logs.append(" ".join(str(x) for x in a)),
        )
        assert path is None
        assert any("无表格可导出" in m for m in logs)

    def test_empty_frames_noop(self):
        assert run_data._export_csv({}, _args(csv="auto"), lambda *a: None) is None


# ─── 参数与摘要 ─────────────────────────────────────────────────────────────────


class TestParserAndSummary:
    def test_defaults(self):
        args = run_data.build_parser().parse_args(["--symbols", "600000.SH"])
        assert args.kind == "klines"
        assert args.period == "1d"
        assert args.count == 250
        assert args.csv is None
        assert args.no_cache is False

    def test_csv_flag_without_value_is_auto(self):
        args = run_data.build_parser().parse_args(["--symbols", "X.US", "--csv"])
        assert args.csv == "auto"

    def test_all_kinds_selectable(self):
        for kind in run_data.KINDS:
            args = run_data.build_parser().parse_args(["--kind", kind])
            assert args.kind == kind

    def test_summary_mentions_source_and_cache(self):
        results = [
            {"rows": 250, "actual_source": "tickflow", "cache_hit": False,
             "quality": {"passed": True}},
        ]
        text = run_data._summary(_args(), results, [], 0)
        assert "250" in text and "tickflow" in text

    def test_summary_flags_quality_and_errors(self):
        results = [{"rows": 10, "actual_source": "akshare", "cache_hit": True,
                    "quality": {"passed": False}}]
        errors = [{"symbol": "X.US", "error": "RuntimeError: 限流"}]
        text = run_data._summary(_args(), results, errors, 1)
        assert "质量问题" in text
        assert "限流" in text


# ─── 端到端（子进程，验证 stdout 纯净与退出码）──────────────────────────────────


class TestEndToEnd:
    def test_missing_symbols_exits_2(self):
        from tests.test_cli_contract import _run_cli

        result = _run_cli(["run_data.py", "--kind", "klines"])
        assert result.returncode == 2
        assert "[error]" in result.stderr

    def test_json_stdout_is_pure(self, monkeypatch, tmp_path):
        """--json 时 stdout 必须是纯 JSON（表格/进度不得混入）。"""
        from tests.test_cli_contract import _run_cli

        result = _run_cli(["run_data.py", "--kind", "macro", "--json"])
        if result.returncode != 0:  # 无网络时跳过（本用例不是联网测试）
            pytest.skip(f"macro 取数不可用：{result.stderr[-120:]}")
        payload = json.loads(result.stdout)
        assert payload["command"] == "data"
        assert payload["kind"] == "macro"
