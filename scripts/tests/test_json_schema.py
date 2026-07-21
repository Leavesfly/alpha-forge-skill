"""JSON 输出 Schema 稳定性测试。

保护 Agent 消费契约：--json 输出的字段名、类型、元信息结构不得意外变更。
字段只增不改不删（SCHEMA_VERSION 注释约定），本测试锁定当前已知字段。
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from backtest.engine import run_backtest
from report.serialize import (
    SCHEMA_VERSION,
    _to_native,
    attach_meta,
    frame_records,
    result_to_dict,
    to_json,
)
from strategies.base import Strategy
from tests.helpers import make_ohlcv


class ConstLongStrategy(Strategy):
    """始终满仓多头，用于隔离引擎行为。"""

    name = "const_long"

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(1.0, index=df.index)


# ─── SCHEMA_VERSION 稳定性 ─────────────────────────────────────────────────────


def test_schema_version_format():
    """SCHEMA_VERSION 应为 'alpha-forge/N' 格式。"""
    assert SCHEMA_VERSION.startswith("alpha-forge/")
    version_num = SCHEMA_VERSION.split("/")[1]
    assert version_num.isdigit()


def test_schema_version_is_stable():
    """锁定当前版本号，升级时需显式修改此测试（提醒破坏性变更）。"""
    assert SCHEMA_VERSION == "alpha-forge/1"


# ─── attach_meta 元信息契约 ────────────────────────────────────────────────────


def test_attach_meta_required_keys():
    """attach_meta 必须包含 schema/command/generated_at 三个元信息键。"""
    payload = {"foo": "bar", "value": 42}
    result = attach_meta(payload, command="test_cmd")

    # 元信息键必须存在
    assert "schema" in result
    assert "command" in result
    assert "generated_at" in result

    # 元信息值正确
    assert result["schema"] == SCHEMA_VERSION
    assert result["command"] == "test_cmd"

    # 原始 payload 字段保留
    assert result["foo"] == "bar"
    assert result["value"] == 42


def test_attach_meta_generated_at_is_iso8601():
    """generated_at 应为 ISO-8601 格式字符串。"""
    result = attach_meta({}, command="test")
    generated_at = result["generated_at"]

    # 应能被 datetime.fromisoformat 解析
    from datetime import datetime

    parsed = datetime.fromisoformat(generated_at)
    assert parsed is not None


def test_attach_meta_does_not_mutate_payload():
    """attach_meta 不应修改原始 payload。"""
    payload = {"key": "value"}
    original_keys = set(payload.keys())
    attach_meta(payload, command="test")
    assert set(payload.keys()) == original_keys


# ─── result_to_dict 字段契约 ───────────────────────────────────────────────────


@pytest.fixture
def sample_result():
    """构造一个最小回测结果。"""
    close = np.array([100, 101, 102, 103, 104, 105], dtype=float)
    df = make_ohlcv(close)
    return run_backtest(df, ConstLongStrategy(), symbol="TEST.SH", period="1d")


def test_result_to_dict_required_fields(sample_result):
    """result_to_dict 必须包含 Agent 消费的核心字段。"""
    d = result_to_dict(sample_result, strategy_name="测试策略")

    # 顶层必需字段（Agent 路由/展示依赖）
    required_fields = [
        "symbol",
        "period",
        "strategy",
        "config",
        "range",
        "metrics",
        "benchmark_metrics",
        "equity_end",
        "benchmark_equity_end",
        "trades_count",
        "trades",
    ]
    for field in required_fields:
        assert field in d, f"缺失必需字段: {field}"


def test_result_to_dict_range_subfields(sample_result):
    """range 子字段必须包含 start/end/num_periods。"""
    d = result_to_dict(sample_result)
    range_obj = d["range"]

    assert "start" in range_obj
    assert "end" in range_obj
    assert "num_periods" in range_obj
    assert isinstance(range_obj["num_periods"], int)


def test_result_to_dict_metrics_fields(sample_result):
    """metrics 必须包含核心绩效指标键。"""
    d = result_to_dict(sample_result)
    metrics = d["metrics"]

    # 核心指标（SKILL.md 转述依赖）
    required_metrics = [
        "total_return",
        "annual_return",
        "annual_volatility",
        "sharpe",
        "sortino",
        "max_drawdown",
        "calmar",
        "num_trades",
        "win_rate",
        "num_periods",
    ]
    for key in required_metrics:
        assert key in metrics, f"metrics 缺失必需键: {key}"


def test_result_to_dict_trades_structure(sample_result):
    """trades 列表每项必须包含 time/action/price。"""
    d = result_to_dict(sample_result)

    for trade in d["trades"]:
        assert "time" in trade
        assert "action" in trade
        assert "price" in trade
        assert trade["action"] in ("BUY", "SELL")
        assert isinstance(trade["price"], float)


# ─── _to_native 类型转换 ───────────────────────────────────────────────────────


def test_to_native_numpy_types():
    """numpy 标量应转为 Python 原生类型。"""
    data = {
        "int": np.int64(42),
        "float": np.float64(3.14),
        "bool": np.bool_(True),
    }
    result = _to_native(data)

    assert type(result["int"]) is int
    assert type(result["float"]) is float
    assert type(result["bool"]) is bool


def test_to_native_pandas_timestamp():
    """pandas Timestamp 应转为 ISO-8601 字符串。"""
    ts = pd.Timestamp("2024-01-15 10:30:00")
    result = _to_native({"ts": ts})

    assert isinstance(result["ts"], str)
    assert "2024-01-15" in result["ts"]


def test_to_native_nested():
    """嵌套结构应递归转换。"""
    data = {
        "outer": {
            "inner": [np.int64(1), np.int64(2)],
        }
    }
    result = _to_native(data)

    assert all(type(x) is int for x in result["outer"]["inner"])


# ─── to_json 序列化 ────────────────────────────────────────────────────────────


def test_to_json_produces_valid_json():
    """to_json 输出应为合法 JSON。"""
    payload = {"key": "value", "num": 42, "nested": {"a": 1}}
    json_str = to_json(payload)

    parsed = json.loads(json_str)
    assert parsed["key"] == "value"
    assert parsed["num"] == 42


def test_to_json_handles_numpy():
    """to_json 应能序列化含 numpy 类型的 payload。"""
    payload = {"value": np.float64(3.14), "count": np.int64(10)}
    json_str = to_json(payload)

    parsed = json.loads(json_str)
    assert parsed["value"] == pytest.approx(3.14)
    assert parsed["count"] == 10


def test_to_json_utf8():
    """to_json 应保留中文（ensure_ascii=False）。"""
    payload = {"summary": "策略跑赢基准"}
    json_str = to_json(payload)

    assert "策略跑赢基准" in json_str
    assert "\\u" not in json_str  # 不应有 unicode 转义


# ─── frame_records ─────────────────────────────────────────────────────────────


def test_frame_records_structure():
    """frame_records 应返回记录列表，每行为字典。"""
    df = pd.DataFrame({"a": [1, 2], "b": [3.0, 4.0]})
    records = frame_records(df)

    assert len(records) == 2
    assert all(isinstance(r, dict) for r in records)
    assert records[0]["a"] == 1
    assert records[0]["b"] == 3.0


def test_frame_records_max_rows():
    """frame_records 应尊重 max_rows 限制。"""
    df = pd.DataFrame({"x": range(100)})
    records = frame_records(df, max_rows=10)

    assert len(records) == 10


def test_frame_records_native_types():
    """frame_records 输出应为原生类型（可 JSON 序列化）。"""
    df = pd.DataFrame({"int": [np.int64(1)], "float": [np.float64(2.5)]})
    records = frame_records(df)

    # 应能无异常序列化
    json_str = json.dumps(records)
    assert json_str is not None
