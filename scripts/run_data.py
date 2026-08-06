#!/usr/bin/env python3
"""数据取用 CLI：把数据层能力（多源降级 + 本地缓存 + 质量校验）暴露为统一命令。

为什么不直接用 SDK：裸调 TickFlow SDK 会绕过整个数据层——没有多源自动降级
（主源挂了就断）、没有本地缓存（重复联网）、没有质量校验、没有交易日历新鲜度
判定、`ALPHA_FORGE_OFFLINE=1` 也不生效。走本命令则全部自动获得，并额外回传
「这份数据到底谁给的、是否命中缓存、质量是否通过」等审计信息。

支持的数据种类（--kind）：

| kind | 内容 | 说明 |
|---|---|---|
| `klines` | OHLCV K 线 | 默认；多源降级 + 缓存 + 质量校验 |
| `dividends` | 每股分红历史 | A 股 akshare / 港美股 openbb，缓存 7 天 |
| `valuation` | PE/PB 估值分位 | A 股精确、港美股近似，缓存 24 小时 |
| `fundamentals` | 财务指标 | 需 TICKFLOW_API_KEY 且账号有财务权限 |
| `macro` | 宏观快照（利率/CPI/PMI） | 无需标的，缓存 12 小时 |

示例：
    # 取日 K（终端看末 10 行 + 数据来源与质量结论）
    uv run python run_data.py --symbols 600000.SH

    # 结构化 JSON（Agent 消费；含 quality/actual_source/cache_hit/cache_meta）
    uv run python run_data.py --symbols 600000.SH --count 250 --json

    # 多标的 + 导出 CSV（不带路径则写 outputs/ 下的规范文件名）
    uv run python run_data.py --symbols 600000.SH,AAPL.US --csv

    # 绕过缓存强制直连数据源（排查缓存陈旧时用）
    uv run python run_data.py --symbols 600000.SH --no-cache

    # 非 K 线数据
    uv run python run_data.py --symbols 600000.SH --kind dividends --json
    uv run python run_data.py --symbols 600000.SH --kind valuation --years 5
    uv run python run_data.py --kind macro --json
"""

from __future__ import annotations

import argparse
from pathlib import Path

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
from data.cache import find_date_column, normalize_adjust, read_meta
from data.quality import validate_ohlcv
from errors import DataFetchError
from naming import default_output
from report import attach_meta, frame_records, frame_table

#: 可取的数据种类
KINDS = ("klines", "dividends", "valuation", "fundamentals", "macro")

#: 无需标的的种类
NO_SYMBOL_KINDS = ("macro",)

#: 可导出 CSV 的种类（标量型结果无表格语义）
CSV_KINDS = ("klines", "dividends", "fundamentals")


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 数据取用：多源降级 + 本地缓存 + 质量校验", __doc__)
    parser.add_argument(
        "--symbols", default=None,
        help="标的代码，逗号分隔（--kind macro 时可省略），如 600000.SH,AAPL.US",
    )
    parser.add_argument(
        "--kind", default="klines", choices=list(KINDS),
        help="数据种类，默认 klines",
    )
    parser.add_argument(
        "--period", default="1d",
        choices=["1m", "5m", "15m", "30m", "60m", "1d", "1w", "1M", "1Q", "1Y"],
        help="K 线周期，默认 1d（分钟级需 TICKFLOW_API_KEY）",
    )
    parser.add_argument("--count", type=int, default=250, help="K 线数量，默认 250")
    parser.add_argument("--adjust", default="forward", help="复权口径，默认前复权")
    parser.add_argument("--years", type=int, default=5, help="估值分位回看年数，默认 5")
    parser.add_argument(
        "--no-cache", action="store_true",
        help="绕过本地缓存直连数据源（排查缓存陈旧时用）",
    )
    parser.add_argument("--tail", type=int, default=10, help="终端展示的末尾行数，默认 10")
    parser.add_argument(
        "--csv", nargs="?", const="auto", default=None, metavar="PATH",
        help="导出 CSV：不带值写 outputs/ 规范文件名（可用 ALPHA_FORGE_OUTPUT_DIR 覆盖），"
             "带路径则写指定文件",
    )
    add_json_arg(parser)
    return parser


# ─── 各 kind 的取数实现 ─────────────────────────────────────────────────────────


def _kline_record(symbol: str, args: argparse.Namespace) -> tuple[dict, pd.DataFrame | None]:
    """取单只标的 K 线，返回 (结构化记录, DataFrame)。

    命中缓存的判据取三重保险，任一条不成立就不算命中：
    ① `df.attrs` 没有 actual_source（源侧拉取会挂上它，是“确实联网”的确证）；
    ② meta 的 fetched_at 本次未被刷新（attrs 因 pandas 不传播而丢失时的兜底）；
    ③ 本地确实存在这份缓存的 meta（`ALPHA_FORGE_NO_CACHE=1` 不落盘，
    此时两次 read_meta 均为 None，不能因“时间戳没变”就误报命中）。
    """
    from datafeed import fetch_ohlcv

    adjust = normalize_adjust(args.adjust)
    use_cache = not args.no_cache
    before = read_meta(symbol, args.period, adjust) if use_cache else None
    df = fetch_ohlcv(
        symbol,
        period=args.period,
        count=args.count,
        adjust=adjust,
        use_cache=use_cache,
    )
    meta = read_meta(symbol, args.period, adjust) if use_cache else None
    refetched = bool(df.attrs.get("actual_source")) or (
        (before or {}).get("fetched_at") != (meta or {}).get("fetched_at")
    )
    cache_hit = use_cache and meta is not None and not refetched
    # 源侧拉取会在 attrs 回传实际命中源；缓存命中则回读 meta
    actual_source = df.attrs.get("actual_source") or (meta or {}).get("actual_source")

    # 无论来自网络还是缓存，都对**实际返回的数据**现场校验一次：
    # Agent 关心的是手里这份数据干不干净，与它从哪来无关
    report = validate_ohlcv(df, symbol, args.period)
    date_col = find_date_column(df)
    dates = pd.to_datetime(df[date_col]) if date_col else None

    return {
        "symbol": symbol,
        "rows": int(len(df)),
        "first_date": dates.iloc[0].strftime("%Y-%m-%d") if dates is not None else None,
        "last_date": dates.iloc[-1].strftime("%Y-%m-%d") if dates is not None else None,
        "actual_source": actual_source,
        "cache_hit": cache_hit,
        "cache_meta": _cache_meta_view(meta),
        "quality": {
            "passed": report.passed,
            "summary": report.summary(),
            "issues": [
                {"level": i.level, "code": i.code, "count": i.count, "detail": i.detail}
                for i in report.issues
            ],
        },
        "records": frame_records(df, max_rows=max(args.count, 1)),
    }, df


def _cache_meta_view(meta: dict | None) -> dict | None:
    """meta 的对外视图（只暴露审计相关字段，不泄露内部格式细节）。"""
    if not meta:
        return None
    return {
        "actual_source": meta.get("actual_source"),
        "rows": meta.get("rows"),
        "last_bar_date": meta.get("last_bar_date"),
        "fetched_date": meta.get("fetched_date"),
    }


def _dividends_record(symbol: str) -> tuple[dict, pd.DataFrame | None]:
    from data.dividends import fetch_dividends

    series = fetch_dividends(symbol)
    df = pd.DataFrame(
        {
            "date": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in series.index],
            "dps": [float(v) for v in series.to_numpy()],
        }
    )
    return {
        "symbol": symbol,
        "rows": int(len(df)),
        "total_dps": round(float(series.sum()), 4),
        "records": frame_records(df, max_rows=200),
    }, df


def _valuation_record(symbol: str, years: int) -> tuple[dict, None]:
    from data.valuation import fetch_valuation_percentile

    vp = fetch_valuation_percentile(symbol, years)
    if vp is None:
        raise DataFetchError(
            f"{symbol} 估值分位数据不可用（A 股经 akshare、港美股经 openbb/yfinance）。"
            "可改用 --kind klines 取价格数据，或稍后重试。"
        )
    return {"symbol": symbol, "payload": vp.to_dict()}, None


def _fundamentals_record(symbols: list[str]) -> tuple[dict, pd.DataFrame | None]:
    from data.fundamentals import fetch_fundamentals

    df = fetch_fundamentals(symbols)
    if df is None or len(df) == 0:
        raise DataFetchError(
            "财务指标不可用：需配置 TICKFLOW_API_KEY 且账号具备财务数据权限。"
            "可先用 --kind valuation 取估值分位，或用 run_screener.py 的免费基本面漏斗。"
        )
    return {
        "symbols": symbols,
        "rows": int(len(df)),
        "columns": list(df.columns),
        "records": frame_records(df, max_rows=500),
    }, df


def _macro_record() -> tuple[dict, None]:
    from data.macro import fetch_macro_snapshot

    snap = fetch_macro_snapshot()
    payload = snap.to_dict()
    if snap.bond_yield_10y is None and snap.cpi_yoy is None and snap.pmi is None:
        raise DataFetchError(
            "宏观快照三项均不可用（利率/CPI/PMI 经 akshare）："
            + "；".join(snap.errors or ["未知原因"])
        )
    return {"payload": payload}, None


# ─── 终端展示 ───────────────────────────────────────────────────────────────────


def _print_kline(record: dict, df: pd.DataFrame, args, log, stderr: bool) -> None:
    log(f"\n{'─' * 60}")
    origin = "本地缓存" if record["cache_hit"] else "数据源直取"
    # 旧缓存（actual_source 字段上线前写入的）无来源记录，如实说明而非笼统写“未知”
    source = record["actual_source"] or (
        "未记录（旧缓存，可加 --no-cache 重拉以记录来源）"
        if record["cache_hit"] else "未知"
    )
    log(f"  {record['symbol']}  [{args.period}]  {record['rows']} 行  {origin}（源：{source}）")
    log(f"  区间：{record['first_date']} ~ {record['last_date']}")
    quality = record["quality"]
    log(f"  质量：{'通过' if quality['passed'] else '有问题'} —— {quality['summary']}")
    for issue in quality["issues"]:
        log(f"    - [{issue['level']}] {issue['detail']}")
    if record["cache_meta"] and record["cache_hit"]:
        cm = record["cache_meta"]
        log(f"  缓存：抓取于 {cm['fetched_date']}，共 {cm['rows']} 行，数据到 {cm['last_bar_date']}")
    if args.tail > 0:
        log("")
        frame_table(df.tail(args.tail), title=f"{record['symbol']} 末 {args.tail} 行", stderr=stderr)


def _print_table(record: dict, df: pd.DataFrame, title: str, args, log, stderr: bool) -> None:
    log(f"\n  {title}：{record['rows']} 行")
    if args.tail > 0:
        frame_table(df.tail(args.tail), title=title, stderr=stderr)


def _print_payload(payload: dict, title: str, log) -> None:
    log(f"\n{'─' * 60}")
    log(f"  {title}")
    for key, value in payload.items():
        if value is not None and not isinstance(value, (list, dict)):
            log(f"    {key}: {value}")
    for key in ("errors", "note"):
        if payload.get(key):
            log(f"    {key}: {payload[key]}")


# ─── CSV 导出 ───────────────────────────────────────────────────────────────────


def _export_csv(frames: dict[str, pd.DataFrame], args: argparse.Namespace, log) -> str | None:
    """导出 CSV；多标的合并为一张带 symbol 列的表。"""
    if not frames:
        return None
    if args.kind not in CSV_KINDS:
        log(f"[warn] --kind {args.kind} 是标量型结果，无表格可导出，已忽略 --csv")
        return None

    if len(frames) == 1:
        table = next(iter(frames.values())).copy()
    else:
        table = pd.concat(
            [df.assign(symbol=sym) for sym, df in frames.items()], ignore_index=True
        )

    if args.csv == "auto":
        path = Path(default_output(f"data_{args.kind}", *sorted(frames)[:3], ext="csv"))
    else:
        path = Path(args.csv).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(path, index=False, encoding="utf-8-sig")
    log(f"\nCSV 已保存：{path}（{len(table)} 行）")
    return str(path)


# ─── 主流程 ─────────────────────────────────────────────────────────────────────


def _collect(args: argparse.Namespace, log, stderr: bool) -> tuple[list[dict], list[dict], dict]:
    """按 kind 逐项取数，返回 (成功记录, 失败记录, 可导出表格)。"""
    results: list[dict] = []
    errors: list[dict] = []
    frames: dict[str, pd.DataFrame] = {}

    if args.kind == "macro":
        record, _ = _macro_record()
        _print_payload(record["payload"], "宏观快照（利率/CPI/PMI）", log)
        return [record], errors, frames

    symbols = split_symbols(args.symbols or "", min_count=1, what=f"--kind {args.kind}")

    if args.kind == "fundamentals":
        record, df = _fundamentals_record(symbols)
        if df is not None:
            frames["fundamentals"] = df
            _print_table(record, df, "财务指标", args, log, stderr)
        return [record], errors, frames

    for symbol in symbols:
        log(f"取数 {symbol} {args.kind} ...")
        try:
            if args.kind == "klines":
                record, df = _kline_record(symbol, args)
                _print_kline(record, df, args, log, stderr)
            elif args.kind == "dividends":
                record, df = _dividends_record(symbol)
                _print_table(record, df, f"{symbol} 每股分红", args, log, stderr)
            else:  # valuation
                record, df = _valuation_record(symbol, args.years)
                _print_payload(record["payload"], f"{symbol} 估值分位", log)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
            log(f"  [skip] {symbol}: {exc}")
            continue
        results.append(record)
        if df is not None:
            frames[symbol] = df

    if not results:
        raise DataFetchError(
            f"全部 {len(symbols)} 个标的取数失败：\n  "
            + "\n  ".join(e["error"] for e in errors)
            + "\n可用 run_doctor.py --json 体检数据源可用性。"
        )
    return results, errors, frames


def main() -> None:
    args = parse_args_with_config(build_parser())
    json_stdout, log = init_log(args)

    if args.kind not in NO_SYMBOL_KINDS and not args.symbols:
        raise SystemExit(
            f"[error] --kind {args.kind} 需要 --symbols（逗号分隔），"
            "如 --symbols 600000.SH；仅 --kind macro 可省略。"
        )

    results, errors, frames = _collect(args, log, json_stdout)
    csv_path = _export_csv(frames, args, log) if args.csv else None

    quality_failed = sum(
        1 for r in results if r.get("quality") and not r["quality"]["passed"]
    )
    log(f"\n{'═' * 60}")
    log(f"  取数完成：{len(results)} 项成功、{len(errors)} 项失败（--kind {args.kind}）")
    if quality_failed:
        log(f"  ⚠ {quality_failed} 项存在 error 级质量问题，建议 run_verify.py 交叉验证")
    log(f"{'═' * 60}")

    if args.json is not None:
        payload = attach_meta(
            {
                "kind": args.kind,
                "period": args.period,
                "count": args.count,
                "adjust": normalize_adjust(args.adjust),
                "use_cache": not args.no_cache,
                "results": results,
                "errors": errors,
                "failed": len(errors),
                "quality_failed": quality_failed,
                "csv_path": csv_path,
                "summary": _summary(args, results, errors, quality_failed),
                "next_steps": build_next_steps(
                    {"action": "score", "reason": "拿到数据后做买点三灯纪律判断",
                     "command": "run_score.py --symbol <代码> --json"},
                    {"action": "backtest", "reason": "用这份数据回测策略历史表现",
                     "command": "run_backtest.py --symbol <代码> --strategy ma_cross --json"},
                    {"action": "verify", "condition": "quality_failed > 0",
                     "reason": "存在质量问题，用另一个源交叉验证确认是哪边有问题",
                     "command": "run_verify.py --symbols <代码> --source-b akshare --json"},
                    {"action": "diagnose_sources", "condition": "failed > 0",
                     "reason": "存在取数失败，体检各数据源当下可用性",
                     "command": "run_doctor.py --json"},
                ),
            },
            command="data",
        )
        emit_json(args.json, payload, log)


def _summary(args, results: list[dict], errors: list[dict], quality_failed: int) -> str:
    """一句话摘要（Agent 转述用）。"""
    if args.kind == "klines":
        rows = sum(r.get("rows", 0) for r in results)
        sources = sorted({r.get("actual_source") or "未记录" for r in results})
        hits = sum(1 for r in results if r.get("cache_hit"))
        text = (
            f"取到 {len(results)} 只标的共 {rows} 根 {args.period} K 线"
            f"（来源：{', '.join(sources)}；{hits} 只命中本地缓存）。"
        )
    elif args.kind == "macro":
        text = "取到宏观快照（10 年期国债收益率 / CPI 同比 / PMI）。"
    else:
        text = f"取到 {len(results)} 项 {args.kind} 数据。"
    if quality_failed:
        text += f" 其中 {quality_failed} 项存在 error 级质量问题，建议交叉验证。"
    if errors:
        text += f" {len(errors)} 项失败：" + "；".join(e["error"][:60] for e in errors)
    return text


if __name__ == "__main__":
    run_cli(main)
