"""HTML 报告回归测试：单策略/对比报告应产出含关键区块的合法 HTML。

使用合成数据驱动回测，渲染到 tmp_path，不落盘到 outputs/。
"""

from __future__ import annotations

import pytest

import pandas as pd

from backtest.engine import run_backtest
from report.html import drawdown_episodes, render_backtest_report, render_compare_report
from strategies import get_strategy


@pytest.fixture
def backtest_result(random_walk_df):
    return run_backtest(
        random_walk_df,
        get_strategy("ma_cross"),
        symbol="TEST.SH",
        period="1d",
    )


def test_backtest_report_contains_key_sections(backtest_result, tmp_path):
    out = tmp_path / "report.html"
    path = render_backtest_report(
        backtest_result,
        strategy_name="双均线",
        config={"复权": "前复权"},
        output=str(out),
    )
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert path == str(out)

    # 基本结构与标题
    assert html.startswith("<!DOCTYPE html>")
    assert "TEST.SH" in html
    # 净值图 + 滚动夏普/收益分布图均以 base64 内嵌
    assert html.count("data:image/png;base64,") >= 2
    assert "滚动风险与收益分布" in html
    # 指标表含说明列与新增指标行
    assert "<th>说明</th>" in html
    assert "欧米茄比率" in html
    assert "盈亏比" in html
    assert "最长回撤期" in html
    # 月度收益热力图：单元格带背景色渐变
    assert "月度收益 (%)" in html
    assert "background:rgb(" in html


def test_tearsheet_new_sections(backtest_result, tmp_path):
    """报告含年度收益图与最深回撤表。"""
    out = tmp_path / "tearsheet.html"
    render_backtest_report(backtest_result, strategy_name="双均线", output=str(out))
    html = out.read_text(encoding="utf-8")
    assert "年度收益对比" in html
    assert "最深回撤 Top 5" in html
    # 占位符应被替换
    assert "{yearly_chart}" not in html
    assert "{drawdown_table}" not in html


def test_drawdown_episodes_basic():
    """回撤事件提取：深度/谷底/恢复正确。"""
    eq = pd.Series([1.0, 0.9, 0.8, 0.85, 1.0, 0.95, 0.9, 1.0])
    eps = drawdown_episodes(eq, top_n=5)
    assert len(eps) >= 2
    # 最深回撤应为 -20%
    assert eps[0]["depth"] == pytest.approx(-0.2)
    assert eps[0]["recover"] is not None


def test_drawdown_episodes_unrecovered_tail():
    """末尾未恢复的回撤事件 recover=None。"""
    eq = pd.Series([1.0, 1.1, 0.9, 0.85])
    eps = drawdown_episodes(eq, top_n=5)
    assert len(eps) == 1
    assert eps[0]["recover"] is None
    assert eps[0]["depth"] == pytest.approx(0.85 / 1.1 - 1.0)


def test_backtest_report_old_metrics_dict_compatible(backtest_result, tmp_path):
    """旧结果缺新指标键时应显示 '-' 而非报错（向后兼容）。"""
    for key in ("omega", "max_dd_duration", "profit_factor"):
        backtest_result.metrics.pop(key, None)
        backtest_result.benchmark_metrics.pop(key, None)
    out = tmp_path / "old.html"
    render_backtest_report(backtest_result, strategy_name="双均线", output=str(out))
    html = out.read_text(encoding="utf-8")
    assert "欧米茄比率" in html  # 行仍在，值退化为 -


def test_compare_report_contains_monthly_tables(random_walk_df, tmp_path):
    results = {
        "双均线": run_backtest(random_walk_df, get_strategy("ma_cross"), symbol="T"),
        "RSI": run_backtest(random_walk_df, get_strategy("rsi"), symbol="T"),
    }
    out = tmp_path / "compare.html"
    render_compare_report(results, symbol="TEST.SH", output=str(out))
    html = out.read_text(encoding="utf-8")

    assert "各策略月度收益" in html
    # 每个策略各有一张月度小表（h3 标题）
    assert "<h3>双均线</h3>" in html
    assert "<h3>RSI</h3>" in html
    assert "background:rgb(" in html
