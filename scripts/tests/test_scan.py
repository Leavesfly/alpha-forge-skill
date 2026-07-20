"""全市场扫描漏斗（scoring/scan.py）回归测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from scoring.scan import scan_symbols
from tests.helpers import make_ohlcv


def _trend_df(daily: float, n: int = 400, seed: int = 7, vol_scale: float = 1.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    steps = daily + rng.normal(0.0, 0.004, size=n)
    close = 100.0 * np.exp(np.cumsum(steps))
    df = make_ohlcv(close)
    df["volume"] = df["volume"] * vol_scale
    return df


def _make_fetch(data: dict):
    def fetch(symbol: str) -> pd.DataFrame:
        if symbol not in data:
            raise RuntimeError(f"{symbol} 拉取失败")
        return data[symbol]

    return fetch


class TestScanFunnel:
    def test_candidates_and_rejected_split(self):
        """上行标的进达标候选，下行标的进否决列表并带原因。"""
        data = {
            "UP1.SH": _trend_df(0.004, seed=1),
            "UP2.SH": _trend_df(0.005, seed=2),
            "DOWN.SH": _trend_df(-0.004, seed=3),
        }
        result = scan_symbols(list(data), fetch=_make_fetch(data))
        got_candidates = {c["symbol"] for c in result["candidates"]}
        got_rejected = {r["symbol"] for r in result["rejected"]}
        assert "DOWN.SH" in got_rejected
        assert got_candidates <= {"UP1.SH", "UP2.SH"}
        assert all(r["reason"] for r in result["rejected"])
        # 候选按排名分降序
        scores = [c["alpha_score"] for c in result["candidates"]]
        assert scores == sorted(scores, reverse=True)

    def test_failed_symbol_skipped_not_fatal(self):
        """拉取失败的标的进 skipped，不中断扫描。"""
        data = {"UP1.SH": _trend_df(0.004, seed=1)}
        result = scan_symbols(["UP1.SH", "MISSING.SH"], fetch=_make_fetch(data))
        assert [s["symbol"] for s in result["skipped"]] == ["MISSING.SH"]
        assert result["candidates"] or result["rejected"]

    def test_liquidity_pool_filter(self):
        """--pool 流动性初筛：成交额低的标的被过滤并注明原因。"""
        data = {
            "BIG.SH": _trend_df(0.004, seed=1, vol_scale=100.0),
            "SMALL.SH": _trend_df(0.004, seed=2, vol_scale=0.01),
        }
        result = scan_symbols(list(data), fetch=_make_fetch(data), pool=1)
        assert [f["symbol"] for f in result["filtered"]] == ["SMALL.SH"]

    def test_min_score_threshold(self):
        """min_score 高到 100 时没有达标候选，全部落入 rejected。"""
        data = {"UP1.SH": _trend_df(0.004, seed=1), "UP2.SH": _trend_df(0.005, seed=2)}
        result = scan_symbols(list(data), fetch=_make_fetch(data), min_score=100.0)
        assert result["candidates"] == []
        assert len(result["rejected"]) == 2

    def test_json_fields_complete(self):
        """输出结构四区块齐全，摘要字段可 JSON 序列化。"""
        import json

        from report import to_json

        data = {"UP1.SH": _trend_df(0.004, seed=1), "DOWN.SH": _trend_df(-0.004, seed=3)}
        result = scan_symbols(list(data), fetch=_make_fetch(data))
        assert set(result) == {"candidates", "rejected", "filtered", "skipped"}
        payload = json.loads(to_json(result))
        for item in payload["candidates"] + payload["rejected"]:
            assert {"symbol", "verdict", "verdict_cn", "alpha_score", "asof"} <= set(item)

    def test_progress_callback(self):
        data = {"UP1.SH": _trend_df(0.004, seed=1)}
        seen = []
        scan_symbols(["UP1.SH"], fetch=_make_fetch(data), on_progress=lambda done, sym: seen.append((done, sym)))
        assert seen == [(1, "UP1.SH")]
