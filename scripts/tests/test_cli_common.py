"""cli_common 与 JSON 输出约定的回归测试。"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from cli_common import (
    check_symbol,
    emit_json,
    eval_condition,
    examples_from_doc,
    log_next_steps,
    make_parser,
    parse_params,
    run_cli,
    split_symbols,
)
from cli_common import _suggest_for_choice_error
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


# ---------- 枚举参数拼错的近似建议 ----------

def test_suggest_for_choice_error_close_match():
    msg = ("argument --strategy: invalid choice: 'macdd' "
           "(choose from 'ma_cross', 'macd', 'rsi')")
    out = _suggest_for_choice_error(msg)
    assert "是否想写：macd" in out
    assert "run_list.py" in out


def test_suggest_for_choice_error_unquoted_choices():
    # Python 3.12+ 的 argparse 报错中 choices 不带引号
    msg = "argument --strategy: invalid choice: 'macdd' (choose from ma_cross, macd, rsi)"
    out = _suggest_for_choice_error(msg)
    assert "是否想写：macd" in out


def test_suggest_for_choice_error_passthrough():
    msg = "the following arguments are required: --symbol"
    assert _suggest_for_choice_error(msg) == msg


def test_make_parser_invalid_choice_hint(capsys):
    parser = make_parser("测试")
    parser.add_argument("--strategy", choices=["ma_cross", "macd", "rsi"])
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--strategy", "macdd"])
    assert exc_info.value.code == 2
    assert "是否想写：macd" in capsys.readouterr().err


# ---------- 「下一步」提示 ----------

def test_log_next_steps_format():
    lines: list[str] = []
    log_next_steps(lambda *a: lines.append(" ".join(map(str, a))), "动作A", "动作B")
    assert lines == ["\n下一步：动作A；动作B"]
    log_next_steps(lambda *a: lines.append("x"))  # 无内容时不输出
    assert len(lines) == 1


# ---------- 环境自检（run_list.py --doctor） ----------

def test_doctor_checks_structure(monkeypatch, tmp_path):
    import datafeed
    import run_list

    monkeypatch.setenv("ALPHA_FORGE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(
        datafeed, "fetch_ohlcv", lambda *a, **k: pd.DataFrame({"close": [1.0] * 30})
    )
    checks = run_list._doctor_checks()
    names = [c["name"] for c in checks]
    for expected in ("Python 版本", "核心依赖", "TICKFLOW_API_KEY", "缓存目录", "数据拉取"):
        assert expected in names
    assert all(c["status"] in ("ok", "warn", "fail") for c in checks)
    assert next(c for c in checks if c["name"] == "数据拉取")["status"] == "ok"
    assert next(c for c in checks if c["name"] == "缓存目录")["status"] == "ok"


def test_doctor_data_fetch_failure_has_hint(monkeypatch, tmp_path):
    import datafeed
    import run_list

    def boom(*a, **k):
        raise RuntimeError("网络不可用")

    monkeypatch.setenv("ALPHA_FORGE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(datafeed, "fetch_ohlcv", boom)
    checks = run_list._doctor_checks()
    fetch = next(c for c in checks if c["name"] == "数据拉取")
    assert fetch["status"] == "fail"
    assert fetch["hint"]  # 失败项必须附修复建议


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
    """default_output 返回绝对路径，文件名符合约定。"""
    path = default_output("backtest", "600000.SH", "ma_cross")
    assert path.endswith("backtest_600000SH_ma_cross.png")
    assert "outputs" in path

    path_html = default_output("report", "600000.SH", "macd", ext="html")
    assert path_html.endswith("report_600000SH_macd.html")


# ---------- next_steps condition 求值 ----------

def test_eval_condition_numeric_dotted_path():
    data = {"dsr": {"dsr": 0.75}, "metrics": {"sharpe": 0.8}, "benchmark_metrics": {"sharpe": 1.2}}
    assert eval_condition("dsr.dsr < 0.9", data) is True
    assert eval_condition("dsr.dsr >= 0.9", data) is False
    assert eval_condition("metrics.sharpe < benchmark_metrics.sharpe", data) is True
    assert eval_condition("metrics.sharpe > benchmark_metrics.sharpe", data) is False


def test_eval_condition_string_and_bool():
    data = {"verdict": "watch", "flag": True}
    assert eval_condition("verdict != no", data) is True
    assert eval_condition("verdict == yes", data) is False
    assert eval_condition("verdict == watch", data) is True
    assert eval_condition("flag == true", data) is True


def test_eval_condition_failure_returns_false():
    data = {"a": 1}
    assert eval_condition("nonexist.field > 1", data) is False  # 路径不存在
    assert eval_condition("bad syntax !!", data) is False  # 语法错
    assert eval_condition("", data) is False  # 空表达式
