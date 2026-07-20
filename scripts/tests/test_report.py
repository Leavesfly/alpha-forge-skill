"""报告生成测试：JSON 序列化与 HTML 研究报告。"""

from __future__ import annotations

import json

import pandas as pd

from backtest.engine import run_backtest
from report.html import render_backtest_report
from report.serialize import result_to_dict, to_json
from strategies.base import Strategy

from tests.helpers import make_ohlcv


class StepStrategy(Strategy):
    def __init__(self, enter_at: int, **params):
        super().__init__(**params)
        self.enter_at = enter_at

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        import numpy as np
        sig = np.zeros(len(df))
        sig[self.enter_at:] = 1.0
        return pd.Series(sig, index=df.index)


def _sample_result():
    close = [100 + i * 0.5 for i in range(120)]
    df = make_ohlcv(close)
    return run_backtest(df, StepStrategy(enter_at=10), symbol="TST.SH")


def test_result_to_dict_is_json_serializable():
    result = _sample_result()
    payload = result_to_dict(result, strategy_name="测试策略", config={"复权": "qfq"})
    text = to_json(payload)
    # 能被标准 json 解析
    parsed = json.loads(text)
    assert parsed["symbol"] == "TST.SH"
    assert parsed["strategy"] == "测试策略"
    assert "metrics" in parsed and "sharpe" in parsed["metrics"]
    assert "benchmark_metrics" in parsed
    assert parsed["config"]["复权"] == "qfq"
    assert isinstance(parsed["metrics"]["sharpe"], (int, float))


def test_result_to_dict_no_numpy_types():
    """所有数值应为原生类型（json 可序列化，无 numpy）。"""
    result = _sample_result()
    payload = result_to_dict(result)
    # json.dumps 不用 default 也应成功（说明无 numpy 残留）
    json.dumps(payload, ensure_ascii=False)


def test_render_html_report(tmp_path):
    result = _sample_result()
    out = tmp_path / "report.html"
    path = render_backtest_report(
        result, strategy_name="测试策略",
        config={"成交价": "close"}, output=str(out),
    )
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html
    assert "TST.SH" in html
    assert "data:image/png;base64," in html  # 图表已内嵌
    assert "绩效指标" in html
    assert path == str(out.resolve())
