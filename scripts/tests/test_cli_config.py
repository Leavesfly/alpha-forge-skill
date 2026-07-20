"""--config TOML 配置文件注入测试。"""

from __future__ import annotations

import argparse

import pytest

from cli_config import parse_args_with_config


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--strategy", default="ma_cross")
    p.add_argument("--count", type=int, default=500)
    p.add_argument("--exec-price", default="close")
    p.add_argument("--allow-short", action="store_true")
    return p


def test_config_injects_defaults(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text(
        'symbol = "600000.SH"\ncount = 800\nexec-price = "open"\nallow-short = true\n',
        encoding="utf-8",
    )
    args = parse_args_with_config(_parser(), ["--config", str(cfg)])
    assert args.symbol == "600000.SH"
    assert args.count == 800
    assert args.exec_price == "open"
    assert args.allow_short is True


def test_cli_overrides_config(tmp_path):
    """显式命令行参数优先于配置文件。"""
    cfg = tmp_path / "c.toml"
    cfg.write_text('symbol = "600000.SH"\ncount = 800\n', encoding="utf-8")
    args = parse_args_with_config(
        _parser(), ["--config", str(cfg), "--count", "300", "--symbol", "AAPL.US"]
    )
    assert args.count == 300
    assert args.symbol == "AAPL.US"


def test_unknown_key_raises(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('symbol = "600000.SH"\nnot_a_param = 1\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="未知参数"):
        parse_args_with_config(_parser(), ["--config", str(cfg)])


def test_missing_config_file():
    with pytest.raises(SystemExit, match="不存在"):
        parse_args_with_config(_parser(), ["--config", "/no/such/file.toml"])


def test_required_still_enforced_without_config():
    """无配置文件时 required 参数仍必填。"""
    with pytest.raises(SystemExit):
        parse_args_with_config(_parser(), [])
