"""cli_common 与 JSON 输出约定的回归测试。"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from cli_common import (
    check_symbol,
    emit_json,
    examples_from_doc,
    parse_params,
    run_cli,
    split_symbols,
)
from naming import default_output
from report.serialize import SCHEMA_VERSION, attach_meta, frame_records


# ---------- 参数解析 ----------

def test_parse_params_space_and_comma():
    assert parse_params(["fast=10", "slow=30"]) == {"fast": 10, "slow": 30}
    assert parse_params(["fast=10,slow=30"]) == {"fast": 10, "slow": 30}
    assert parse_params(["thr=0.05", "name=abc"]) == {"thr": 0.05, "name": "abc"}
    assert parse_params(None) == {}


def test_parse_params_bad_format():
    with pytest.raises(SystemExit, match="key=value"):
        parse_params(["fast10"])


# ---------- 标的校验 ----------

def test_check_symbol_valid():
    for sym in ("600000.SH", "AAPL.US", "00700.HK", "cu2501.SHF"):
        assert check_symbol(sym) == sym


def test_check_symbol_invalid():
    for bad in ("600000", "600000.", ".SH", "600000 SH", ""):
        with pytest.raises(SystemExit, match="标的代码"):
            check_symbol(bad)


def test_split_symbols_min_count():
    assert split_symbols("600000.SH, 000001.SZ", min_count=2) == [
        "600000.SH", "000001.SZ",
    ]
    with pytest.raises(SystemExit, match="至少需要 2 个标的"):
        split_symbols("600000.SH", min_count=2)


# ---------- --help 示例段 ----------

def test_examples_from_doc():
    doc = "说明文字。\n\n示例：\n    uv run python run_x.py --symbol 600000.SH\n"
    out = examples_from_doc(doc)
    assert out is not None and out.startswith("示例")
    assert examples_from_doc("只有说明没有命令段") is None
    assert examples_from_doc(None) is None


# ---------- run_cli 退出码 ----------

def test_run_cli_expected_error_exit_1(capsys):
    def boom():
        raise RuntimeError("数据源失败")

    with pytest.raises(SystemExit) as exc_info:
        run_cli(boom)
    assert exc_info.value.code == 1
    assert "[error] 数据源失败" in capsys.readouterr().err


def test_run_cli_unexpected_error_exit_1(capsys):
    def boom():
        raise KeyError("oops")

    with pytest.raises(SystemExit) as exc_info:
        run_cli(boom)
    assert exc_info.value.code == 1
    assert "ALPHA_FORGE_DEBUG" in capsys.readouterr().err


def test_run_cli_debug_reraises(monkeypatch):
    monkeypatch.setenv("ALPHA_FORGE_DEBUG", "1")

    def boom():
        raise RuntimeError("看堆栈")

    with pytest.raises(RuntimeError):
        run_cli(boom)


def test_run_cli_success():
    run_cli(lambda: None)  # 不应抛出


# ---------- JSON 输出约定 ----------

def test_attach_meta_keys():
    payload = attach_meta({"symbol": "600000.SH"}, command="backtest")
    assert payload["schema"] == SCHEMA_VERSION
    assert payload["command"] == "backtest"
    assert "generated_at" in payload
    assert payload["symbol"] == "600000.SH"  # 业务字段保持顶层


def test_frame_records_native_types():
    df = pd.DataFrame({"a": pd.array([1, 2]), "b": [0.5, 1.5]})
    records = frame_records(df)
    text = json.dumps(records)  # 不应因 numpy 类型报错
    assert json.loads(text) == [{"a": 1, "b": 0.5}, {"a": 2, "b": 1.5}]


def test_emit_json_writes_file(tmp_path):
    dest = tmp_path / "out" / "r.json"
    emit_json(str(dest), {"x": 1}, log=lambda *a: None)
    assert json.loads(dest.read_text(encoding="utf-8")) == {"x": 1}


# ---------- 输出命名约定 ----------

def test_default_output_ext():
    assert default_output("backtest", "600000.SH", "ma_cross") == (
        "../outputs/backtest_600000SH_ma_cross.png"
    )
    assert default_output("report", "600000.SH", "macd", ext="html") == (
        "../outputs/report_600000SH_macd.html"
    )
