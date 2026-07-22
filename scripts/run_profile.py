#!/usr/bin/env python3
"""用户风险画像 CLI：登记 / 查看 / 重置个性化风控偏好（outputs/profile.json）。

画像是跨命令共享的风险偏好登记（仅登记，不做任何交易执行）：
- run_score.py 未显式传 --capital/--risk-pct 时自动读取画像的建议仓位参数；
- 画像含 max_drawdown 时，相关命令可据此给出个性化回撤告警。

三档预设（conservative 保守 / balanced 平衡 / aggressive 激进）会自动填充
建议的 risk_pct / max_drawdown / max_single_position；显式参数始终优先。

示例：
    # 登记为平衡型投资者，可用资金 20 万
    uv run python run_profile.py --set --risk-tolerance balanced --capital 200000

    # 自定义（激进 + 显式覆盖最大回撤）
    uv run python run_profile.py --set --risk-tolerance aggressive --max-drawdown 0.4

    # 查看画像
    uv run python run_profile.py --json

    # 重置（恢复命令默认行为）
    uv run python run_profile.py --reset
"""

from __future__ import annotations

import argparse

from cli_common import (
    add_json_arg,
    build_next_steps,
    emit_json,
    init_log,
    log_next_steps,
    make_parser,
    run_cli,
)
from cli_config import parse_args_with_config
from profile import (
    RISK_PRESETS,
    load_profile,
    profile_path,
    reset_profile,
    set_profile,
)
from report import attach_meta


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 用户风险画像（登记/查看/重置）", __doc__)
    parser.add_argument("--set", action="store_true", help="登记/更新画像（增量合并，未传字段保留原值）")
    parser.add_argument("--reset", action="store_true", help="重置画像（删除文件，恢复命令默认行为）")
    parser.add_argument(
        "--risk-tolerance",
        choices=list(RISK_PRESETS),
        default=None,
        help="风险偏好：conservative(保守)/balanced(平衡)/aggressive(激进)，自动填充建议风控参数",
    )
    parser.add_argument("--capital", type=float, default=None, help="可用资金（用于建议仓位），如 200000")
    parser.add_argument("--risk-pct", type=float, default=None, help="单笔交易风险预算占资金比例，如 0.01")
    parser.add_argument("--max-drawdown", type=float, default=None, help="可接受最大回撤，如 0.2 表示 20%%")
    parser.add_argument("--max-single-position", type=float, default=None, help="单标的最大仓位占比，如 0.3")
    parser.add_argument("--note", default=None, help="备注（可选）")
    add_json_arg(parser)
    return parser


def _print_profile(prof: dict | None, log) -> None:
    """终端展示画像。"""
    log()
    log("===== 用户风险画像 =====")
    if not prof:
        log("（未登记。用 --set 登记后，run_score 等命令的建议仓位将因人而异。）")
        log("  预设档位：" + "，".join(
            f"{k}({v['label']})" for k, v in RISK_PRESETS.items()
        ))
        return
    tol = prof.get("risk_tolerance")
    label = RISK_PRESETS.get(tol, {}).get("label", tol or "自定义")
    log(f"  风险偏好    : {label}")
    if prof.get("capital") is not None:
        log(f"  可用资金    : {prof['capital']:,.0f}")
    if prof.get("risk_pct") is not None:
        log(f"  单笔风险比例: {prof['risk_pct'] * 100:.2f}%（止损时约亏资金的这个比例）")
    if prof.get("max_drawdown") is not None:
        log(f"  可接受回撤  : {prof['max_drawdown'] * 100:.0f}%")
    if prof.get("max_single_position") is not None:
        log(f"  单标的上限  : {prof['max_single_position'] * 100:.0f}%")
    if prof.get("note"):
        log(f"  备注        : {prof['note']}")
    log(f"\n画像文件：{profile_path()}")


def main() -> None:
    args = parse_args_with_config(build_parser())
    json_stdout, log = init_log(args)

    if args.set and args.reset:
        raise SystemExit("[error] --set 与 --reset 不能同时使用。")

    action = "list"
    if args.reset:
        action = "reset"
        reset_profile()
        log("已重置用户风险画像（恢复命令默认行为）。")
    elif args.set:
        action = "set"
        if not any([
            args.risk_tolerance, args.capital is not None, args.risk_pct is not None,
            args.max_drawdown is not None, args.max_single_position is not None,
            args.note is not None,
        ]):
            raise SystemExit(
                "[error] 登记画像至少需要一个参数，"
                "如：run_profile.py --set --risk-tolerance balanced --capital 200000"
            )
        try:
            set_profile(
                risk_tolerance=args.risk_tolerance,
                capital=args.capital,
                risk_pct=args.risk_pct,
                max_drawdown=args.max_drawdown,
                max_single_position=args.max_single_position,
                note=args.note,
            )
        except ValueError as exc:
            raise SystemExit(f"[error] {exc}") from exc
        log("已登记用户风险画像。")

    prof = load_profile()
    _print_profile(prof, log)

    if prof:
        log_next_steps(
            log,
            "评分时建议仓位将按画像计算 run_score.py --symbol <代码>",
            "登记真实持仓联动 run_account.py --set --symbol <代码> --shares N --cost P",
        )

    if args.json is not None:
        tol = (prof or {}).get("risk_tolerance")
        label = RISK_PRESETS.get(tol, {}).get("label", tol) if tol else None
        summary = (
            f"用户风险画像：{label or '自定义'}"
            + (f"，可用资金 {prof['capital']:,.0f}" if prof and prof.get("capital") else "")
            + (f"，单笔风险 {prof['risk_pct'] * 100:.2f}%" if prof and prof.get("risk_pct") else "")
            + "。仅登记偏好，不做交易执行。"
            if prof
            else "尚未登记用户风险画像，命令将使用各自默认风控参数。"
        )
        payload = attach_meta(
            {
                "action": action,
                "profile": prof,
                "risk_tolerance_label": label,
                "presets": {k: v for k, v in RISK_PRESETS.items()},
                "profile_file": str(profile_path()),
                "summary": summary,
                "next_steps": build_next_steps(
                    {"action": "score", "reason": "用画像参数计算个性化建议仓位",
                     "command": "run_score.py --symbol <代码> --json"},
                    {"action": "account", "reason": "登记真实持仓与画像联动",
                     "command": "run_account.py --set --symbol <代码> --shares N --cost P --json"},
                ),
            },
            command="profile",
        )
        emit_json(args.json, payload, log)


if __name__ == "__main__":
    run_cli(main)
