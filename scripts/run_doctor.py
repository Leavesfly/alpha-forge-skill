#!/usr/bin/env python3
"""数据源健康诊断 CLI：逐源 × 逐标的做一次**真实**拉取，报告谁能用、谁挂了。

与 ``run_list.py --doctor``（检查本机环境：依赖/字体/缓存目录）的分工：
本命令只管**外部数据源链路**——绕过本地缓存直连每个源，用耗时、行数、
末根 K 线日期与质量校验结论回答「现在到底哪几个源真的能取到数据」。

诚实提示：港美股表面上有 openbb / tickflow / yfinance 三个源，但 openbb 走的
就是 yfinance provider，两者同上游 Yahoo；Yahoo 限流时会**同时**失效，
独立上游数其实更少，报告会显式标注。

API Key 只从系统环境变量读取，输出一律掩码（仅前 6 位 + ***），
不写入任何文件。

示例：
    # 三市场代表标的全源体检（A股/港股/美股各一只）
    uv run python run_doctor.py

    # 结构化 JSON 输出（供 agent/脚本消费）
    uv run python run_doctor.py --json

    # 只体检指定标的与周期
    uv run python run_doctor.py --symbols 600000.SH --period 1d

    # 只体检指定源
    uv run python run_doctor.py --sources openbb,yfinance --symbols AAPL.US
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import time

import pandas as pd

from cli_common import (
    add_json_arg,
    build_next_steps,
    emit_json,
    init_log,
    make_parser,
    run_cli,
    split_symbols,
)
from cli_config import parse_args_with_config
from data.cache import find_date_column
from data.calendar import market_of
from data.quality import validate_ohlcv
from data.sources import (
    AkshareSource,
    BaostockSource,
    OpenBBSource,
    TickFlowSource,
    YFinanceSource,
)
from report import attach_meta, frame_table

#: 三市场代表标的（覆盖 A 股 / 港股 / 美股）
DEFAULT_SYMBOLS = "600000.SH,00700.HK,AAPL.US"

#: 源清单：名称 -> (类, 需要的环境变量 Key, 角色说明, 上游标识)
#: 上游标识相同者会同时失效（Yahoo 限流时 openbb 与 yfinance 一起挂）。
SOURCE_SPECS: dict[str, tuple[type, str | None, str, str]] = {
    "openbb": (OpenBBSource, None, "港美股主力（经 yfinance provider）", "yahoo"),
    "tickflow": (TickFlowSource, "TICKFLOW_API_KEY", "多市场全周期主源", "tickflow"),
    "baostock": (BaostockSource, None, "沪深日/周/月 K 二级兜底", "baostock"),
    "akshare": (AkshareSource, None, "A 股日/周/月 K（含北交所）", "eastmoney"),
    "yfinance": (YFinanceSource, None, "港美股兜底", "yahoo"),
}


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 数据源健康诊断：逐源真实拉取体检", __doc__)
    parser.add_argument(
        "--symbols", default=DEFAULT_SYMBOLS,
        help=f"体检标的，逗号分隔；默认三市场代表标的 {DEFAULT_SYMBOLS}",
    )
    parser.add_argument(
        "--period", default="1d", choices=["1d", "1w", "1M"],
        help="K 线周期，默认 1d",
    )
    parser.add_argument("--count", type=int, default=60, help="每次试探拉取的 K 线数量，默认 60")
    parser.add_argument("--adjust", default="forward", help="复权口径，默认前复权")
    parser.add_argument(
        "--sources", default=None,
        help=f"只体检指定源，逗号分隔（默认全部）：{','.join(SOURCE_SPECS)}",
    )
    add_json_arg(parser)
    return parser


def mask_key(value: str | None) -> str | None:
    """掩码 API Key：只保留前 6 位，其余以 *** 代替（绝不打印完整 Key）。"""
    if not value:
        return None
    return (value[:6] + "***") if len(value) > 6 else "***"


def _resolve_sources(spec: str | None) -> list[str]:
    """解析 --sources，未知源名给出明确错误。"""
    if not spec:
        return list(SOURCE_SPECS)
    names = [s.strip() for s in spec.split(",") if s.strip()]
    unknown = [n for n in names if n not in SOURCE_SPECS]
    if unknown:
        raise SystemExit(
            f"[error] 未知数据源：{', '.join(unknown)}。"
            f"可选：{', '.join(SOURCE_SPECS)}"
        )
    return names


def _quality_of(df: pd.DataFrame, symbol: str, period: str) -> dict:
    """取质量校验结论：优先复用源侧已挂在 attrs 的报告，缺失则现算一次。"""
    report = df.attrs.get("quality")
    if report is None:  # ALPHA_FORGE_NO_QUALITY_CHECK=1 时源侧跳过了校验
        report = validate_ohlcv(df, symbol, period)
    return {
        "passed": report.passed,
        "summary": report.summary(),
        "issues": [i.code for i in report.issues],
    }


def _last_bar(df: pd.DataFrame) -> str | None:
    col = find_date_column(df)
    if col is None or len(df) == 0:
        return None
    try:
        return pd.Timestamp(pd.to_datetime(df[col]).iloc[-1]).strftime("%Y-%m-%d")
    except (ValueError, TypeError, IndexError):
        return None


def probe(name: str, symbol: str, period: str, count: int, adjust: str) -> dict:
    """对单个「源 × 标的」做一次真实拉取，返回体检记录。

    直接实例化源类（而非走 datafeed），因此天然绕过本地缓存与降级链——
    体检要的就是「这个源此刻自己能不能取到数据」，而不是「链路整体能不能」。
    """
    cls, key_env, role, upstream = SOURCE_SPECS[name]
    key_value = os.environ.get(key_env) if key_env else None
    record: dict = {
        "source": name,
        "symbol": symbol,
        "role": role,
        "upstream": upstream,
        "needs_api_key": key_env,
        "api_key_configured": bool(key_value),
        "api_key_masked": mask_key(key_value),
        "supported": None,
        "status": "skip",
        "elapsed_sec": None,
        "rows": None,
        "last_bar_date": None,
        "quality": None,
        "error": None,
    }

    source = cls()
    record["supported"] = bool(source.supports(symbol, period))
    if not record["supported"]:
        record["error"] = f"{name} 不覆盖 {symbol} {period}（supports 判定为 False）"
        return record

    started = time.perf_counter()
    try:
        # baostock 等 SDK 会往 stdout 打 login/logout 横幅，重定向到 stderr
        # 保证 --json 的 stdout 纯净（与 datafeed._fetch_with_retry 约定一致）
        with contextlib.redirect_stdout(sys.stderr):
            df = source.fetch(symbol, period, count, adjust)
    except Exception as exc:
        record["status"] = "fail"
        record["elapsed_sec"] = round(time.perf_counter() - started, 2)
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record

    record["status"] = "ok"
    record["elapsed_sec"] = round(time.perf_counter() - started, 2)
    record["rows"] = int(len(df))
    record["last_bar_date"] = _last_bar(df)
    record["quality"] = _quality_of(df, symbol, period)
    return record


def summarize(records: list[dict]) -> dict:
    """按市场汇总可用源数与**独立上游**数（同上游只算一个）。"""
    markets: dict[str, dict] = {}
    for rec in records:
        market = market_of(rec["symbol"]) or "OTHER"
        bucket = markets.setdefault(
            market,
            {"symbols": [], "usable_sources": [], "upstreams": [], "failed_sources": []},
        )
        if rec["symbol"] not in bucket["symbols"]:
            bucket["symbols"].append(rec["symbol"])
        if rec["status"] == "ok":
            if rec["source"] not in bucket["usable_sources"]:
                bucket["usable_sources"].append(rec["source"])
            if rec["upstream"] not in bucket["upstreams"]:
                bucket["upstreams"].append(rec["upstream"])
        elif rec["status"] == "fail" and rec["source"] not in bucket["failed_sources"]:
            bucket["failed_sources"].append(rec["source"])
    for bucket in markets.values():
        bucket["usable_count"] = len(bucket["usable_sources"])
        bucket["independent_upstreams"] = len(bucket["upstreams"])
    return markets


def _upstream_notes(markets: dict) -> list[str]:
    """同上游提示：可用源数虚高时必须说清，否则给人假的冗余感。"""
    notes: list[str] = []
    for market, bucket in sorted(markets.items()):
        if bucket["usable_count"] > bucket["independent_upstreams"]:
            same = [s for s in bucket["usable_sources"] if s in ("openbb", "yfinance")]
            if len(same) > 1:
                notes.append(
                    f"{market}：{bucket['usable_count']} 个源可用，但 {' 与 '.join(same)} "
                    f"同上游 Yahoo，独立上游实为 {bucket['independent_upstreams']} 个"
                    "——Yahoo 限流时会一起失效。"
                )
    return notes


def _print_records(records: list[dict], log, stderr: bool = False) -> None:
    """按标的分组打印体检明细表。

    ``stderr=True``（--json 输 stdout 时）表格也走 stderr，否则会把
    rich 表格混进纯 JSON 的 stdout 里，让调用方无法解析。
    """
    icons = {"ok": "✓", "fail": "✗", "skip": "–"}
    for symbol in dict.fromkeys(r["symbol"] for r in records):
        rows = []
        for rec in (r for r in records if r["symbol"] == symbol):
            quality = rec["quality"]
            rows.append(
                {
                    "源": rec["source"],
                    "状态": icons[rec["status"]],
                    "耗时s": "" if rec["elapsed_sec"] is None else f"{rec['elapsed_sec']:.2f}",
                    "行数": "" if rec["rows"] is None else rec["rows"],
                    "末根K线": rec["last_bar_date"] or "",
                    "质量": "" if quality is None else ("通过" if quality["passed"] else "有问题"),
                    "Key": rec["api_key_masked"] or ("需要但未配置" if rec["needs_api_key"] else "无需"),
                }
            )
        log("")
        frame_table(pd.DataFrame(rows), title=f"{symbol} 数据源体检", stderr=stderr)
        for rec in (r for r in records if r["symbol"] == symbol and r["error"]):
            log(f"    [{rec['source']}] {rec['error']}")


def _print_summary(markets: dict, notes: list[str], log) -> None:
    log(f"\n{'═' * 60}")
    for market, bucket in sorted(markets.items()):
        usable = ", ".join(bucket["usable_sources"]) or "无"
        log(f"  {market}（{', '.join(bucket['symbols'])}）：可用源 {bucket['usable_count']} 个 → {usable}")
        if bucket["failed_sources"]:
            log(f"      失败：{', '.join(bucket['failed_sources'])}")
    for note in notes:
        log(f"  ⚠ {note}")
    log(f"{'═' * 60}")


def main() -> None:
    args = parse_args_with_config(build_parser())
    json_stdout, log = init_log(args)

    symbols = split_symbols(args.symbols, min_count=1, what="数据源体检")
    names = _resolve_sources(args.sources)

    records: list[dict] = []
    for symbol in symbols:
        for name in names:
            log(f"体检 {name} × {symbol} {args.period} ...")
            records.append(probe(name, symbol, args.period, args.count, args.adjust))

    _print_records(records, log, stderr=json_stdout)
    markets = summarize(records)
    notes = _upstream_notes(markets)
    _print_summary(markets, notes, log)

    ok = sum(1 for r in records if r["status"] == "ok")
    failed = sum(1 for r in records if r["status"] == "fail")
    skipped = sum(1 for r in records if r["status"] == "skip")

    if args.json is not None:
        payload = attach_meta(
            {
                "period": args.period,
                "count": args.count,
                "adjust": args.adjust,
                "results": records,
                "markets": markets,
                "upstream_notes": notes,
                "ok": ok,
                "failed": failed,
                "skipped": skipped,
                "summary": (
                    f"数据源体检（{args.period}，{len(symbols)} 只标的 × {len(names)} 个源）："
                    f"{ok} 项成功、{failed} 项失败、{skipped} 项因不覆盖跳过。"
                    + ("" if not notes else " " + " ".join(notes))
                ),
                "next_steps": build_next_steps(
                    {"action": "verify", "reason": "对可用的两个源做交叉验证确认数据一致",
                     "command": "run_verify.py --symbols 600000.SH --source-b akshare --json"},
                    {"action": "sync", "reason": "源可用后批量预热本地缓存，避免逐只联网",
                     "command": "run_sync.py --symbols 600000.SH --json"},
                    {"action": "diagnose_env", "condition": "failed > 0",
                     "reason": "存在失败源，进一步检查本机环境（依赖/网络/缓存目录）",
                     "command": "run_list.py --doctor --json"},
                ),
            },
            command="datasource_doctor",
        )
        emit_json(args.json, payload, log)

    if ok == 0:
        print(
            "\n[error] 没有任何数据源成功返回数据：请检查网络连通性后重试。",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    run_cli(main)
