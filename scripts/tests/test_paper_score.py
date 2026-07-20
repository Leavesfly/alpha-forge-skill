"""模拟盘 score 模式（run_paper --mode score）回归测试。

用 monkeypatch 替换数据拉取与状态路径，验证「评分裁决 → 纸面执行」闭环：
是=建仓、否=空仓、观察=维持现仓、同日幂等、strategy 模式参数校验。
"""

from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
import pytest

import run_paper
from tests.helpers import make_ohlcv


def _uptrend_df(n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    steps = 0.0015 + rng.normal(0.0, 0.006, size=n)
    return make_ohlcv(100.0 * np.exp(np.cumsum(steps)))


def _downtrend_df(n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(3)
    steps = -0.004 + rng.normal(0.0, 0.004, size=n)
    return make_ohlcv(100.0 * np.exp(np.cumsum(steps)))


def _run(monkeypatch, tmp_path, df: pd.DataFrame, argv: list[str]):
    """打桩运行 run_paper.main()：数据本地化、状态文件写 tmp_path。"""
    monkeypatch.setattr(run_paper, "fetch_ohlcv", lambda symbol, **kw: df)
    monkeypatch.setattr(
        run_paper, "state_path", lambda sym, tag: tmp_path / f"paper_{sym}_{tag}.json"
    )
    monkeypatch.setattr(sys, "argv", ["run_paper.py"] + argv)
    run_paper.main()


BASE_ARGS = [
    "--symbol", "TEST.SH", "--mode", "score",
    # 无基准（打桩后同一 df 会被当基准返回，故显式禁用市场默认基准无意义；
    # 用不存在的期货后缀无法通过校验，这里让基准与标的同 df 即可——
    # 相对强度为 0 不影响本测试关注的建仓/离场行为）
    "--market", "generic", "--lot-size", "1", "--capital", "100000",
]


class TestScoreMode:
    def test_yes_builds_position(self, tmp_path, monkeypatch, capsys):
        """上行趋势 →「是」→ 满仓建仓，裁决写入状态文件。"""
        _run(monkeypatch, tmp_path, _uptrend_df(), BASE_ARGS)
        state = json.loads((tmp_path / "paper_TEST.SH_score.json").read_text())
        assert state["mode"] == "score"
        assert state["shares"] > 0
        assert state["verdicts"][-1]["verdict"] == "yes"
        assert state["trades"][-1]["verdict"] == "yes"

    def test_no_stays_flat(self, tmp_path, monkeypatch, capsys):
        """下行趋势 →「否」→ 保持空仓，不产生成交。"""
        _run(monkeypatch, tmp_path, _downtrend_df(), BASE_ARGS)
        state = json.loads((tmp_path / "paper_TEST.SH_score.json").read_text())
        assert state["shares"] == 0
        assert state["trades"] == []
        assert state["verdicts"][-1]["verdict"] == "no"

    def test_reduce_risk_exits_position(self, tmp_path, monkeypatch, capsys):
        """先建仓（是），再遇下行（持仓需减风险）→ 清仓离场。"""
        up = _uptrend_df()
        _run(monkeypatch, tmp_path, up, BASE_ARGS)
        # 换成下行行情（末日期后移一天，绕过同日幂等）
        down = _downtrend_df()
        down["trade_date"] = pd.date_range("2021-01-01", periods=len(down), freq="B")
        _run(monkeypatch, tmp_path, down, BASE_ARGS)
        state = json.loads((tmp_path / "paper_TEST.SH_score.json").read_text())
        assert state["shares"] == 0
        assert state["verdicts"][-1]["verdict"] == "reduce_risk"
        assert state["trades"][-1]["action"] == "卖出"

    def test_same_day_idempotent(self, tmp_path, monkeypatch, capsys):
        """同一交易日重复运行幂等：不重复成交、不重复记录裁决。"""
        df = _uptrend_df()
        _run(monkeypatch, tmp_path, df, BASE_ARGS)
        state1 = json.loads((tmp_path / "paper_TEST.SH_score.json").read_text())
        _run(monkeypatch, tmp_path, df, BASE_ARGS)
        state2 = json.loads((tmp_path / "paper_TEST.SH_score.json").read_text())
        assert state1["trades"] == state2["trades"]
        assert state1["verdicts"] == state2["verdicts"]

    def test_strategy_mode_requires_strategy(self, tmp_path, monkeypatch, capsys):
        """strategy 模式缺 --strategy 应报参数错误并提示 --mode score。"""
        monkeypatch.setattr(sys, "argv", ["run_paper.py", "--symbol", "TEST.SH"])
        with pytest.raises(SystemExit):
            run_paper.main()


class TestSummary:
    """组合级聚合（--summary）。"""

    def _write_state(self, tmp_path, symbol, strategy, cash, shares, capital=100000.0):
        state = {
            "symbol": symbol, "strategy": strategy, "mode": "score",
            "initial_capital": capital, "cash": cash, "shares": shares,
            "start_date": "2021-01-01", "last_date": "2021-06-01",
            "trades": [{"date": "2021-01-05", "action": "买入",
                        "shares": shares or 100, "price": 10.0, "cost": 5.0}],
            "verdicts": [],
        }
        p = tmp_path / f"paper_{symbol}_{strategy}.json"
        p.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def _run_summary(self, monkeypatch, tmp_path, capsys, price=10.0):
        df = make_ohlcv(np.full(5, price))
        monkeypatch.setattr(run_paper, "outputs_dir", lambda: tmp_path)
        monkeypatch.setattr(run_paper, "fetch_ohlcv", lambda symbol, **kw: df)
        monkeypatch.setattr(sys, "argv", ["run_paper.py", "--summary", "--json"])
        run_paper.main()
        return json.loads(capsys.readouterr().out)

    def test_aggregates_multiple_papers(self, tmp_path, monkeypatch, capsys):
        """多个模拟盘聚合：总净值/权重/计数正确。"""
        self._write_state(tmp_path, "AAA.SH", "score", cash=50_000.0, shares=5_000)
        self._write_state(tmp_path, "BBB.SH", "ma_cross", cash=100_000.0, shares=0)
        payload = self._run_summary(monkeypatch, tmp_path, capsys, price=10.0)
        totals = payload["totals"]
        assert totals["count"] == 2
        assert totals["equity"] == pytest.approx(200_000.0)
        assert totals["nav"] == pytest.approx(1.0)
        assert payload["symbol_weights"]["AAA.SH"] == pytest.approx(0.25)

    def test_concentration_warning(self, tmp_path, monkeypatch, capsys):
        """单标的市值占比超 40% 时输出集中度告警。"""
        self._write_state(tmp_path, "AAA.SH", "score", cash=0.0, shares=10_000)
        payload = self._run_summary(monkeypatch, tmp_path, capsys, price=10.0)
        assert any("集中度" in w for w in payload["risk_warnings"])

    def test_drawdown_warning(self, tmp_path, monkeypatch, capsys):
        """净值 < 0.9 的模拟盘触发回撤告警。"""
        self._write_state(tmp_path, "AAA.SH", "score", cash=80_000.0, shares=0)
        payload = self._run_summary(monkeypatch, tmp_path, capsys, price=10.0)
        assert any("回撤" in w for w in payload["risk_warnings"])

    def test_summary_without_states_errors(self, tmp_path, monkeypatch, capsys):
        """无任何状态文件时报可操作错误。"""
        monkeypatch.setattr(run_paper, "outputs_dir", lambda: tmp_path)
        monkeypatch.setattr(sys, "argv", ["run_paper.py", "--summary"])
        with pytest.raises(SystemExit):
            run_paper.main()

    def test_equity_history_recorded(self, tmp_path, monkeypatch, capsys):
        """每次执行后状态文件追加 equity_history。"""
        _run(monkeypatch, tmp_path, _uptrend_df(), BASE_ARGS)
        state = json.loads((tmp_path / "paper_TEST.SH_score.json").read_text())
        assert len(state["equity_history"]) == 1
        assert state["equity_history"][0]["equity"] > 0
