"""价值筛选模块单元测试：mock 数据验证筛选逻辑与评分计算。"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from screener import (
    PRESETS,
    ScreenCriteria,
    ScreenResult,
    composite_score,
    market_regime,
    run_screen,
    screen_us_phase1,
)
from screener.engine import (
    _check_all_criteria,
    _check_detail_criteria,
    _check_tech_criteria,
    _code_to_symbol,
    _filter_dividend_years,
    _filter_drawdown,
    _filter_monster_tech,
    _filter_price_position,
    _filter_valuation_pct,
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
        # 猛兽股技术维度默认全部不启用（不改变存量行为）
        assert c.min_price_pos == 0.0
        assert c.trend_filter is False
        assert c.rs_filter is False
        assert c.min_updown_vol == 0.0
        assert c.market_filter is False
        # DHQ 维度默认全部不启用（不改变存量行为）
        assert c.min_gross_margin == 0.0
        assert c.min_drawdown == 0.0

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

    def test_to_dict_monster_dims(self):
        c = ScreenCriteria(
            min_price_pos=0.75, trend_filter=True, rs_filter=True,
            min_updown_vol=1.2, market_filter=True,
        )
        d = c.to_dict()
        assert d["min_price_pos"] == 0.75
        assert d["trend_filter"] is True
        assert d["rs_filter"] is True
        assert d["min_updown_vol"] == 1.2
        assert d["market_filter"] is True
        # 未启用时不出现在契约中
        d2 = ScreenCriteria().to_dict()
        for key in ("min_price_pos", "trend_filter", "rs_filter", "min_updown_vol", "market_filter"):
            assert key not in d2

    def test_to_dict_dhq_dims(self):
        c = ScreenCriteria(min_gross_margin=40, min_drawdown=20)
        d = c.to_dict()
        assert d["min_gross_margin"] == 40
        assert d["min_drawdown"] == 20
        # 未启用时不出现在契约中
        d2 = ScreenCriteria().to_dict()
        assert "min_gross_margin" not in d2
        assert "min_drawdown" not in d2

    def test_to_dict_fisher_dims(self):
        """费雪维度：研发强度/利润率趋势/反稀释默认不启用，启用后进入契约。"""
        assert ScreenCriteria().min_rd_ratio == 0.0
        assert ScreenCriteria().margin_trend is False
        assert ScreenCriteria().no_dilution is False
        c = ScreenCriteria(min_rd_ratio=3.0, margin_trend=True, no_dilution=True)
        d = c.to_dict()
        assert d["min_rd_ratio"] == 3.0
        assert d["margin_trend"] is True
        assert d["no_dilution"] is True
        d2 = ScreenCriteria().to_dict()
        for key in ("min_rd_ratio", "margin_trend", "no_dilution"):
            assert key not in d2

    def test_to_dict_dividend_dims(self):
        """红利股维度：连续分红/分位上限默认不启用；分位上限隐含启用分位拉取。"""
        assert ScreenCriteria().min_div_years == 0
        assert ScreenCriteria().max_val_pct == 0.0
        c = ScreenCriteria(min_div_years=5, max_val_pct=0.5)
        d = c.to_dict()
        assert d["min_div_years"] == 5
        assert d["max_val_pct"] == 0.5
        assert d["use_valuation_pct"] is True
        d2 = ScreenCriteria().to_dict()
        assert "min_div_years" not in d2
        assert "max_val_pct" not in d2

    def test_to_dict_navellier_dims(self):
        """纳维里尔维度：预期/惊喜/动能默认不启用，启用后进入契约。"""
        assert ScreenCriteria().min_forecast_growth == 0.0
        assert ScreenCriteria().earnings_surprise is False
        assert ScreenCriteria().eps_momentum is False
        c = ScreenCriteria(min_forecast_growth=15.0, earnings_surprise=True, eps_momentum=True)
        d = c.to_dict()
        assert d["min_forecast_growth"] == 15.0
        assert d["earnings_surprise"] is True
        assert d["eps_momentum"] is True
        d2 = ScreenCriteria().to_dict()
        for key in ("min_forecast_growth", "earnings_surprise", "eps_momentum"):
            assert key not in d2


class TestPresets:
    def test_multibagger_preset_exists(self):
        assert "multibagger" in PRESETS

    def test_hundredbagger_preset_exists(self):
        assert "hundredbagger" in PRESETS

    def test_monster_preset_exists(self):
        assert "monster" in PRESETS

    def test_dhq_preset_exists(self):
        assert "dhq" in PRESETS

    def test_superstock_preset_exists(self):
        assert "superstock" in PRESETS

    def test_fisher_preset_exists(self):
        assert "fisher" in PRESETS

    def test_navellier_preset_exists(self):
        assert "navellier" in PRESETS

    def test_dividend_preset_exists(self):
        assert "dividend" in PRESETS

    def test_dividend_preset_semantics(self):
        """预设语义（红利股左侧）：高股息+低估值+低分位+连续分红+财务稳健。"""
        p = PRESETS["dividend"]
        assert p["min_div"] >= 3.0            # 高股息
        assert 0 < p["max_pe"] <= 20.0        # 低估值
        assert 0 < p["max_pb"] <= 2.0
        assert 0 < p["max_val_pct"] <= 0.5    # 低分位硬条件（防周期顶部假低估）
        assert p["min_div_years"] >= 3        # 分红纪律
        assert p["min_roe"] > 0               # 利润支撑分红
        assert 0 < p["max_debt"] <= 70.0      # 低杠杆，股息不靠债撑

    def test_preset_keys_are_criteria_fields(self):
        """预设键必须是 ScreenCriteria 字段（同时也是 CLI dest）。"""
        fields = set(ScreenCriteria.__dataclass_fields__)
        for preset in PRESETS.values():
            for key in preset:
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

    def test_hundredbagger_preset_semantics(self):
        """预设语义（迈耶百倍股标准）：高 ROE+双高增+小市值+低杠杆，不卡 PB、不左侧择时。"""
        p = PRESETS["hundredbagger"]
        assert p["min_roe"] >= 20.0          # 核心：高 ROE 复利发动机
        assert p["min_growth"] > 0           # 利润高增
        assert p["min_rev_growth"] > 0       # 营收驱动
        assert p["max_pb"] == 0.0            # 不卡 PB（与高 ROE 不矛盾）
        assert p["max_pe"] > 0               # 合理价格宽松上限
        assert 0 < p["max_debt"] <= 70.0     # 低杠杆
        assert p["max_cap"] > p["min_cap"]   # 市值区间合法
        assert "max_price_pos" not in p      # 不择时：买对拿住

    def test_monster_preset_semantics(self):
        """预设语义（波伊克《猛兽股》）：右侧强势——大势+盈利高增+高位+RS+量价，不看估值。"""
        p = PRESETS["monster"]
        assert p["max_pe"] == 0.0            # 不看 PE：成长领导股 PE 普遍偏高
        assert p["max_pb"] == 0.0            # 不看 PB：买强不买便宜
        assert p["min_roe"] > 0              # 领导股质量
        assert p["min_growth"] >= 25.0       # 盈利高增（欧奈尔 C 标准）
        assert 0.5 <= p["min_price_pos"] <= 1.0  # 52 周高位：买强不买弱
        assert p["trend_filter"] is True     # 多头结构
        assert p["rs_filter"] is True        # RS 线跑赢基准
        assert p["min_updown_vol"] > 1.0     # 上涨放量下跌缩量
        assert p["market_filter"] is True    # 大势确认（必要条件）
        assert "max_price_pos" not in p      # 与左侧低位口径互斥

    def test_dhq_preset_semantics(self):
        """预设语义（马哈尼《高增长科技股投资法》）：高质量回调——营收高增+高毛利+规模+折扣，不看 PE/PB/ROE。"""
        p = PRESETS["dhq"]
        assert p["max_pe"] == 0.0            # 不看 PE：收入增速才是领先指标
        assert p["max_pb"] == 0.0            # 不看 PB：轻资产科技公司 PB 无意义
        assert p["min_roe"] == 0.0           # 不卡 ROE：高增长期利润被创新投入压低
        assert p["min_rev_growth"] >= 20.0   # 书中高增长门槛：营收增速 20%+
        assert p["min_gross_margin"] > 0     # 高毛利：定价权信号
        assert p["min_cap"] >= 100.0         # 已具规模：防小盘故事股
        assert 20.0 <= p["min_drawdown"] <= 30.0  # 折扣触发：书中 20%~30% 加仓区
        assert "max_price_pos" not in p      # 用回撤口径而非位置口径
        assert "market_filter" not in p      # 不择大势：回调多发生在弱市

    def test_superstock_preset_semantics(self):
        """预设语义（斯泰恩《100倍超级强势股》）：低 PE+盈利爆发+小市值+右侧突破结构的合流。"""
        p = PRESETS["superstock"]
        assert 0 < p["max_pe"] <= 10.0       # 书中标准：PE<10 锁死下行风险
        assert p["max_pb"] == 0.0            # 不看 PB
        assert p["min_roe"] == 0.0           # 不卡 ROE：爆发前盈利基数低
        assert 0 < p["max_debt"] <= 60.0     # 书中"无负债"的 A 股宽松口径
        assert p["min_growth"] >= 30.0       # 爆发性盈利
        assert p["min_rev_growth"] > 0       # 营收驱动，防一次性收益
        assert p["max_cap"] > p["min_cap"]   # 小市值区间合法（低流通盘代理）
        assert 0 < p["min_price_pos"] <= 0.75  # 已突破启动但不要求新高（回踩买）
        assert p["trend_filter"] is True     # 神奇支撑线：沿 10 周线≈MA50 多头结构
        assert p["min_updown_vol"] > 1.0     # 突破放量回调缩量（吸筹）
        assert "max_price_pos" not in p      # 与左侧低位口径互斥
        assert "market_filter" not in p      # 不强制择大势：合流已够严，留给用户叠加

    def test_fisher_preset_semantics(self):
        """预设语义（费雪《怎样选择成长股》15 要点）：营收+研发驱动的真成长，
        利润率趋势不恶化且无增发稀释，不卡小市值、不择时。"""
        p = PRESETS["fisher"]
        assert p["min_rd_ratio"] > 0         # 核心维度：研发是成长引擎（要点 3）
        assert p["min_growth"] > 0           # 利润增长高于平均（要点 1/2）
        assert p["min_rev_growth"] > 0       # 真成长由营收驱动（防削减成本假增长）
        assert p["min_gross_margin"] > 0     # 利润率高于行业（要点 5：定价权）
        assert p["margin_trend"] is True     # 维持并改善利润率（要点 6/7）
        assert p["min_roe"] >= 15.0          # 管理层高效运用资本
        assert p["smart_growth"] is True     # 再投资效率：资产增速<利润增速
        assert p["no_dilution"] is True      # 成长不靠股权融资稀释（要点 13）
        assert 0 < p["max_debt"] <= 70.0     # 财务稳健
        assert p["max_pe"] > 0               # 合理价格（宽松上限防纯故事股）
        assert p["max_pb"] == 0.0            # 不看 PB：评估质地而非账面资产
        assert "max_cap" not in p            # 不卡小市值：成熟成长公司也可
        assert "max_price_pos" not in p      # 不择时：买好公司长期持有

    def test_navellier_preset_semantics(self):
        """预设语义（纳维里尔 8 大指标）：双高增+高 ROE+现金流+利润率趋势
        +机构预期+盈利动能，不看估值、不卡市值；盈利惊喜（仅港美股）不入预设。"""
        p = PRESETS["navellier"]
        assert p["max_pe"] == 0.0              # 不看 PE：8 指标打分而非估值选股
        assert p["max_pb"] == 0.0              # 不看 PB
        assert p["min_roe"] >= 15.0            # 指标 8：高 ROE
        assert p["min_growth"] >= 20.0         # 指标 6：盈利高增
        assert p["min_rev_growth"] > 0         # 指标 3：营收驱动
        assert p["min_cash_yield"] > 0         # 指标 5：现金流验证盈利质量
        assert p["margin_trend"] is True       # 指标 4：利润率扩张（不恶化近似）
        assert p["min_forecast_growth"] > 0    # 指标 1 近似：机构预测增速
        assert p["eps_momentum"] is True       # 指标 7：增长在加速
        assert "earnings_surprise" not in p    # 指标 2 仅港美股有数据，A 股预设不启用
        assert "max_cap" not in p              # 不卡市值
        assert "max_price_pos" not in p        # 不做左侧择时


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

    def test_min_price_pos_high_is_better(self):
        """猛兽股口径：位置越高（越接近 52 周新高）得分越高，与左侧口径相反。"""
        c = ScreenCriteria(max_pe=0, max_pb=0, min_roe=0, max_debt=0, min_price_pos=0.75)
        high = composite_score({"price_pos": 0.95}, c)
        low = composite_score({"price_pos": 0.76}, c)
        assert high > low
        assert high == 95.0  # 位置 95% → 95 分（唯一启用维度）

    def test_rs_excess_score(self):
        """RS 超额 0~50pp 线性映射；超过 50pp 封顶。"""
        c = ScreenCriteria(max_pe=0, max_pb=0, min_roe=0, max_debt=0, rs_filter=True)
        assert composite_score({"rs_excess": 25.0}, c) == 50.0
        assert composite_score({"rs_excess": 80.0}, c) == 100.0
        assert composite_score({"rs_excess": -10.0}, c) == 0.0

    def test_updown_vol_score(self):
        """量比达标程度评分：阈值处 50 分，2 倍阈值封顶 100 分。"""
        c = ScreenCriteria(max_pe=0, max_pb=0, min_roe=0, max_debt=0, min_updown_vol=1.2)
        assert composite_score({"updown_vol_ratio": 1.2}, c) == 50.0
        assert composite_score({"updown_vol_ratio": 2.4}, c) == 100.0

    def test_gross_margin_score(self):
        """毛利率达标程度评分：阈值处 50 分，2 倍阈值封顶 100 分。"""
        c = ScreenCriteria(max_pe=0, max_pb=0, min_roe=0, max_debt=0, min_gross_margin=40)
        assert composite_score({"gross_margin": 40.0}, c) == 50.0
        assert composite_score({"gross_margin": 80.0}, c) == 100.0

    def test_rd_ratio_score(self):
        """研发强度达标程度评分：阈值处 50 分，2 倍阈值封顶 100 分；缺失不参与。"""
        c = ScreenCriteria(max_pe=0, max_pb=0, min_roe=0, max_debt=0, min_rd_ratio=3)
        assert composite_score({"rd_ratio": 3.0}, c) == 50.0
        assert composite_score({"rd_ratio": 6.0}, c) == 100.0
        assert composite_score({"rd_ratio": None}, c) == 0.0

    def test_drawdown_score_deeper_is_better(self):
        """DHQ 口径：回撤越深折扣越大得分越高（阈值处 50 分，2 倍阈值封顶）。"""
        c = ScreenCriteria(max_pe=0, max_pb=0, min_roe=0, max_debt=0, min_drawdown=20)
        assert composite_score({"drawdown": 20.0}, c) == 50.0
        assert composite_score({"drawdown": 40.0}, c) == 100.0
        assert composite_score({"drawdown": 30.0}, c) > composite_score({"drawdown": 22.0}, c)

    def test_forecast_growth_score(self):
        """机构预测增速达标程度评分：阈值处 50 分，2 倍阈值封顶；缺失不参与。"""
        c = ScreenCriteria(max_pe=0, max_pb=0, min_roe=0, max_debt=0, min_forecast_growth=15)
        assert composite_score({"forecast_growth": 15.0}, c) == 50.0
        assert composite_score({"forecast_growth": 30.0}, c) == 100.0
        assert composite_score({"forecast_growth": None}, c) == 0.0


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

    def test_rev_growth_pass_fail(self):
        """营收增速维度（百倍股：增长须由营收驱动）。"""
        c = ScreenCriteria(min_roe=0, max_debt=0, min_rev_growth=15)
        assert _check_detail_criteria({"revenue_growth": 20.0}, c) == []
        reasons = _check_detail_criteria({"revenue_growth": 5.0}, c)
        assert any("营收增速" in r for r in reasons)
        reasons = _check_detail_criteria({"revenue_growth": None}, c)
        assert any("缺失" in r for r in reasons)

    def test_gross_margin_pass_fail(self):
        """毛利率维度（DHQ：高毛利=定价权信号）。"""
        c = ScreenCriteria(min_roe=0, max_debt=0, min_gross_margin=40)
        assert _check_detail_criteria({"gross_margin": 55.0}, c) == []
        reasons = _check_detail_criteria({"gross_margin": 25.0}, c)
        assert any("定价权不足" in r for r in reasons)
        reasons = _check_detail_criteria({"gross_margin": None}, c)
        assert any("缺失" in r for r in reasons)

    def test_rd_ratio_pass_fail(self):
        """研发强度维度（费雪：研发是成长引擎）。"""
        c = ScreenCriteria(min_roe=0, max_debt=0, min_rd_ratio=3)
        assert _check_detail_criteria({"rd_ratio": 5.0}, c) == []
        reasons = _check_detail_criteria({"rd_ratio": 1.2}, c)
        assert any("研发引擎不足" in r for r in reasons)
        reasons = _check_detail_criteria({"rd_ratio": None}, c)
        assert any("未披露" in r for r in reasons)

    def test_margin_trend_pass_fail(self):
        """利润率趋势维度（费雪要点 6/7）：同比降幅超 2pp 容差剔除，缺失剔除。"""
        c = ScreenCriteria(min_roe=0, max_debt=0, margin_trend=True)
        # 改善或小幅波动（容差内）→ 通过
        assert _check_detail_criteria({"margin_trend_pp": 1.5}, c) == []
        assert _check_detail_criteria({"margin_trend_pp": -1.9}, c) == []
        # 降幅超容差 → 利润率恶化
        reasons = _check_detail_criteria({"margin_trend_pp": -5.0}, c)
        assert any("利润率恶化" in r for r in reasons)
        # 无同期可比数据 → 缺失剔除
        reasons = _check_detail_criteria({"margin_trend_pp": None}, c)
        assert any("缺失" in r for r in reasons)
        # 未启用维度不检查
        assert _check_detail_criteria({"margin_trend_pp": -20.0}, ScreenCriteria(min_roe=0, max_debt=0)) == []

    def test_no_dilution_pass_fail(self):
        """反稀释维度（费雪要点 13）：A 股用增发记录，港美股用股本增速。"""
        c = ScreenCriteria(min_roe=0, max_debt=0, no_dilution=True)
        # A 股：近 3 年无增发 → 通过；有增发 → 剔除
        assert _check_detail_criteria({"offerings_3y": 0}, c) == []
        reasons = _check_detail_criteria({"offerings_3y": 2}, c)
        assert any("股权融资稀释" in r for r in reasons)
        # 港美股：股本小幅变动/回购 → 通过；扩张超 5% → 剔除
        assert _check_detail_criteria({"offerings_3y": None, "share_growth": -1.5}, c) == []
        reasons = _check_detail_criteria({"offerings_3y": None, "share_growth": 12.0}, c)
        assert any("增发稀释" in r for r in reasons)
        # 两个指标都缺失 → 无法核查即不买
        reasons = _check_detail_criteria({"offerings_3y": None, "share_growth": None}, c)
        assert any("缺失" in r for r in reasons)
        # 未启用维度不检查
        assert _check_detail_criteria({"offerings_3y": 5}, ScreenCriteria(min_roe=0, max_debt=0)) == []

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

    def test_forecast_growth_pass_fail(self):
        """机构预测增速维度（纳维里尔指标 1 近似）：不达阈/无机构覆盖都剔除。"""
        c = ScreenCriteria(min_roe=0, max_debt=0, min_forecast_growth=15)
        assert _check_detail_criteria({"forecast_growth": 25.0}, c) == []
        reasons = _check_detail_criteria({"forecast_growth": 8.0}, c)
        assert any("预期面不足" in r for r in reasons)
        reasons = _check_detail_criteria({"forecast_growth": None}, c)
        assert any("无机构覆盖" in r for r in reasons)

    def test_earnings_surprise_pass_fail(self):
        """盈利惊喜维度（指标 2）：实际低于预期剔除；A 股无数据按缺失剔除。"""
        c = ScreenCriteria(min_roe=0, max_debt=0, earnings_surprise=True)
        assert _check_detail_criteria({"surprise_pct": 6.5}, c) == []
        assert _check_detail_criteria({"surprise_pct": 0.0}, c) == []  # 持平不算惊吓
        reasons = _check_detail_criteria({"surprise_pct": -4.0}, c)
        assert any("盈利惊吓" in r for r in reasons)
        reasons = _check_detail_criteria({"surprise_pct": None}, c)
        assert any("缺失" in r for r in reasons)
        # 未启用维度不检查
        assert _check_detail_criteria({"surprise_pct": -50.0}, ScreenCriteria(min_roe=0, max_debt=0)) == []

    def test_eps_momentum_pass_fail(self):
        """盈利动能维度（指标 7）：增速回落超 5pp 容差剔除，缺失剔除。"""
        c = ScreenCriteria(min_roe=0, max_debt=0, eps_momentum=True)
        # 加速或小幅回落（容差内）→ 通过
        assert _check_detail_criteria({"eps_momentum_pp": 12.0}, c) == []
        assert _check_detail_criteria({"eps_momentum_pp": -4.9}, c) == []
        # 回落超容差 → 明显减速
        reasons = _check_detail_criteria({"eps_momentum_pp": -18.0}, c)
        assert any("增长明显减速" in r for r in reasons)
        # 无相邻两期可比 → 缺失剔除
        reasons = _check_detail_criteria({"eps_momentum_pp": None}, c)
        assert any("缺失" in r for r in reasons)
        # 未启用维度不检查
        assert _check_detail_criteria({"eps_momentum_pp": -30.0}, ScreenCriteria(min_roe=0, max_debt=0)) == []


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

    def test_drawdown_fail(self):
        """52 周回撤（DHQ 折扣触发）：回撤未达阈或数据缺失都剔除。"""
        c = ScreenCriteria(max_pe=0, max_pb=0, min_roe=0, max_debt=0, min_cap=0, min_drawdown=20)
        assert _check_all_criteria({"drawdown": 30.0}, c) == []
        reasons = _check_all_criteria({"drawdown": 8.0}, c)
        assert any("尚未进入折扣区" in r for r in reasons)
        reasons = _check_all_criteria({"drawdown": None}, c)
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

    @patch("screener.engine.fetch_astock_rd_ratio")
    @patch("screener.engine.fetch_astock_detail")
    def test_rd_ratio_filter(self, mock_detail, mock_rd):
        """研发强度：启用时逐只拉利润表补齐并过滤，未启用时不打接口。"""
        mock_detail.return_value = {"roe": 20, "debt_ratio": 40, "profit_growth": 20}
        mock_rd.return_value = 5.5
        survivors = [
            {"code": "600000", "name": "测试", "pe": 10, "pb": 1,
             "total_mv": 100, "div_yield": 2, "close": 10.0},
        ]
        criteria = ScreenCriteria(min_roe=10, max_debt=70, min_rd_ratio=3)
        results = screen_astock_phase2(survivors, criteria)
        assert len(results) == 1
        assert results[0].metrics["rd_ratio"] == pytest.approx(5.5)
        # 研发强度不足 → 剔除
        mock_rd.return_value = 1.0
        assert screen_astock_phase2(survivors, criteria) == []
        # 未披露研发（None）→ 数据缺失剔除
        mock_rd.return_value = None
        assert screen_astock_phase2(survivors, criteria) == []
        # 未启用维度 → 不调研发接口
        mock_rd.reset_mock()
        criteria = ScreenCriteria(min_roe=10, max_debt=70)
        screen_astock_phase2(survivors, criteria)
        mock_rd.assert_not_called()

    @patch("screener.engine.fetch_astock_offering_count")
    @patch("screener.engine.fetch_astock_margin_profile")
    @patch("screener.engine.fetch_astock_detail")
    def test_fisher_dims_filter(self, mock_detail, mock_margin, mock_offer):
        """费雪维度接线：利润率趋势/增发记录启用时拉取并过滤，未启用不打接口。"""
        mock_detail.return_value = {"roe": 20, "debt_ratio": 40, "profit_growth": 20}
        mock_margin.return_value = {"gross_margin": 45.0, "margin_trend_pp": 1.2}
        mock_offer.return_value = 0
        survivors = [
            {"code": "600000", "name": "测试", "pe": 10, "pb": 1,
             "total_mv": 100, "div_yield": 2, "close": 10.0},
        ]
        criteria = ScreenCriteria(min_roe=10, max_debt=70, margin_trend=True, no_dilution=True)
        results = screen_astock_phase2(survivors, criteria)
        assert len(results) == 1
        assert results[0].metrics["margin_trend_pp"] == pytest.approx(1.2)
        assert results[0].metrics["offerings_3y"] == 0
        # 利润率恶化 → 剔除
        mock_margin.return_value = {"gross_margin": 45.0, "margin_trend_pp": -6.0}
        assert screen_astock_phase2(survivors, criteria) == []
        # 近 3 年有增发 → 剔除
        mock_margin.return_value = {"gross_margin": 45.0, "margin_trend_pp": 1.2}
        mock_offer.return_value = 1
        assert screen_astock_phase2(survivors, criteria) == []
        # 未启用维度 → 不打接口
        mock_margin.reset_mock()
        mock_offer.reset_mock()
        criteria = ScreenCriteria(min_roe=10, max_debt=70)
        screen_astock_phase2(survivors, criteria)
        mock_margin.assert_not_called()
        mock_offer.assert_not_called()

    @patch("screener.engine.fetch_astock_forecast_growth")
    @patch("screener.engine.fetch_astock_detail")
    def test_navellier_dims_filter(self, mock_detail, mock_forecast):
        """纳维里尔维度接线：预测增速批量表命中与相邻报告期动能差，未启用不打接口。"""
        mock_detail.return_value = {
            "roe": 20, "debt_ratio": 40,
            "profit_growth": 30, "profit_growth_prev": 22,
        }
        mock_forecast.return_value = 25.0
        survivors = [
            {"code": "600000", "name": "测试", "pe": 10, "pb": 1,
             "total_mv": 100, "div_yield": 2, "close": 10.0},
        ]
        criteria = ScreenCriteria(
            min_roe=10, max_debt=70, min_forecast_growth=15, eps_momentum=True,
        )
        results = screen_astock_phase2(survivors, criteria)
        assert len(results) == 1
        assert results[0].metrics["forecast_growth"] == pytest.approx(25.0)
        assert results[0].metrics["eps_momentum_pp"] == pytest.approx(8.0)  # 30 - 22
        # 预测增速不足 → 剔除
        mock_forecast.return_value = 5.0
        assert screen_astock_phase2(survivors, criteria) == []
        # 增速明显减速（回落超 5pp）→ 剔除
        mock_forecast.return_value = 25.0
        mock_detail.return_value = {
            "roe": 20, "debt_ratio": 40,
            "profit_growth": 10, "profit_growth_prev": 40,
        }
        assert screen_astock_phase2(survivors, criteria) == []
        # 上期增速缺失 → 动能按缺失剔除
        mock_detail.return_value = {
            "roe": 20, "debt_ratio": 40,
            "profit_growth": 30, "profit_growth_prev": None,
        }
        assert screen_astock_phase2(survivors, criteria) == []
        # 未启用维度 → 不调预测批量表
        mock_forecast.reset_mock()
        criteria = ScreenCriteria(min_roe=10, max_debt=70)
        screen_astock_phase2(survivors, criteria)
        mock_forecast.assert_not_called()


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

    @patch("screener.engine.fetch_yfinance_rd_ratio")
    @patch("screener.engine.fetch_yfinance_metrics")
    def test_rd_ratio_filter(self, mock_yf, mock_rd):
        """研发强度：启用时额外拉利润表，未启用时不打接口。"""
        mock_yf.return_value = {
            "name": "Test", "close": 100, "pe": 20, "pb": 3,
            "roe": 25, "div_yield": 1, "debt_ratio": 40,
            "profit_growth": 20, "total_mv": 500,
        }
        mock_rd.return_value = 8.0
        criteria = ScreenCriteria(max_pe=30, max_pb=0, min_roe=15, max_debt=0, min_cap=0, min_rd_ratio=3)
        results = screen_yfinance(["MSFT.US"], criteria)
        assert len(results) == 1
        assert results[0].metrics["rd_ratio"] == pytest.approx(8.0)
        # 无研发科目 → 数据缺失剔除
        mock_rd.return_value = None
        assert screen_yfinance(["MSFT.US"], criteria) == []
        # 未启用维度 → 不调研发接口
        mock_rd.reset_mock()
        criteria = ScreenCriteria(max_pe=30, max_pb=0, min_roe=15, max_debt=0, min_cap=0)
        screen_yfinance(["MSFT.US"], criteria)
        mock_rd.assert_not_called()

    @patch("screener.engine.fetch_yfinance_fisher_extra")
    @patch("screener.engine.fetch_yfinance_metrics")
    def test_fisher_dims_filter(self, mock_yf, mock_extra):
        """费雪维度接线：启用时拉年度利润表一次取齐趋势与股本，未启用不打接口。"""
        mock_yf.return_value = {
            "name": "Test", "close": 100, "pe": 20, "pb": 3,
            "roe": 25, "div_yield": 1, "debt_ratio": 40,
            "profit_growth": 20, "total_mv": 500,
        }
        mock_extra.return_value = {"margin_trend_pp": 0.8, "share_growth": -1.0}
        criteria = ScreenCriteria(
            max_pe=30, max_pb=0, min_roe=15, max_debt=0, min_cap=0,
            margin_trend=True, no_dilution=True,
        )
        results = screen_yfinance(["MSFT.US"], criteria)
        assert len(results) == 1
        assert results[0].metrics["margin_trend_pp"] == pytest.approx(0.8)
        assert results[0].metrics["share_growth"] == pytest.approx(-1.0)
        # 股本扩张超 5% → 增发稀释剔除
        mock_extra.return_value = {"margin_trend_pp": 0.8, "share_growth": 9.0}
        assert screen_yfinance(["MSFT.US"], criteria) == []
        # 利润表不可用 → 数据缺失剔除
        mock_extra.return_value = None
        assert screen_yfinance(["MSFT.US"], criteria) == []
        # 未启用维度 → 不打接口
        mock_extra.reset_mock()
        criteria = ScreenCriteria(max_pe=30, max_pb=0, min_roe=15, max_debt=0, min_cap=0)
        screen_yfinance(["MSFT.US"], criteria)
        mock_extra.assert_not_called()

    @patch("screener.engine.fetch_yfinance_navellier")
    @patch("screener.engine.fetch_yfinance_metrics")
    def test_navellier_dims_filter(self, mock_yf, mock_nav):
        """纳维里尔维度接线（港美股）：启用时一次拉齐预期/惊喜/动能，未启用不打接口。"""
        mock_yf.return_value = {
            "name": "Test", "close": 100, "pe": 20, "pb": 3,
            "roe": 25, "div_yield": 1, "debt_ratio": 40,
            "profit_growth": 20, "total_mv": 500,
        }
        mock_nav.return_value = {
            "forecast_growth": 22.0, "surprise_pct": 5.0, "eps_momentum_pp": 3.0,
        }
        criteria = ScreenCriteria(
            max_pe=30, max_pb=0, min_roe=15, max_debt=0, min_cap=0,
            min_forecast_growth=15, earnings_surprise=True, eps_momentum=True,
        )
        results = screen_yfinance(["MSFT.US"], criteria)
        assert len(results) == 1
        assert results[0].metrics["forecast_growth"] == pytest.approx(22.0)
        assert results[0].metrics["surprise_pct"] == pytest.approx(5.0)
        assert results[0].metrics["eps_momentum_pp"] == pytest.approx(3.0)
        # 盈利惊吓（实际低于预期）→ 剔除
        mock_nav.return_value = {
            "forecast_growth": 22.0, "surprise_pct": -3.0, "eps_momentum_pp": 3.0,
        }
        assert screen_yfinance(["MSFT.US"], criteria) == []
        # 接口不可用 → 三项全缺失剔除
        mock_nav.return_value = None
        assert screen_yfinance(["MSFT.US"], criteria) == []
        # 未启用维度 → 不打接口
        mock_nav.reset_mock()
        criteria = ScreenCriteria(max_pe=30, max_pb=0, min_roe=15, max_debt=0, min_cap=0)
        screen_yfinance(["MSFT.US"], criteria)
        mock_nav.assert_not_called()


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
# 美股 universe：代码归一化 / Phase 1 过滤与降级 / run_screen 路由
# ---------------------------------------------------------------------------


class TestUsTickerNormalize:
    def test_normalize_variants(self):
        from screener.data import _us_ticker_to_symbol

        assert _us_ticker_to_symbol("AAPL") == "AAPL.US"
        assert _us_ticker_to_symbol("brk_b") == "BRK-B.US"   # 东财下划线口径
        assert _us_ticker_to_symbol("BRK.B") == "BRK-B.US"   # 维基点号口径
        assert _us_ticker_to_symbol("") is None
        assert _us_ticker_to_symbol("$$$") is None


def _fake_us_snapshot() -> pd.DataFrame:
    return pd.DataFrame({
        "code": ["CHEAP.US", "PRICY.US", "BIG.US", "NOPE.US"],
        "name": ["Cheap", "Pricy", "Big", "NoPe"],
        "close": [10.0, 50.0, 100.0, 5.0],
        "pe": [8.0, 40.0, 9.0, None],
        "pb": [None, None, None, None],  # 快照无 PB：全 NaN 时该维度交给 Phase 2
        "total_mv": [50.0, 60.0, 5000.0, 20.0],
        "div_yield": [None, None, None, None],
    })


class TestUsPhase1:
    @patch("screener.engine.fetch_us_snapshot")
    def test_snapshot_filter(self, mock_snap):
        """快照模式：PE/市值批量过滤；PB 全 NaN 时跳过不误杀；PE 缺失行被剔。"""
        mock_snap.return_value = _fake_us_snapshot()
        criteria = ScreenCriteria(max_pe=10, max_pb=3.0, min_cap=10.0, max_cap=1000.0)
        survivors, total = screen_us_phase1(criteria)
        assert total == 4
        codes = [r["code"] for r in survivors]
        assert codes == ["CHEAP.US"]  # PRICY 超 PE，BIG 超市值，NOPE 无 PE

    @patch("screener.engine.fetch_sp500_symbols")
    @patch("screener.engine.fetch_us_snapshot")
    def test_fallback_sp500(self, mock_snap, mock_sp500):
        """快照不可用：降级 S&P 500 名单，不做批量过滤全部交给 Phase 2。"""
        mock_snap.return_value = None
        mock_sp500.return_value = ["AAPL.US", "MSFT.US"]
        survivors, total = screen_us_phase1(ScreenCriteria(max_pe=10))
        assert total == 2
        assert [r["code"] for r in survivors] == ["AAPL.US", "MSFT.US"]

    @patch("screener.engine.fetch_sp500_symbols")
    @patch("screener.engine.fetch_us_snapshot")
    def test_both_sources_fail(self, mock_snap, mock_sp500):
        mock_snap.return_value = None
        mock_sp500.return_value = None
        assert screen_us_phase1(ScreenCriteria()) == ([], 0)


class TestRunScreenUsUniverse:
    @patch("screener.engine.screen_yfinance")
    @patch("screener.engine.screen_us_phase1")
    def test_universe_routing(self, mock_p1, mock_yf):
        """universe=us：Phase 1 存活代码逐只交给 yfinance 深度核查。"""
        mock_p1.return_value = ([{"code": "CHEAP.US"}], 13000)
        mock_yf.return_value = [
            ScreenResult("CHEAP.US", "Cheap", {"pe": 8, "total_mv": 50}, 80, True)
        ]
        result = run_screen(ScreenCriteria(max_pe=10), universe="us")
        assert result["n_scanned"] == 13000
        assert result["n_phase1"] == 1
        assert result["n_final"] == 1
        mock_yf.assert_called_once()
        assert mock_yf.call_args[0][0] == ["CHEAP.US"]

    @patch("screener.engine.fetch_benchmark_close")
    def test_universe_halts_when_spy_down(self, mock_bench):
        """大势前置检查：SPY 未确认上行时纪律性不筛，不跑逐只漏斗。"""
        mock_bench.return_value = pd.Series(range(400, 100, -1))  # 下行基准
        result = run_screen(ScreenCriteria(market_filter=True), universe="us")
        assert result["candidates"] == []
        assert result["n_scanned"] == 0
        assert result["market_regime"]["uptrend"] is False
        mock_bench.assert_called_once_with("SPY.US")


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
# DHQ：_filter_drawdown (mock)
# ---------------------------------------------------------------------------


class TestFilterDrawdown:
    def _make_results(self):
        return [
            ScreenResult("600001.SH", "深回撤", {"gross_margin": 55.0, "drawdown": None}, 50, True),
            ScreenResult("600002.SH", "浅回撤", {"gross_margin": 55.0, "drawdown": None}, 50, True),
            ScreenResult("600003.SH", "无数据", {"gross_margin": 55.0, "drawdown": None}, 50, True),
        ]

    @patch("screener.engine.fetch_drawdown_52w")
    def test_filter_keeps_dislocated(self, mock_dd):
        """只保留回撤达阈的标的；回撤写入 metrics 并重算评分。"""
        mock_dd.side_effect = [35.0, 8.0, None]
        criteria = ScreenCriteria(max_pe=0, max_pb=0, min_roe=0, max_debt=0, min_drawdown=20)
        kept = _filter_drawdown(self._make_results(), criteria)
        assert [r.symbol for r in kept] == ["600001.SH"]
        assert kept[0].metrics["drawdown"] == 35.0
        assert kept[0].score > 0

    @patch("screener.engine.fetch_drawdown_52w")
    def test_precomputed_drawdown_not_refetched(self, mock_dd):
        """已有 drawdown（如 yfinance 路径）不重复拉日 K。"""
        r = ScreenResult("AAPL.US", "Apple", {"gross_margin": 45.0, "drawdown": 25.0}, 50, True)
        criteria = ScreenCriteria(max_pe=0, max_pb=0, min_roe=0, max_debt=0, min_drawdown=20)
        kept = _filter_drawdown([r], criteria)
        assert len(kept) == 1
        mock_dd.assert_not_called()


# ---------------------------------------------------------------------------
# 猛兽股：market_regime / _check_tech_criteria / _filter_monster_tech (mock)
# ---------------------------------------------------------------------------


class TestMarketRegime:
    def _make_series(self, trend: str) -> pd.Series:
        if trend == "up":
            return pd.Series(range(100, 400))  # 单调上行：收盘 > MA50 > MA200
        return pd.Series(range(400, 100, -1))  # 单调下行

    def test_uptrend(self):
        regime = market_regime(self._make_series("up"))
        assert regime is not None
        assert regime["uptrend"] is True
        assert regime["close"] > regime["ma50"] > regime["ma200"]

    def test_downtrend(self):
        regime = market_regime(self._make_series("down"))
        assert regime is not None
        assert regime["uptrend"] is False

    def test_insufficient_data(self):
        assert market_regime(None) is None
        assert market_regime(pd.Series(range(100))) is None  # 不足 200 根


class TestCheckTechCriteria:
    def _profile(self, **overrides) -> dict:
        base = {
            "price_pos": 0.9, "close": 100.0, "ma50": 90.0, "ma200": 80.0,
            "trend_ok": True, "rs_excess": 12.0, "updown_vol_ratio": 1.5,
        }
        base.update(overrides)
        return base

    def _criteria(self) -> ScreenCriteria:
        return ScreenCriteria(
            max_pe=0, max_pb=0, min_roe=0, max_debt=0,
            min_price_pos=0.75, trend_filter=True, rs_filter=True,
            min_updown_vol=1.2, market_filter=True,
        )

    def test_all_pass(self):
        regime = {"uptrend": True, "close": 4000, "ma50": 3900, "ma200": 3800}
        assert _check_tech_criteria(self._profile(), self._criteria(), regime) == []

    def test_profile_missing(self):
        reasons = _check_tech_criteria(None, self._criteria(), None)
        assert any("技术面数据缺失" in r for r in reasons)

    def test_low_position_fail(self):
        regime = {"uptrend": True, "close": 1, "ma50": 1, "ma200": 1}
        reasons = _check_tech_criteria(self._profile(price_pos=0.3), self._criteria(), regime)
        assert any("买强不买弱" in r for r in reasons)

    def test_trend_fail(self):
        regime = {"uptrend": True, "close": 1, "ma50": 1, "ma200": 1}
        reasons = _check_tech_criteria(self._profile(trend_ok=False), self._criteria(), regime)
        assert any("趋势结构" in r for r in reasons)

    def test_rs_weak_fail(self):
        regime = {"uptrend": True, "close": 1, "ma50": 1, "ma200": 1}
        reasons = _check_tech_criteria(self._profile(rs_excess=-5.0), self._criteria(), regime)
        assert any("RS 线弱势" in r for r in reasons)
        # RS 数据缺失也剔除（无法核查即不买）
        reasons = _check_tech_criteria(self._profile(rs_excess=None), self._criteria(), regime)
        assert any("RS 相对强度数据缺失" in r for r in reasons)

    def test_updown_vol_fail(self):
        regime = {"uptrend": True, "close": 1, "ma50": 1, "ma200": 1}
        reasons = _check_tech_criteria(self._profile(updown_vol_ratio=0.8), self._criteria(), regime)
        assert any("派发重于吸筹" in r for r in reasons)

    def test_market_downtrend_fail(self):
        regime = {"uptrend": False, "close": 3000, "ma50": 3100, "ma200": 3200}
        reasons = _check_tech_criteria(self._profile(), self._criteria(), regime)
        assert any("大势未确认上行" in r for r in reasons)
        # 大势数据缺失同样剔除
        reasons = _check_tech_criteria(self._profile(), self._criteria(), None)
        assert any("大势数据缺失" in r for r in reasons)

    def test_disabled_dims_skip(self):
        """未启用的维度不检查（即使指标很差）。"""
        c = ScreenCriteria(max_pe=0, max_pb=0, min_roe=0, max_debt=0)
        profile = self._profile(price_pos=0.1, trend_ok=False, rs_excess=-20, updown_vol_ratio=0.5)
        assert _check_tech_criteria(profile, c, None) == []


class TestFilterMonsterTech:
    def _make_results(self):
        return [
            ScreenResult("600001.SH", "强势", {"roe": 20.0}, 50, True),
            ScreenResult("600002.SH", "弱势", {"roe": 20.0}, 50, True),
            ScreenResult("600003.SH", "无数据", {"roe": 20.0}, 50, True),
        ]

    def _criteria(self) -> ScreenCriteria:
        return ScreenCriteria(
            max_pe=0, max_pb=0, min_roe=0, max_debt=0,
            min_price_pos=0.75, trend_filter=True, rs_filter=True, min_updown_vol=1.2,
        )

    @patch("screener.engine.fetch_benchmark_close")
    @patch("screener.engine.fetch_technical_profile")
    def test_keeps_strong_drops_weak(self, mock_profile, mock_bench):
        mock_bench.return_value = pd.Series(range(100, 400))
        strong = {
            "price_pos": 0.9, "close": 100.0, "ma50": 90.0, "ma200": 80.0,
            "trend_ok": True, "rs_excess": 15.0, "updown_vol_ratio": 1.6,
        }
        weak = {
            "price_pos": 0.4, "close": 50.0, "ma50": 55.0, "ma200": 60.0,
            "trend_ok": False, "rs_excess": -8.0, "updown_vol_ratio": 0.9,
        }
        mock_profile.side_effect = [strong, weak, None]
        kept = _filter_monster_tech(self._make_results(), self._criteria())
        assert [r.symbol for r in kept] == ["600001.SH"]
        # 技术指标写入 metrics 并重算评分
        assert kept[0].metrics["price_pos"] == 0.9
        assert kept[0].metrics["rs_excess"] == 15.0
        assert kept[0].metrics["updown_vol_ratio"] == 1.6
        assert kept[0].score > 0

    @patch("screener.engine.fetch_benchmark_close")
    @patch("screener.engine.fetch_technical_profile")
    def test_no_bench_fetch_when_not_needed(self, mock_profile, mock_bench):
        """未启用 RS/大势维度时不拉基准。"""
        c = ScreenCriteria(max_pe=0, max_pb=0, min_roe=0, max_debt=0, min_price_pos=0.75)
        mock_profile.return_value = {
            "price_pos": 0.9, "close": 100.0, "ma50": 90.0, "ma200": 80.0,
            "trend_ok": True, "rs_excess": None, "updown_vol_ratio": None,
        }
        kept = _filter_monster_tech(self._make_results(), c)
        assert len(kept) == 3
        mock_bench.assert_not_called()


class TestRunScreenMarketFilter:
    @patch("screener.engine.fetch_benchmark_close")
    def test_bulk_halts_when_market_down(self, mock_bench):
        """大势未确认上行：纪律性不筛，直接返回空结果 + market_regime。"""
        mock_bench.return_value = pd.Series(range(400, 100, -1))  # 下行基准
        criteria = ScreenCriteria(market_filter=True)
        result = run_screen(criteria, symbols=None)
        assert result["candidates"] == []
        assert result["n_scanned"] == 0
        assert result["market_regime"]["uptrend"] is False


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

    def test_hundredbagger_preset_applied(self):
        args = self._parse(["--preset", "hundredbagger"])
        p = PRESETS["hundredbagger"]
        assert args.min_roe == p["min_roe"]
        assert args.min_growth == p["min_growth"]
        assert args.min_rev_growth == p["min_rev_growth"]
        assert args.max_pb == 0.0            # 不卡 PB
        assert args.max_cap == p["max_cap"]
        # 未在预设中的项保持 CLI 默认：不启用左侧择时
        assert args.max_price_pos == 0.0
        assert args.smart_growth is False

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

    def test_monster_preset_applied(self):
        args = self._parse(["--preset", "monster"])
        p = PRESETS["monster"]
        assert args.max_pe == p["max_pe"]
        assert args.min_roe == p["min_roe"]
        assert args.min_growth == p["min_growth"]
        assert args.min_price_pos == p["min_price_pos"]
        assert args.trend_filter is True
        assert args.rs_filter is True
        assert args.min_updown_vol == p["min_updown_vol"]
        assert args.market_filter is True
        # 不启用左侧低位口径（与 min_price_pos 互斥）
        assert args.max_price_pos == 0.0

    def test_monster_explicit_override(self):
        """显式参数 > 预设：放宽 52 周位置下限到 0.6。"""
        args = self._parse(["--preset", "monster", "--min-price-pos", "0.6"])
        assert args.min_price_pos == 0.6
        assert args.rs_filter is True  # 未显式提供的项仍用预设

    def test_dhq_preset_applied(self):
        args = self._parse(["--preset", "dhq"])
        p = PRESETS["dhq"]
        assert args.max_pe == p["max_pe"]
        assert args.min_roe == 0.0           # 不卡 ROE（预设覆盖 CLI 默认的 10）
        assert args.min_rev_growth == p["min_rev_growth"]
        assert args.min_gross_margin == p["min_gross_margin"]
        assert args.min_cap == p["min_cap"]
        assert args.min_drawdown == p["min_drawdown"]
        # 未在预设中的项保持 CLI 默认：不启用位置/大势维度
        assert args.max_price_pos == 0.0
        assert args.market_filter is False

    def test_dhq_explicit_override(self):
        """显式参数 > 预设：收紧回撤阈值到 30%（书中加仓区上沿）。"""
        args = self._parse(["--preset", "dhq", "--min-drawdown", "30"])
        assert args.min_drawdown == 30.0
        assert args.min_gross_margin == PRESETS["dhq"]["min_gross_margin"]  # 未显式提供的项仍用预设

    def test_dividend_preset_applied(self):
        args = self._parse(["--preset", "dividend"])
        p = PRESETS["dividend"]
        assert args.max_pe == p["max_pe"]
        assert args.min_div == p["min_div"]
        assert args.min_div_years == p["min_div_years"]
        assert args.max_val_pct == p["max_val_pct"]
        assert args.max_debt == p["max_debt"]
        # 未在预设中的项保持 CLI 默认：不启用成长/位置/大势维度
        assert args.min_growth == 0.0
        assert args.max_price_pos == 0.0
        assert args.market_filter is False

    def test_dividend_explicit_override(self):
        """显式参数 > 预设：股息率抬高到 5%、连续分红放宽到 3 年。"""
        args = self._parse(["--preset", "dividend", "--min-div", "5", "--min-div-years", "3"])
        assert args.min_div == 5.0
        assert args.min_div_years == 3
        assert args.max_val_pct == PRESETS["dividend"]["max_val_pct"]  # 未显式提供的项仍用预设

    def test_fisher_preset_applied(self):
        args = self._parse(["--preset", "fisher"])
        p = PRESETS["fisher"]
        assert args.min_rd_ratio == p["min_rd_ratio"]
        assert args.min_gross_margin == p["min_gross_margin"]
        assert args.margin_trend is True
        assert args.no_dilution is True
        assert args.smart_growth is True
        assert args.max_pb == 0.0            # 不看 PB
        # 未在预设中的项保持 CLI 默认：不卡市值上限、不择时
        assert args.max_cap == 0.0
        assert args.max_price_pos == 0.0

    def test_fisher_explicit_override(self):
        """显式参数 > 预设：研发强度抬高到 8%（硬科技口径）。"""
        args = self._parse(["--preset", "fisher", "--min-rd-ratio", "8"])
        assert args.min_rd_ratio == 8.0
        assert args.margin_trend is True     # 未显式提供的项仍用预设

    def test_navellier_preset_applied(self):
        args = self._parse(["--preset", "navellier"])
        p = PRESETS["navellier"]
        assert args.min_roe == p["min_roe"]
        assert args.min_growth == p["min_growth"]
        assert args.min_rev_growth == p["min_rev_growth"]
        assert args.min_cash_yield == p["min_cash_yield"]
        assert args.margin_trend is True
        assert args.min_forecast_growth == p["min_forecast_growth"]
        assert args.eps_momentum is True
        assert args.max_pe == 0.0            # 不看估值
        assert args.max_pb == 0.0
        # 未在预设中的项保持 CLI 默认：盈利惊喜（仅港美股）不启用、不择时
        assert args.earnings_surprise is False
        assert args.max_price_pos == 0.0

    def test_navellier_explicit_override(self):
        """显式参数 > 预设：港美股筛选叠加盈利惊喜维度。"""
        args = self._parse(["--preset", "navellier", "--earnings-surprise"])
        assert args.earnings_surprise is True
        assert args.eps_momentum is True     # 未显式提供的项仍用预设


# ---------------------------------------------------------------------------
# 红利股：_filter_dividend_years / _filter_valuation_pct / 连续分红年数口径
# ---------------------------------------------------------------------------


class TestFilterDividendYears:
    def _make_results(self):
        return [
            ScreenResult("600001.SH", "纪律长", {"div_yield": 5.0}, 50, True),
            ScreenResult("600002.SH", "纪律短", {"div_yield": 4.0}, 50, True),
            ScreenResult("00700.HK", "无数据", {"div_yield": 3.5}, 50, True),
        ]

    @patch("screener.engine.fetch_dividend_years")
    def test_filter_keeps_long_streak(self, mock_years):
        """只保留连续分红达阈的标的；年数写入 metrics 并重算评分，缺失剔除。"""
        mock_years.side_effect = [8, 2, None]
        criteria = ScreenCriteria(max_pe=0, max_pb=0, min_roe=0, max_debt=0, min_div_years=5)
        kept = _filter_dividend_years(self._make_results(), criteria)
        assert [r.symbol for r in kept] == ["600001.SH"]
        assert kept[0].metrics["div_years"] == 8
        assert kept[0].score > 0


class TestFilterValuationPct:
    def _result(self, symbol, valuation):
        r = ScreenResult(symbol, symbol, {"pe": 10.0}, 50, True)
        r.valuation = valuation
        return r

    def test_low_kept_high_and_missing_dropped(self):
        """分位均值超阈与分位缺失都剔除（低分位是硬条件，无法核查即不买）。"""
        criteria = ScreenCriteria(max_val_pct=0.5)
        results = [
            self._result("600001.SH", {"pe_percentile": 0.2, "pb_percentile": 0.3}),
            self._result("600002.SH", {"pe_percentile": 0.8, "pb_percentile": 0.9}),
            self._result("600003.SH", None),
        ]
        kept = _filter_valuation_pct(results, criteria)
        assert [r.symbol for r in kept] == ["600001.SH"]

    def test_single_percentile_used_when_other_missing(self):
        """PE/PB 只有一个分位时用可用那个判定，不按缺失剔除。"""
        criteria = ScreenCriteria(max_val_pct=0.5)
        r = self._result("600001.SH", {"pe_percentile": None, "pb_percentile": 0.4})
        assert [x.symbol for x in _filter_valuation_pct([r], criteria)] == ["600001.SH"]


class TestDividendYearsStreak:
    """_fetch_dividend_years_remote 的连续年数口径（mock 分红序列，不依赖网络）。"""

    def _series(self, years):
        idx = pd.DatetimeIndex([pd.Timestamp(f"{y}-07-01") for y in years])
        return pd.Series([0.5] * len(idx), index=idx)

    @patch("data.dividends.fetch_dividends")
    def test_consecutive_years_counted(self, mock_div):
        from datetime import date

        from screener.data import _fetch_dividend_years_remote

        y = date.today().year
        mock_div.return_value = self._series(range(y - 6, y + 1))
        assert _fetch_dividend_years_remote("600001.SH")["div_years"] == 7

    @patch("data.dividends.fetch_dividends")
    def test_streak_breaks_on_gap(self, mock_div):
        from datetime import date

        from screener.data import _fetch_dividend_years_remote

        y = date.today().year
        mock_div.return_value = self._series([y, y - 1, y - 3, y - 4])
        assert _fetch_dividend_years_remote("600001.SH")["div_years"] == 2

    @patch("data.dividends.fetch_dividends")
    def test_lapsed_streak_returns_zero(self, mock_div):
        """最近分红早于去年 → 纪律已中断，返回 0（而非历史段长度）。"""
        from datetime import date

        from screener.data import _fetch_dividend_years_remote

        y = date.today().year
        mock_div.return_value = self._series([y - 3, y - 4])
        assert _fetch_dividend_years_remote("600001.SH")["div_years"] == 0

    @patch("data.dividends.fetch_dividends")
    def test_fetch_failure_returns_none(self, mock_div):
        """非 A 股/接口异常 → None（调用方按数据缺失剔除）。"""
        from screener.data import _fetch_dividend_years_remote

        mock_div.side_effect = RuntimeError("分红数据目前仅支持 A 股")
        assert _fetch_dividend_years_remote("AAPL.US") is None


# ---------------------------------------------------------------------------
# 费雪维度数据层：利润率画像 / 增发记录 / 港美股利润表解析（纯函数，不依赖网络）
# ---------------------------------------------------------------------------


class TestMarginProfileFromAbstract:
    def _abstract(self, values: dict) -> pd.DataFrame:
        return pd.DataFrame([{"指标": "毛利率", **values}])

    def test_latest_and_yoy_trend(self):
        """最新期毛利率 + 同期报告期（去年同 MMDD）同比变动。"""
        from screener.data import _margin_profile_from_abstract

        df = self._abstract({"20260331": 42.0, "20251231": 44.0, "20250331": 45.5})
        profile = _margin_profile_from_abstract(df)
        assert profile["gross_margin"] == pytest.approx(42.0)
        # 同比对照是去年同期 20250331（非上一期 20251231，避免季节性）
        assert profile["margin_trend_pp"] == pytest.approx(-3.5)

    def test_no_prior_period_trend_none(self):
        """无同期可比报告期 → 趋势为 None（最新值仍返回）。"""
        from screener.data import _margin_profile_from_abstract

        df = self._abstract({"20260331": 42.0, "20251231": 44.0})
        profile = _margin_profile_from_abstract(df)
        assert profile["gross_margin"] == pytest.approx(42.0)
        assert profile["margin_trend_pp"] is None

    def test_invalid_input_returns_none(self):
        from screener.data import _margin_profile_from_abstract

        assert _margin_profile_from_abstract(None) is None
        assert _margin_profile_from_abstract(pd.DataFrame()) is None
        # 无毛利率行
        df = pd.DataFrame([{"指标": "ROE", "20260331": 15.0}])
        assert _margin_profile_from_abstract(df) is None


class TestOfferingDatesFromTable:
    def test_counts_by_code(self):
        from screener.data import _offering_dates_from_table

        df = pd.DataFrame({
            "code": ["600001", "600001", "600002"],
            "date": ["20250601", "20200101", "20260101"],
        })
        dates = _offering_dates_from_table(df)
        assert dates["600001"] == ["20250601", "20200101"]
        assert dates["600002"] == ["20260101"]
        assert "600003" not in dates  # 从未增发的代码不在表中（计数为 0）


class TestFisherExtraFromIncomeStmt:
    def _stmt(self, data: dict) -> pd.DataFrame:
        """构造 yfinance 风格年度利润表（列为财年日期降序）。"""
        cols = pd.DatetimeIndex(["2025-12-31", "2024-12-31"])
        return pd.DataFrame(data, index=cols).T

    def test_margin_trend_and_share_growth(self):
        from screener.data import _fisher_extra_from_income_stmt

        stmt = self._stmt({
            "Gross Profit": [50.0, 40.0],
            "Total Revenue": [100.0, 100.0],
            "Basic Average Shares": [102.0, 100.0],
        })
        extra = _fisher_extra_from_income_stmt(stmt)
        assert extra["margin_trend_pp"] == pytest.approx(10.0)  # 50% - 40%
        assert extra["share_growth"] == pytest.approx(2.0)

    def test_diluted_fallback_when_basic_missing(self):
        """Basic 缺失时降级摊薄口径。"""
        from screener.data import _fisher_extra_from_income_stmt

        stmt = self._stmt({"Diluted Average Shares": [95.0, 100.0]})
        extra = _fisher_extra_from_income_stmt(stmt)
        assert extra["share_growth"] == pytest.approx(-5.0)  # 回购缩股
        assert extra["margin_trend_pp"] is None

    def test_invalid_input_returns_none(self):
        from screener.data import _fisher_extra_from_income_stmt

        assert _fisher_extra_from_income_stmt(None) is None
        assert _fisher_extra_from_income_stmt(pd.DataFrame()) is None
        # 只有一个财年 → 无同比基准
        one_year = pd.DataFrame(
            {"Gross Profit": [50.0]}, index=pd.DatetimeIndex(["2025-12-31"])
        ).T
        assert _fisher_extra_from_income_stmt(one_year) is None


# ---------------------------------------------------------------------------
# 纳维里尔维度数据层：盈利预测表 / 盈利惊喜 / 盈利动能（纯函数，不依赖网络）
# ---------------------------------------------------------------------------


class TestForecastTableFromRaw:
    def _raw(self, **overrides) -> pd.DataFrame:
        base = {
            "代码": ["600001", "600002", "600003"],
            "2026预测每股收益": [1.00, 2.00, -0.50],
            "2027预测每股收益": [1.25, 1.80, 0.30],
        }
        base.update(overrides)
        return pd.DataFrame(base)

    def test_growth_from_two_forecast_years(self):
        """取最近两个预测年度：增速 = (次年/首年 - 1) × 100；基数非正置 NaN。"""
        from screener.data import _forecast_table_from_raw

        df = _forecast_table_from_raw(self._raw())
        by_code = dict(zip(df["code"], df["forecast_growth"]))
        assert by_code["600001"] == pytest.approx(25.0)
        assert by_code["600002"] == pytest.approx(-10.0)  # 预期下行也如实返回
        assert pd.isna(by_code["600003"])                  # 首年预测亏损：无法计算增速

    def test_year_columns_sorted_not_positional(self):
        """预测年度列按年份排序而非列顺序（接口列序变更不影响口径）。"""
        from screener.data import _forecast_table_from_raw

        raw = pd.DataFrame({
            "代码": ["600001"],
            "2027预测每股收益": [1.50],  # 故意把远年度放前
            "2026预测每股收益": [1.00],
        })
        df = _forecast_table_from_raw(raw)
        assert df["forecast_growth"].iloc[0] == pytest.approx(50.0)

    def test_invalid_input_returns_none(self):
        from screener.data import _forecast_table_from_raw

        assert _forecast_table_from_raw(None) is None
        assert _forecast_table_from_raw(pd.DataFrame()) is None
        # 只有一个预测年度 → 无法计算增速
        raw = pd.DataFrame({"代码": ["600001"], "2026预测每股收益": [1.0]})
        assert _forecast_table_from_raw(raw) is None


class TestSurpriseFromHistory:
    def _hist(self, rows: dict) -> pd.DataFrame:
        return pd.DataFrame(rows, index=pd.DatetimeIndex(["2026-03-31", "2026-06-30"]))

    def test_latest_surprise_pct(self):
        """取最近一期实际与预期同时非空的记录自算惊喜幅度。"""
        from screener.data import _surprise_from_history

        hist = self._hist({"epsActual": [1.00, 1.10], "epsEstimate": [0.90, 1.00]})
        assert _surprise_from_history(hist) == pytest.approx(10.0)

    def test_negative_estimate_uses_abs_base(self):
        """预期为负时用绝对值作分母（亏损收窄也是正惊喜）。"""
        from screener.data import _surprise_from_history

        hist = self._hist({"epsActual": [1.00, -0.10], "epsEstimate": [0.90, -0.20]})
        assert _surprise_from_history(hist) == pytest.approx(50.0)

    def test_missing_latest_falls_back(self):
        """最近一期实际未公布（NaN）时回退上一期。"""
        from screener.data import _surprise_from_history

        hist = self._hist({"epsActual": [1.00, None], "epsEstimate": [0.80, 1.00]})
        assert _surprise_from_history(hist) == pytest.approx(25.0)

    def test_invalid_input_returns_none(self):
        from screener.data import _surprise_from_history

        assert _surprise_from_history(None) is None
        assert _surprise_from_history(pd.DataFrame()) is None
        # 缺列 / 预期全为 0（除零保护）
        hist = self._hist({"epsActual": [1.0, 1.0], "epsEstimate": [0.0, 0.0]})
        assert _surprise_from_history(hist) is None


class TestMomentumFromIncomeStmt:
    def _stmt(self, net_income: list[float]) -> pd.DataFrame:
        cols = pd.DatetimeIndex(["2025-12-31", "2024-12-31", "2023-12-31"][: len(net_income)])
        return pd.DataFrame({"Net Income": net_income}, index=cols).T

    def test_accelerating_positive(self):
        """增速加快 → 动能为正：130/100-1=30% vs 100/90-1≈11.1% → +18.9pp。"""
        from screener.data import _momentum_from_income_stmt

        assert _momentum_from_income_stmt(self._stmt([130.0, 100.0, 90.0])) == pytest.approx(
            18.89, abs=0.01
        )

    def test_decelerating_negative(self):
        """增速回落 → 动能为负。"""
        from screener.data import _momentum_from_income_stmt

        assert _momentum_from_income_stmt(self._stmt([105.0, 100.0, 50.0])) < 0

    def test_invalid_input_returns_none(self):
        from screener.data import _momentum_from_income_stmt

        assert _momentum_from_income_stmt(None) is None
        assert _momentum_from_income_stmt(pd.DataFrame()) is None
        # 不足 3 个财年 / 基数财年亏损 → 增速无意义
        assert _momentum_from_income_stmt(self._stmt([130.0, 100.0])) is None
        assert _momentum_from_income_stmt(self._stmt([130.0, -10.0, 90.0])) is None
