#!/usr/bin/env python3
"""能力清单 CLI：列出全部策略（含默认参数与寻优网格）、轮动、因子、ML 模型与定投模式。

新手用它了解「有哪些可用」，agent 用 --json 拿到结构化清单再编排调用；
--doctor 逐项自检环境（依赖/Key/缓存/字体/数据拉取）并给出修复建议。

示例：
    # 终端表格查看全部单标的策略与参数
    uv run python run_list.py

    # 环境自检（新手遇到问题先跑这条；全部 ✓ 即环境就绪）
    uv run python run_list.py --doctor

    # 结构化 JSON（供 agent/脚本消费）
    uv run python run_list.py --json > capabilities.json
"""

from __future__ import annotations

import argparse
import os
import sys

from cli_common import add_json_arg, emit_json, init_log, make_parser, run_cli
from cli_config import parse_args_with_config
from strategies import STRATEGIES


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 能力清单（策略/轮动/因子/模型/定投模式）与环境自检", __doc__)
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="环境自检：逐项检查 Python/依赖/API Key/缓存/中文字体/数据拉取，并给出修复建议",
    )
    add_json_arg(parser)
    return parser


def _strategy_entries() -> list[dict]:
    entries = []
    for name, cls in STRATEGIES.items():
        defaults = {k: v for k, v in cls.default_params().items() if k != "allow_short"}
        entries.append(
            {
                "name": name,
                "display_name": cls.display_name,
                "default_params": defaults,
                "param_grid": dict(cls.param_grid),
            }
        )
    return entries


def _doctor_checks() -> list[dict]:
    """逐项环境检查，返回 [{name, status(ok/warn/fail), detail, hint}]。"""
    checks: list[dict] = []

    def add(name: str, status: str, detail: str, hint: str = "") -> None:
        checks.append({"name": name, "status": status, "detail": detail, "hint": hint})

    # 1) Python 版本（pyproject 要求 >= 3.10）
    ver = sys.version_info
    if (ver.major, ver.minor) >= (3, 10):
        add("Python 版本", "ok", f"{ver.major}.{ver.minor}.{ver.micro}")
    else:
        add("Python 版本", "fail", f"{ver.major}.{ver.minor}（需 >= 3.10）",
            "用 uv 运行会自动选择兼容解释器：cd scripts && uv sync && uv run python ...")

    # 2) 核心依赖可导入
    missing = []
    for mod in ("pandas", "numpy", "matplotlib", "sklearn", "rich", "tickflow", "akshare"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    if missing:
        add("核心依赖", "fail", "缺失/损坏：" + ", ".join(missing),
            "cd scripts && uv sync（跑测试需 uv sync --group dev）")
    else:
        add("核心依赖", "ok", "pandas/numpy/matplotlib/sklearn/rich/tickflow/akshare 均可用")

    # 3) LightGBM（macOS 缺 libomp 时导入会失败，不致命）
    try:
        __import__("lightgbm")
        add("LightGBM", "ok", "可用（run_ml 默认模型）")
    except Exception as exc:
        add("LightGBM", "warn", f"导入失败：{type(exc).__name__}",
            "macOS 执行 brew install libomp；或 run_ml 改用 --model ridge/logistic")

    # 4) API Key（免费日 K 无需 Key，仅提醒影响范围）
    if os.environ.get("TICKFLOW_API_KEY"):
        add("TICKFLOW_API_KEY", "ok", "已配置（完整服务：实时/分钟 K/股票池/财务因子）")
    else:
        add("TICKFLOW_API_KEY", "warn", "未配置：历史日 K 回测/寻优/评分等主流程不受影响",
            "仅实时/分钟 K、--universe 股票池、财务因子需要；tickflow.org 申请后 export TICKFLOW_API_KEY=...")

    # 5) 缓存目录可写
    from pathlib import Path

    from data.cache import _project_cache_dir

    cache_dir = Path(os.environ.get("ALPHA_FORGE_CACHE_DIR") or _project_cache_dir())
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        probe = cache_dir / ".doctor_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("缓存目录", "ok", f"可写：{cache_dir}")
    except OSError as exc:
        add("缓存目录", "fail", f"不可写：{cache_dir}（{exc}）",
            "检查目录权限，或用 ALPHA_FORGE_CACHE_DIR 指向可写位置")

    # 6) 中文字体（缺失时图表中文显示为方框，不致命）
    try:
        from matplotlib import font_manager

        wanted = {"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Arial Unicode MS"}
        installed = {f.name for f in font_manager.fontManager.ttflist}
        hit = sorted(wanted & installed)
        if hit:
            add("中文字体", "ok", f"可用：{hit[0]}")
        else:
            add("中文字体", "warn", "未找到常用中文字体，--plot 图中文会显示为方框",
                "安装任一中文字体（PingFang/微软雅黑/Noto Sans CJK）后重跑")
    except Exception as exc:
        add("中文字体", "warn", f"检查跳过：{type(exc).__name__}", "")

    # 7) 数据拉取端到端（可能命中本地缓存；失败才能确认有问题）
    try:
        from datafeed import fetch_ohlcv

        df = fetch_ohlcv("600000.SH", "1d", 30)
        add("数据拉取", "ok", f"600000.SH 日 K {len(df)} 根（可能命中本地缓存）")
    except Exception as exc:
        add("数据拉取", "fail", f"600000.SH 拉取失败：{exc}",
            "检查网络；可设 ALPHA_FORGE_DATA_SOURCE=tickflow|akshare 单源排查，详见 references/faq.md")

    return checks


def _run_doctor(args, log) -> None:
    """执行自检并输出；存在 fail 项时以退出码 1 结束。"""
    checks = _doctor_checks()
    icons = {"ok": "✓", "warn": "!", "fail": "✗"}

    log("===== 环境自检（run_list.py --doctor）=====")
    for c in checks:
        log(f"  [{icons[c['status']]}] {c['name']}: {c['detail']}")
        if c["hint"] and c["status"] != "ok":
            log(f"      └ 修复：{c['hint']}")

    n_fail = sum(1 for c in checks if c["status"] == "fail")
    n_warn = sum(1 for c in checks if c["status"] == "warn")
    if n_fail:
        log(f"\n结论：{n_fail} 项失败、{n_warn} 项警告——请先按上方建议修复失败项。")
    elif n_warn:
        log(f"\n结论：环境可用（{n_warn} 项警告不影响免费日 K 主流程）。下一步：references/use-cases.md 的「两个 Hello」。")
    else:
        log("\n结论：环境完全就绪。下一步：references/use-cases.md 的「两个 Hello」。")

    if args.json is not None:
        from report import attach_meta

        payload = attach_meta(
            {
                "checks": checks,
                "summary": {
                    "ok": sum(1 for c in checks if c["status"] == "ok"),
                    "warn": n_warn,
                    "fail": n_fail,
                },
            },
            command="doctor",
        )
        emit_json(args.json, payload, log)

    if n_fail:
        sys.exit(1)


def main() -> None:
    args = parse_args_with_config(build_parser())
    json_stdout, log = init_log(args)

    if args.doctor:
        _run_doctor(args, log)
        return

    from dca.engine import MODES as DCA_MODES
    from factors import FACTORS
    from ml.model import MODELS as ML_MODELS
    from portfolio.rotation import ROTATIONS
    from scoring import VERDICT_CN

    strategies = _strategy_entries()

    log(f"===== 单标的策略（{len(strategies)} 个，run_backtest/run_optimize/run_compare/run_signal）=====")
    for ent in strategies:
        params = ", ".join(f"{k}={v}" for k, v in ent["default_params"].items())
        log(f"  {ent['name']:<12} {ent['display_name']:<14} 默认: {params}")
        grid = ", ".join(f"{k}∈{v}" for k, v in ent["param_grid"].items())
        log(f"  {'':<12} {'':<14} 网格: {grid}")

    log(f"\n===== 组合轮动/优化（run_portfolio --strategy）=====")
    log("  " + ", ".join(ROTATIONS))

    log(f"\n===== 选股因子（run_factor --factors）=====")
    for name, spec in FACTORS.items():
        log(f"  {name:<14} 类别: {spec.category}")

    log(f"\n===== 机器学习模型（run_ml --model）=====")
    log("  " + ", ".join(ML_MODELS))

    log(f"\n===== 定投模式（run_dca --mode）=====")
    log("  " + ", ".join(DCA_MODES))

    log(f"\n===== 纪律评分（run_score / run_scan）=====")
    log("  结论五态: " + ", ".join(f"{k}({v})" for k, v in VERDICT_CN.items()))
    log("  四层: alpha(ALPHA加权) -> veto(风险否决) -> confirm(技术确认) -> timing(入场时机)")
    log("  另有: --replay 回放验证 / --risk-file 事件风险降级 / --cost 持仓联动")

    from canslim import LETTERS_CN as CANSLIM_LETTERS

    log(f"\n===== CAN SLIM 检查清单（run_canslim）=====")
    log("  七项: " + ", ".join(f"{k}({v})" for k, v in CANSLIM_LETTERS.items()))
    log("  纪律: M 大势不满足直接否；C/A 基本面缺失封顶「观察」；多标的横截面 RS 百分位排名")

    from strategies.custom import INDICATOR_SPEC, OPERATORS

    log(f"\n===== 自定义规则策略 DSL（run_custom --rules <TOML>）=====")
    log("  指标白名单: " + ", ".join(sorted(INDICATOR_SPEC)))
    log("  运算符: " + ", ".join(OPERATORS))
    log("  逻辑组合: entry/exit 各自支持 and/or；示例见 examples/custom_rule.toml")

    log("\n各策略原理与参数详见 references/strategies.md；--help 附可复制示例。")

    if args.json is not None:
        from report import attach_meta

        payload = attach_meta(
            {
                "strategies": strategies,
                "rotations": list(ROTATIONS),
                "factors": {
                    name: {"category": spec.category} for name, spec in FACTORS.items()
                },
                "ml_models": list(ML_MODELS),
                "dca_modes": list(DCA_MODES),
                "scoring": {
                    "verdicts": dict(VERDICT_CN),
                    "layers": ["alpha", "veto", "confirm", "timing", "event_risk"],
                    "commands": ["run_score.py", "run_scan.py"],
                },
                "canslim": {
                    "letters": dict(CANSLIM_LETTERS),
                    "commands": ["run_canslim.py"],
                },
                "custom_dsl": {
                    "indicators": sorted(INDICATOR_SPEC),
                    "operators": list(OPERATORS),
                    "example": "examples/custom_rule.toml",
                    "commands": ["run_custom.py"],
                },
            },
            command="list",
        )
        emit_json(args.json, payload, log)


if __name__ == "__main__":
    run_cli(main)
