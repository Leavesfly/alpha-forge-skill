"""多策略对比：净值叠加图与 HTML 对比报告测试。"""

from __future__ import annotations

import numpy as np
import pytest

from backtest.engine import run_backtest
from backtest.plot import plot_compare
from report import render_compare_report
from strategies import get_strategy
from tests.helpers import make_ohlcv


@pytest.fixture
def results():
    rng = np.random.default_rng(11)
    steps = rng.normal(loc=0.0005, scale=0.02, size=260)
    close = 100.0 * np.exp(np.cumsum(steps))
    df = make_ohlcv(close)
    out = {}
    for name in ("ma_cross", "macd"):
        strat = get_strategy(name)
        out[strat.display_name] = run_backtest(df, strat, symbol="TEST.SH")
    return out


def test_render_compare_report(results, tmp_path):
    """对比报告为自包含 HTML，含全部策略名与基准。"""
    out = tmp_path / "compare.html"
    path = render_compare_report(results, symbol="TEST.SH", output=str(out))
    html = out.read_text(encoding="utf-8")
    assert path == str(out.resolve())
    for name in results:
        assert name in html
    assert "基准" in html
    assert "data:image/png;base64," in html  # 图表内嵌，无外部依赖


def test_plot_compare(results, tmp_path):
    """净值叠加图正常落盘。"""
    out = tmp_path / "compare.png"
    path = plot_compare(results, symbol="TEST.SH", output=str(out))
    assert out.exists() and out.stat().st_size > 0
    assert path == str(out.resolve())
