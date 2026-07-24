"""价值筛选模块单元测试：mock 数据验证筛选逻辑与评分计算。"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from screener import PRESETS, ScreenCriteria, ScreenResult, composite_score, run_screen
from screener.engine import (
    _check_all_criteria,
    _check_detail_criteria,
    _code_to_symbol,
    _filter_price_position,
    _sort_results,
    screen_astock_phase1,
    screen_astock_phase2,
    screen_yfinance,
)

# ---------------------------------------------------------------------------
# ScreenCriteria
# ---------------------------------------------------------------------------


class TestScreenCriteria:
    def test_defaults(self):
        c = ScreenCriteria()
        assert c.max_pe == 20.0
        assert c.max_pb == 3.0
        assert c.min_roe == 10.0
        assert c.max_debt == 70.0
        assert c.min_div == 0.0
        assert c.min_growth == 0.0
        assert c.min_cap == 30.0
        # 十倍股维度默认全部不启用（不改变存量行为）
        assert c.max_cap == 0.0
        assert c.min_cash_yield == 0.0
        assert c.smart_growth is False
        assert c.max_price_pos == 0.0

    def test_to_dict(self):
        c = ScreenCriteria(max_pe=15, min_div=3)
        d = c.to_dict()
        assert d["max_pe"] == 15
        assert d["min_div"] == 3
        assert "max_pb" in d
        # 未启用的十倍股维度不出现在契约中
        assert "max_cap" not in d
        assert "smart_growth" not in d

    def test_to_dict_multibagger_dims(self):
        c = ScreenCriteria(max_cap=200, min_cash_yield=6, smart_growth=True, max_price_pos=0.5)
        d = c.to_dict()
        assert d["max_cap"] == 200
        assert d["min_cash_yield"] == 6
        assert d["smart_growth"] is True
        assert d["max_price_pos"] == 0.5


class TestPresets:
    def test_multibagger_preset_exists(self):
        assert "multibagger" in PRESETS

    def test_multibagger_preset_keys_are_criteria_fields(self):
        """预设键必须是 ScreenCriteria 字段（同时也是 CLI dest）。"""
        fields = set(ScreenCriteria.__dataclass_fields__)
        for key in PRESETS["multibagger"]:
            assert key in fields

    def test_multibagger_preset_semantics(self):
        """预设语义：小市值+便宜+现金流+聪明增长+低位，不看 PE、不要求高增长。"""
        p = PRESETS["multibagger"]
        assert p["max_pe"] == 0.0            # 不看 PE
        assert 0 < p["max_pb"] <= 2.0        # 便宜
        assert p["max_cap"] > p["min_cap"]   # 市值区间合法
        assert p["min_cash_yield"] > 0       # 现金流因子启用
        assert p["smart_growth"] is True
        assert 0 < p["max_price_pos"] <= 1.0


# ---------------------------------------------------------------------------
# composite_score
# ---------------------------------------------------------------------------


class TestCompositeScore:
    def test_all_perfect(self):
        """所有指标刚好达标 → 子分 50（ratio=1 → 1/2*100=50）。"""
        c = ScreenCriteria(max_pe=20, max_pb=3, min_roe=10, max_debt=70)
        metrics = {"pe": 20, "pb": 3, "roe": 10, "debt_ratio": 70}
        score = composite_score(metrics, c)
        assert 45 <= score <= 55  # 各维度 ratio=1 → 50 分

    def test_better_than_threshold(self):
        """指标远优于阈值 → 高分。"""
        c = ScreenCriteria(max_pe=20, max_pb=3, min_roe=10)
        metrics = {"pe": 5, "pb": 0.5, "roe": 30}
        score = composite_score(metrics, c)
        assert score > 70

    def test_worse_than_threshold(self):
        """指标劣于阈值 → 低分。"""
        c = ScreenCriteria(max_pe=20, min_roe=10)
        metrics = {"pe": 40, "roe": 3}
        score = composite_score(metrics, c)
        assert score < 30

    def test_disabled_dimensions_excluded(self):
        """阈值=0 的维度不参与评分。"""
        c = ScreenCriteria(max_pe=20, max_pb=0, min_roe=0)
        metrics = {"pe": 10, "pb": 100, "roe": 1}  # pb/roe 很差但被禁用
        score = composite_score(metrics, c)
        # 只有 PE 参与：ratio=20/10=2 → cap 2 → 100 分
        assert score == 100.0

    def test_no_active_dimensions(self):
        """所有维度禁用 → 0 分。"""
        c = ScreenCriteria(max_pe=0, max_pb=0, min_roe=0, max_debt=0)
        metrics = {"pe": 10, "pb": 1, "roe": 20}
        score = composite_score(metrics, c)
        assert score == 0.0

    def test_missing_metrics(self):
        """缺失指标不参与评分。"""
        c = ScreenCriteria(max_pe=20, min_roe=10)
        metrics = {"pe": 10, "roe": None}  # ROE 缺失
        score = composite_score(metrics, c)
        # 只有 PE 参与
        assert score == 100.0

    def test_negative_pe_excluded(self):
        """PE 为负（亏损）不参与评分。"""
        c = ScreenCriteria(max_pe=20)
        metrics = {"pe": -5}
        score = composite_score(metrics, c)
        assert score == 0.0


# ---------------------------------------------------------------------------
# _check_detail_criteria
# ---------------------------------------------------------------------------


class TestCheckDetailCriteria:
    def test_all_pass(self):
        c = ScreenCriteria(min_roe=10, max_debt=70, min_growth=5)
        metrics = {"roe": 15, "debt_ratio": 50, "profit_growth": 10}
        assert _check_detail_criteria(metrics, c) == []

    def test_roe_fail(self):
        c = ScreenCriteria(min_roe=15, max_debt=0, min_growth=0)
        metrics = {"roe": 8}
        reasons = _check_detail_criteria(metrics, c)
        assert len(reasons) == 1
        assert "ROE" in reasons[0]

    def test_debt_fail(self):
        c = ScreenCriteria(min_roe=0, max_debt=60, min_growth=0)
        metrics = {"debt_ratio": 80}
        reasons = _check_detail_criteria(metrics, c)
        assert len(reasons) == 1
        assert "负债率" in reasons[0]

    def test_growth_fail(self):
        c = ScreenCriteria(min_roe=0, max_debt=0, min_growth=10)
        metrics = {"profit_growth": -5}
        reasons = _check_detail_criteria(metrics, c)
        assert len(reasons) == 1
        assert "增速" in reasons[0]

    def test_missing_data(self):
        c = ScreenCriteria(min_roe=10)
        metrics = {"roe": None}
        reasons = _check_detail_criteria(metrics, c)
        assert "缺失" in reasons[0]

    def test_disabled_dimensions(self):
        c = ScreenCriteria(min_roe=0, max_debt=0, min_growth=0)
        metrics = {"roe": None, "debt_ratio": None}
        assert _check_detail_criteria(metrics, c) == []

    def test_cash_yield_pass_fail(self):
        c = ScreenCriteria(min_roe=0, max_debt=0, min_cash_yield=6)
        assert _check_detail_criteria({"cash_yield": 8.0}, c) == []
        reasons = _check_detail_criteria({"cash_yield": 3.0}, c)
        assert any("现金流" in r for r in reasons)
        reasons = _check_detail_criteria({"cash_yield": None}, c)
        assert any("缺失" in r for r in reasons)

    def test_smart_growth_pass_fail(self):
        c = ScreenCriteria(min_roe=0, max_debt=0, smart_growth=True)
        # 资产增速 < 利润增速 → 通过
        assert _check_detail_criteria({"asset_growth": 5, "profit_growth": 20}, c) == []
        # 资产增速 ≥ 利润增速 → 扩张低效
        reasons = _check_detail_criteria({"asset_growth": 30, "profit_growth": 10}, c)
        assert any("扩张低效" in r for r in reasons)
        # 数据缺失（如港美股无资产增速）→ 剔除
        reasons = _check_detail_criteria({"asset_growth": None, "profit_growth": 10}, c)
        assert any("缺失" in r for r in reasons)


# ---------------------------------------------------------------------------
# _check_all_criteria
# ---------------------------------------------------------------------------


class TestCheckAllCriteria:
    def test_pe_fail(self):
        c = ScreenCriteria(max_pe=15)
        metrics = {"pe": 25}
        reasons = _check_all_criteria(metrics, c)
        assert any("PE" in r for r in reasons)

    def test_pb_fail(self):
        c = ScreenCriteria(max_pb=2)
        metrics = {"pb": 5}
        reasons = _check_all_criteria(metrics, c)
        assert any("PB" in r for r in reasons)

    def test_cap_fail(self):
        c = ScreenCriteria(min_cap=50)
        metrics = {"total_mv": 20}
        reasons = _check_all_criteria(metrics, c)
        assert any("市值" in r for r in reasons)

    def test_div_fail(self):
        c = ScreenCriteria(min_div=3)
        metrics = {"div_yield": 1}
        reasons = _check_all_criteria(metrics, c)
        assert any("股息" in r for r in reasons)

    def test_max_cap_fail(self):
        """市值上限：十倍股筛选要求小市值起步。"""
        c = ScreenCriteria(max_cap=200)
        metrics = {"total_mv": 500}
        reasons = _check_all_criteria(metrics, c)
        assert any("市值" in r and ">" in r for r in reasons)
        assert _check_all_criteria({"total_mv": 100}, ScreenCriteria(max_cap=200, max_pe=0, max_pb=0, min_roe=0, max_debt=0)) == []

    def test_price_pos_fail(self):
        """52 周位置：位置偏高或数据缺失都剔除。"""
        c = ScreenCriteria(max_pe=0, max_pb=0, min_roe=0, max_debt=0, min_cap=0, max_price_pos=0.5)
        assert _check_all_criteria({"price_pos": 0.3}, c) == []
        reasons = _check_all_criteria({"price_pos": 0.9}, c)
        assert any("位置偏高" in r for r in reasons)
        reasons = _check_all_criteria({"price_pos": None}, c)
        assert any("缺失" in r for r in reasons)

    def test_all_pass(self):
        c = ScreenCriteria(max_pe=20, max_pb=3, min_cap=30, min_div=2, min_roe=10, max_debt=0, min_growth=0)
        metrics = {"pe": 10, "pb": 1.5, "total_mv": 100, "div_yield": 4, "roe": 15}
        assert _check_all_criteria(metrics, c) == []


# ---------------------------------------------------------------------------
# _code_to_symbol
# ---------------------------------------------------------------------------


class TestCodeToSymbol:
    def test_sh(self):
        assert _code_to_symbol("600000") == "600000.SH"
        assert _code_to_symbol("601318") == "601318.SH"

    def test_sz(self):
        assert _code_to_symbol("000001") == "000001.SZ"
        assert _code_to_symbol("300750") == "300750.SZ"

    def test_bj(self):
        assert _code_to_symbol("430047") == "430047.BJ"
        assert _code_to_symbol("830799") == "830799.BJ"


# ---------------------------------------------------------------------------
# _sort_results
# ---------------------------------------------------------------------------


class TestSortResults:
    def _make_results(self):
        return [
            ScreenResult("A", "A", {"pe": 10, "roe": 20, "div_yield": 3}, 80, True),
            ScreenResult("B", "B", {"pe": 5, "roe": 15, "div_yield": 5}, 70, True),
            ScreenResult("C", "C", {"pe": 15, "roe": 25, "div_yield": 1}, 90, True),
        ]

    def test_sort_by_score(self):
        results = _sort_results(self._make_results(), "score")
        assert [r.symbol for r in results] == ["C", "A", "B"]

    def test_sort_by_pe(self):
        results = _sort_results(self._make_results(), "pe")
        assert [r.symbol for r in results] == ["B", "A", "C"]

    def test_sort_by_roe(self):
        results = _sort_results(self._make_results(), "roe")
        assert [r.symbol for r in results] == ["C", "A", "B"]

    def test_sort_by_div(self):
        results = _sort_results(self._make_results(), "div")
        assert [r.symbol for r in results] == ["B", "A", "C"]


# ---------------------------------------------------------------------------
# ScreenResult.to_dict
# ---------------------------------------------------------------------------


class TestScreenResult:
    def test_to_dict(self):
        r = ScreenResult(
            symbol="600000.SH",
            name="浦发银行",
            metrics={"pe": 5.2, "pb": 0.6, "roe": 12.5, "close": 8.5},
            score=85.3,
            passed=True,
        )
        d = r.to_dict()
        assert d["symbol"] == "600000.SH"
        assert d["score"] == 85.3
        assert d["passed"] is True
        assert d["pe"] == 5.2
        assert d["pb"] == 0.6

    def test_to_dict_none_metrics(self):
        r = ScreenResult(
            symbol="AAPL.US",
            name="Apple",
            metrics={"pe": 30, "roe": None},
            score=60,
            passed=False,
            fail_reasons=["ROE 数据缺失"],
        )
        d = r.to_dict()
        assert d["roe"] is None
        assert d["fail_reasons"] == ["ROE 数据缺失"]


# ---------------------------------------------------------------------------
# Phase 1: screen_astock_phase1 (mock)
# ---------------------------------------------------------------------------


class TestScreenAstockPhase1:
    def _mock_snapshot(self):
        return pd.DataFrame({
            "code": ["600000", "000001", "300750", "600001"],
            "name": ["浦发银行", "平安银行", "宁德时代", "ST测试"],
            "close": [8.5, 12.0, 200.0, 3.0],
            "pe": [5.2, 8.0, 50.0, 10.0],
            "pb": [0.6, 0.9, 8.0, 1.0],
            "total_mv": [2500, 2300, 9000, 20],  # 亿
            "div_yield": [4.5, 3.0, 0.5, 0.0],
        })

    @patch("screener.engine.fetch_astock_snapshot")
    def test_basic_filter(self, mock_fetch):
        mock_fetch.return_value = self._mock_snapshot()
        criteria = ScreenCriteria(max_pe=20, max_pb=3, min_cap=30)
        survivors, total = screen_astock_phase1(criteria)

        assert total == 4
        # 300750 PE=50>20 被过滤；600001 是 ST 被过滤；600001 市值 20<30 也被过滤
        codes = [s["code"] for s in survivors]
        assert "600000" in codes
        assert "000001" in codes
        assert "300750" not in codes  # PE 过高
        assert "600001" not in codes  # ST

    @patch("screener.engine.fetch_astock_snapshot")
    def test_empty_snapshot(self, mock_fetch):
        mock_fetch.return_value = None
        criteria = ScreenCriteria()
        survivors, total = screen_astock_phase1(criteria)
        assert survivors == []
        assert total == 0

    @patch("screener.engine.fetch_astock_snapshot")
    def test_no_pe_filter(self, mock_fetch):
        """max_pe=0 时不过滤 PE。"""
        mock_fetch.return_value = self._mock_snapshot()
        criteria = ScreenCriteria(max_pe=0, max_pb=0, min_cap=0)
        survivors, total = screen_astock_phase1(criteria)
        # 只排除 ST
        assert len(survivors) == 3

    @patch("screener.engine.fetch_astock_snapshot")
    def test_max_cap_filter(self, mock_fetch):
        """市值上限：剔除大市值，只留小市值（十倍股预设路径）。"""
        mock_fetch.return_value = self._mock_snapshot()
        criteria = ScreenCriteria(max_pe=0, max_pb=0, min_cap=0, max_cap=3000)
        survivors, _ = screen_astock_phase1(criteria)
        codes = [s["code"] for s in survivors]
        assert "300750" not in codes  # 市值 9000 亿 > 3000 亿
        assert "600000" in codes


# ---------------------------------------------------------------------------
# Phase 2: screen_astock_phase2 (mock)
# ---------------------------------------------------------------------------


class TestScreenAstockPhase2:
    @patch("screener.engine.fetch_astock_detail")
    def test_detail_filter(self, mock_detail):
        mock_detail.return_value = {"roe": 15, "debt_ratio": 50, "profit_growth": 10}
        survivors = [
            {"code": "600000", "name": "浦发银行", "pe": 5.2, "pb": 0.6,
             "total_mv": 2500, "div_yield": 4.5, "close": 8.5},
        ]
        criteria = ScreenCriteria(min_roe=10, max_debt=70)
        results = screen_astock_phase2(survivors, criteria)
        assert len(results) == 1
        assert results[0].passed is True
        assert results[0].metrics["roe"] == 15

    @patch("screener.engine.fetch_astock_detail")
    def test_detail_fail(self, mock_detail):
        mock_detail.return_value = {"roe": 5, "debt_ratio": 80, "profit_growth": -10}
        survivors = [
            {"code": "600000", "name": "测试", "pe": 10, "pb": 1,
             "total_mv": 100, "div_yield": 2, "close": 10},
        ]
        criteria = ScreenCriteria(min_roe=10, max_debt=70)
        results = screen_astock_phase2(survivors, criteria)
        assert len(results) == 0  # 不通过

    @patch("screener.engine.fetch_astock_detail")
    def test_detail_fetch_fail(self, mock_detail):
        mock_detail.return_value = None
        survivors = [
            {"code": "600000", "name": "测试", "pe": 10, "pb": 1,
             "total_mv": 100, "div_yield": 2, "close": 10},
        ]
        criteria = ScreenCriteria(min_roe=10)
        results = screen_astock_phase2(survivors, criteria)
        assert len(results) == 0  # 拉取失败跳过

    @patch("screener.engine.fetch_astock_detail")
    def test_no_detail_needed(self, mock_detail):
        """无深度指标阈值时不调用 detail 接口。"""
        survivors = [
            {"code": "600000", "name": "测试", "pe": 10, "pb": 1,
             "total_mv": 100, "div_yield": 2, "close": 10},
        ]
        criteria = ScreenCriteria(min_roe=0, max_debt=0, min_growth=0, min_div=0)
        results = screen_astock_phase2(survivors, criteria)
        assert len(results) == 1
        mock_detail.assert_not_called()

    @patch("screener.engine.fetch_astock_detail")
    def test_cash_yield_computed(self, mock_detail):
        """现金流收益率 = 每股经营现金流 / 股价，并参与阈值过滤。"""
        mock_detail.return_value = {
            "roe": 8, "debt_ratio": 40, "profit_growth": 15,
            "asset_growth": 5, "ocf_per_share": 1.2,
        }
        survivors = [
            {"code": "600000", "name": "测试", "pe": 10, "pb": 1,
             "total_mv": 100, "div_yield": 2, "close": 10.0},
        ]
        # 1.2/10 = 12% ≥ 6% → 通过；同时验证聪明增长（5 < 15）
        criteria = ScreenCriteria(min_roe=5, max_debt=70, min_cash_yield=6, smart_growth=True)
        results = screen_astock_phase2(survivors, criteria)
        assert len(results) == 1
        assert results[0].metrics["cash_yield"] == pytest.approx(12.0)
        # 阈值抬高到 15% → 剔除
        criteria = ScreenCriteria(min_roe=5, max_debt=70, min_cash_yield=15)
        results = screen_astock_phase2(survivors, criteria)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# screen_yfinance (mock)
# ---------------------------------------------------------------------------


class TestScreenYfinance:
    @patch("screener.engine.fetch_yfinance_metrics")
    def test_basic(self, mock_yf):
        mock_yf.return_value = {
            "name": "Apple", "close": 180, "pe": 28, "pb": 2.5,
            "roe": 150, "div_yield": 0.5, "debt_ratio": 50,
            "profit_growth": 10, "total_mv": 28000,
        }
        criteria = ScreenCriteria(max_pe=30, max_pb=3, min_roe=20, max_debt=0, min_cap=0)
        results = screen_yfinance(["AAPL.US"], criteria)
        assert len(results) == 1
        assert results[0].symbol == "AAPL.US"

    @patch("screener.engine.fetch_yfinance_metrics")
    def test_fetch_fail(self, mock_yf):
        mock_yf.return_value = None
        criteria = ScreenCriteria()
        results = screen_yfinance(["INVALID.US"], criteria)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# run_screen 集成 (mock)
# ---------------------------------------------------------------------------


class TestRunScreen:
    @patch("screener.engine.fetch_yfinance_metrics")
    def test_manual_symbols(self, mock_yf):
        mock_yf.return_value = {
            "name": "Test", "close": 100, "pe": 10, "pb": 1.5,
            "roe": 20, "div_yield": 3, "debt_ratio": 40,
            "profit_growth": 15, "total_mv": 500,
        }
        criteria = ScreenCriteria(max_pe=20, min_roe=10)
        result = run_screen(criteria, symbols=["600519.SH", "AAPL.US"])
        assert result["n_scanned"] == 2
        assert result["n_final"] >= 0

    @patch("screener.engine.screen_astock_phase2")
    @patch("screener.engine.screen_astock_phase1")
    def test_astock_bulk(self, mock_p1, mock_p2):
        mock_p1.return_value = (
            [{"code": "600000", "name": "浦发", "pe": 5, "pb": 0.6,
              "total_mv": 2500, "div_yield": 4, "close": 8}],
            5000,
        )
        mock_p2.return_value = [
            ScreenResult("600000.SH", "浦发", {"pe": 5, "pb": 0.6, "roe": 12}, 85, True)
        ]
        criteria = ScreenCriteria()
        result = run_screen(criteria, symbols=None)
        assert result["n_scanned"] == 5000
        assert result["n_final"] == 1
        assert len(result["candidates"]) == 1


# ---------------------------------------------------------------------------
# Phase 3: _filter_price_position (mock)
# ---------------------------------------------------------------------------


class TestFilterPricePosition:
    def _make_results(self):
        return [
            ScreenResult("600001.SH", "低位", {"pb": 1.0, "price_pos": None}, 50, True),
            ScreenResult("600002.SH", "高位", {"pb": 1.0, "price_pos": None}, 50, True),
            ScreenResult("600003.SH", "无数据", {"pb": 1.0, "price_pos": None}, 50, True),
        ]

    @patch("screener.engine.fetch_price_position")
    def test_filter_keeps_low_position(self, mock_pos):
        mock_pos.side_effect = [0.2, 0.9, None]
        criteria = ScreenCriteria(max_pb=3, max_price_pos=0.5)
        kept = _filter_price_position(self._make_results(), criteria)
        assert [r.symbol for r in kept] == ["600001.SH"]
        assert kept[0].metrics["price_pos"] == 0.2

    @patch("screener.engine.fetch_price_position")
    def test_precomputed_position_not_refetched(self, mock_pos):
        """已有 price_pos（如 yfinance 路径）不重复拉日 K。"""
        r = ScreenResult("AAPL.US", "Apple", {"pb": 2.0, "price_pos": 0.3}, 50, True)
        criteria = ScreenCriteria(max_pb=3, max_price_pos=0.5)
        kept = _filter_price_position([r], criteria)
        assert len(kept) == 1
        mock_pos.assert_not_called()


# ---------------------------------------------------------------------------
# CLI 预设应用（_apply_preset）
# ---------------------------------------------------------------------------


class TestApplyPreset:
    def _parse(self, argv):
        import run_screener

        args = run_screener.build_parser().parse_args(argv)
        return run_screener._apply_preset(args, argv)

    def test_no_preset_keeps_defaults(self):
        args = self._parse([])
        assert args.max_pe == 20.0
        assert args.max_cap == 0.0
        assert args.smart_growth is False

    def test_multibagger_preset_applied(self):
        args = self._parse(["--preset", "multibagger"])
        p = PRESETS["multibagger"]
        assert args.max_pe == p["max_pe"]
        assert args.max_pb == p["max_pb"]
        assert args.max_cap == p["max_cap"]
        assert args.min_cash_yield == p["min_cash_yield"]
        assert args.smart_growth is True
        assert args.max_price_pos == p["max_price_pos"]

    def test_explicit_arg_overrides_preset(self):
        """显式参数 > 预设：--max-cap 300 覆盖预设的 200。"""
        args = self._parse(["--preset", "multibagger", "--max-cap", "300"])
        assert args.max_cap == 300.0
        # 未显式提供的项仍用预设
        assert args.max_pb == PRESETS["multibagger"]["max_pb"]

    def test_explicit_equals_form_overrides(self):
        """--max-cap=300 等号形式也能识别为显式参数。"""
        args = self._parse(["--preset", "multibagger", "--max-cap=300"])
        assert args.max_cap == 300.0
