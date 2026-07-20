#!/usr/bin/env python3
"""单股纪律评分 CLI：四层否决式评分 -> 结论 -> 交易计划 -> 可选回放验证。

回答的不是「这个标的好不好」，而是：按当前价量结构、市场环境和风险约束，
现在是否适合参与。结论五态：是 / 观察 / 否 / 持仓需减风险 / 无法评分。

四层各司其职、单向降级（利好不能救弱势标的）：
    ① ALPHA 加权（动量 55 · 相对强度 35 · 趋势效率 10）→ 排名分
    ② 风险否决（MA60/MA200 · 周线结构 · 大盘环境）→ 封顶或否决
    ③ 技术确认（MACD · RSI · KDJ · 量价）→ 只拦截「是」
    ④ 入场时机（过热降级 · 回调确认）→ 调整入场结论

示例：
    # 单股评分（A 股基准自动取 510300.SH，免费日 K 即可）
    uv run python run_score.py --symbol 600000.SH

    # 只要结论与计划价位（简短模式）
    uv run python run_score.py --symbol AAPL.US --brief

    # 历史回放验证：最近 250 日逐日评分 + 21/63 日前瞻收益事件研究
    uv run python run_score.py --symbol 600519.SH --count 800 --replay --plot

    # 结合持仓成本给操作建议（结论可能变为「持仓需减风险」）
    uv run python run_score.py --symbol 600000.SH --cost 8.50 --shares 1000

    # 事件风险降级：读取 agent 标注的风险文件（date,risk,note）
    uv run python run_score.py --symbol 600000.SH --risk-file ../outputs/risk_600000SH.csv

    # 事件风险三步闭环第一步：抓新闻/公告素材并生成待标注模板（agent 填 risk 列后用 --risk-file 回传）
    uv run python run_score.py --symbol 600000.SH --fetch-events
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from cli_common import (
    add_json_arg,
    build_next_steps,
    check_symbol,
    emit_json,
    make_logger,
    make_parser,
    run_cli,
)
from cli_config import parse_args_with_config
from datafeed import fetch_ohlcv
from naming import default_output, sanitize
from report import attach_meta
from scoring import (
    attach_position_sizing,
    default_benchmark,
    format_plan,
    format_replay_report,
    replay_study,
    replay_verdicts,
    score_symbol,
)
from scoring.replay import calibrate_threshold

LAYER_CN = {
    "data": "数据检查",
    "alpha": "ALPHA 加权",
    "veto": "风险否决",
    "confirm": "技术确认",
    "timing": "入场时机",
    "event_risk": "事件风险",
}

DISCLAIMER = (
    "提示：评分是纪律工具而非收益预测，阈值未经过样本外验证（可用 --replay 自证）；"
    "可用 run_backtest.py 验证策略、run_paper.py 跟踪模拟盘。不构成投资建议。"
)


def build_parser() -> argparse.ArgumentParser:
    parser = make_parser("Alpha Forge 单股纪律评分（四层否决式）", __doc__)
    parser.add_argument("--symbol", required=True, help="标的代码，如 600000.SH / AAPL.US")
    parser.add_argument(
        "--benchmark",
        default=None,
        help="基准代码；默认按市场自动选择（A股 510300.SH / 港股 02800.HK / 美股 SPY.US）",
    )
    parser.add_argument("--period", default="1d", help="K 线周期，默认 1d（评分按日线纪律设计）")
    parser.add_argument("--count", type=int, default=500, help="K 线数量，默认 500（评分至少需 250 根）")
    parser.add_argument("--brief", action="store_true", help="简短模式：只输出结论与计划价位")
    parser.add_argument(
        "--replay",
        nargs="?",
        const=250,
        default=None,
        type=int,
        metavar="N",
        help="历史回放验证：最近 N 个交易日逐日评分 + 21/63 日前瞻收益事件研究（默认 250）",
    )
    parser.add_argument(
        "--calibrate",
        nargs="?",
        const=250,
        default=None,
        type=int,
        metavar="N",
        help="阈值自校准：回放最近 N 日，网格搜索最优 alpha_score 入场阈值（默认 250）",
    )
    parser.add_argument("--calibrate-horizon", type=int, default=21, help="校准前瞻窗口（交易日），默认 21")
    parser.add_argument(
        "--risk-file",
        default=None,
        help="事件风险 CSV（列：date,risk∈{high,medium,low},note）；近 30 天 high 事件只降级不加分",
    )
    parser.add_argument(
        "--fetch-events",
        action="store_true",
        help="事件风险第一步：抓个股新闻/公告素材并生成待标注风险模板（仅 A 股；agent 填 risk 列后用 --risk-file 回传）",
    )
    parser.add_argument("--cost", type=float, default=None, help="持仓成本价；提供后输出持仓操作建议")
    parser.add_argument("--shares", type=float, default=None, help="持仓数量（配合 --cost 计算市值）")
    parser.add_argument("--capital", type=float, default=100_000.0, help="可用资金（用于交易计划建议仓位），默认 10 万；0 关闭建议仓位")
    parser.add_argument("--risk-pct", type=float, default=0.01, help="单笔交易风险预算占资金比例，默认 0.01（止损时约亏资金的 1%%）")
    parser.add_argument("--plot", action="store_true", help="生成评分图（价格 + 均线 + 计划价位；回放时背景按结论着色）")
    parser.add_argument("--output", default=None, help="图表输出路径；默认 ../outputs/score_<标的>.png")
    add_json_arg(parser)
    return parser


def load_risk_events(path: str) -> list[dict]:
    """读取事件风险 CSV（date,risk,note），格式错误时给出可操作提示。"""
    import pandas as pd

    file = Path(path).expanduser()
    if not file.exists():
        raise SystemExit(f"[error] 风险文件不存在：{path}。先抓新闻并让 agent 标注（见 references/scoring.md）。")
    df = pd.read_csv(file)
    missing = {"date", "risk"} - set(df.columns)
    if missing:
        raise SystemExit(
            f"[error] 风险文件缺少列 {sorted(missing)}；需要 date,risk(high/medium/low),note 三列。"
        )
    if "note" not in df.columns:
        df["note"] = ""
    return df[["date", "risk", "note"]].fillna("").to_dict("records")


def fetch_event_material(symbol: str, log, json_dest, emit) -> None:
    """事件风险 agent-in-the-loop 第一步：抓新闻素材 + 生成待标注模板。

    产出两个文件（outputs/ 下）：
    - ``events_<标的>.csv``：新闻素材（date,title,source,url），供 agent 阅读；
    - ``risk_<标的>.csv``：待标注模板（date,risk,note），risk 列留空由 agent
      逐行填入 high/medium/low（无风险的行删除），再用 --risk-file 回传评分。
    """
    import pandas as pd

    from sentiment.news import fetch_stock_news

    log(f"拉取 {symbol} 个股新闻/公告素材（akshare，仅 A 股，近约 100 条）...")
    news = fetch_stock_news(symbol)
    out_dir = Path(__file__).resolve().parent.parent / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = sanitize(symbol)

    events_path = out_dir / f"events_{tag}.csv"
    news_out = news[["date", "title", "source", "url"]].copy()
    news_out["date"] = pd.to_datetime(news_out["date"]).dt.strftime("%Y-%m-%d")
    news_out.to_csv(events_path, index=False, encoding="utf-8-sig")

    risk_path = out_dir / f"risk_{tag}.csv"
    template = pd.DataFrame(
        {"date": news_out["date"], "risk": "", "note": news_out["title"]}
    ).drop_duplicates(subset=["date", "note"])
    template.to_csv(risk_path, index=False, encoding="utf-8-sig")

    log(f"素材已保存：{events_path}（{len(news_out)} 条）")
    log(f"待标注模板：{risk_path}")
    log("")
    log("下一步（agent 标注流程）：")
    log("  1. 阅读素材文件，识别具风险属性的事件（财报爆雷/监管处罚/减持/诉讼等）；")
    log("  2. 在模板的 risk 列填入 high/medium/low（无风险的行直接删除，note 可改写为风险要点）；")
    log(f"  3. 重跑评分：run_score.py --symbol {symbol} --risk-file {risk_path}")
    log("注：近 30 天的 high 事件只降级不加分（利好不救弱势标的）。")

    if json_dest is not None:
        payload = attach_meta(
            {
                "symbol": symbol,
                "stage": "fetch_events",
                "news_count": int(len(news_out)),
                "events_file": str(events_path),
                "risk_template": str(risk_path),
                "summary": (
                    f"已抓取 {symbol} 新闻素材 {len(news_out)} 条并生成风险标注模板。"
                    "agent 需逐条判断风险属性写入 risk 列，再用 --risk-file 回传评分。"
                ),
                "next_steps": build_next_steps(
                    {"action": "annotate", "reason": "agent 阅读素材并标注风险等级",
                     "command": f"读 {events_path} 后将 risk∈{{high,medium,low}} 写入 {risk_path}"},
                    {"action": "score", "reason": "带事件风险重跑评分",
                     "command": f"run_score.py --symbol {symbol} --risk-file {risk_path} --json"},
                ),
            },
            command="score",
        )
        emit(json_dest, payload, log)


def detect_account_position(symbol: str, log) -> dict | None:
    """读取统一持仓账户（run_account.py 登记）的持仓。"""
    from account import get_position

    try:
        pos = get_position(symbol)
    except RuntimeError:  # 账户文件损坏不阻断评分，降级为无持仓
        return None
    if pos:
        log(f"检测到账户持仓（account.json）：{pos['shares']:g} 股，成本 {pos['cost']}（显式 --cost 优先）")
    return pos


def detect_paper_position(symbol: str, log) -> dict | None:
    """自动探测模拟盘状态文件的持仓（近似成本 = (初始资金-现金)/持股）。"""
    out_dir = Path(__file__).resolve().parent.parent / "outputs"
    for path in sorted(out_dir.glob(f"paper_{sanitize(symbol)}_*.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        shares = state.get("shares") or 0
        if shares > 0:
            cost = (state.get("initial_capital", 0) - state.get("cash", 0)) / shares
            if cost > 0:
                log(f"检测到模拟盘持仓（{path.name}）：{shares} 股，近似成本 {cost:.3f}（显式 --cost 优先）")
                return {"cost": cost, "shares": shares, "source": f"paper:{path.name}"}
    return None


def _close_series(df):
    """从 OHLCV DataFrame 提取带时间索引的收盘价序列。"""
    import pandas as pd

    close = df["close"].astype(float).reset_index(drop=True)
    if "trade_date" in df.columns:
        close.index = pd.DatetimeIndex(pd.to_datetime(df["trade_date"]))
    return close


def main() -> None:
    args = parse_args_with_config(build_parser())
    check_symbol(args.symbol)
    json_stdout = args.json == "-"
    log = make_logger(json_stdout)

    if args.fetch_events:
        fetch_event_material(args.symbol, log, args.json, emit_json)
        return

    log(f"拉取 {args.symbol} {args.period} K 线（{args.count} 根）...")
    df = fetch_ohlcv(args.symbol, period=args.period, count=args.count)

    # 基准：显式 > 按市场默认；拉取失败降级为无基准评分（stderr 告警）
    bench_symbol = args.benchmark or default_benchmark(args.symbol)
    bench_close = None
    if bench_symbol:
        try:
            bench_close = _close_series(
                fetch_ohlcv(bench_symbol, period=args.period, count=args.count)
            )
        except Exception as exc:
            print(f"[warn] 基准 {bench_symbol} 拉取失败（{type(exc).__name__}），降级为无基准评分", file=sys.stderr)
            bench_symbol = None

    risk_events = load_risk_events(args.risk_file) if args.risk_file else None
    position = None
    if args.cost is not None:
        position = {"cost": args.cost, "shares": args.shares, "source": "cli"}
    else:
        # 探测优先级：真实账户登记（run_account.py）> 模拟盘状态文件
        position = detect_account_position(args.symbol, log) or detect_paper_position(args.symbol, log)

    result = score_symbol(
        df,
        symbol=args.symbol,
        benchmark_close=bench_close,
        benchmark_symbol=bench_symbol,
        risk_events=risk_events,
        position=position,
    )

    # 市场状态（描述性上下文，不参与评分裁决）
    from research.regime import detect_regime, format_regime

    regime = detect_regime(df["close"])

    # 建议仓位：风险预算法（资金 × 风险比例 / R），回答「买多少」
    if result.plan is not None and args.capital > 0:
        lot = 100 if args.symbol.upper().endswith((".SH", ".SZ", ".BJ")) else 1
        attach_position_sizing(result.plan, args.capital, args.risk_pct, lot_size=lot)

    # ---------- 终端输出（裁决式，结论先行） ----------
    log()
    log(f"========== {args.symbol} 纪律评分（截至 {result.asof}）==========")
    log(f"结论          : {result.verdict_cn}")
    log(format_regime(regime))
    if result.alpha_score is not None:
        comp = result.components
        parts = [f"动量 {comp['momentum']['score']:.0f}"]
        if comp["rel_strength"]["score"] is not None:
            parts.append(f"相对强度 {comp['rel_strength']['score']:.0f}")
        parts.append(f"趋势效率 {comp['efficiency']['score']:.0f}")
        log(f"排名分        : {result.alpha_score:.1f}（{' / '.join(parts)}）")
    if bench_symbol:
        log(f"基准          : {bench_symbol}")

    if not args.brief:
        log("--- 关键证据（分层拆解） ---")
        for layer in result.layers:
            tag = LAYER_CN.get(layer["name"], layer["name"])
            status = {"pass": "通过", "veto": "否决", "cap": "封顶", "downgrade": "降级"}.get(
                layer["status"], layer["status"]
            )
            log(f"[{tag}] {status}")
            for reason in layer["reasons"]:
                log(f"  · {reason}")

    if result.position is not None:
        log("--- 持仓状态 ---")
        pos = result.position
        log(f"成本 {pos['cost']}，浮盈亏 {pos['pnl_pct'] * 100:+.2f}%" + (
            f"，市值 {pos['market_value']:,.2f}" if pos.get("market_value") else ""
        ))
        if pos.get("stop_ref"):
            log(f"止损参考 {pos['stop_ref']}（距当前 {pos['stop_distance_pct'] * 100:+.2f}%）")
        log(f"建议：{pos['advice']}")

    if result.plan is not None or result.verdict in ("yes", "watch"):
        log("--- 交易计划（风险管理参考，非订单指令） ---")
        for line in format_plan(result.plan):
            log(line)

    # ---------- P1：历史回放验证 ----------
    replay_payload = None
    verdict_series = None
    if args.replay is not None:
        log()
        log(f"回放最近 {args.replay} 个交易日（逐日 final-only 重算，无前视）...")
        verdict_series = replay_verdicts(df, benchmark_close=bench_close, days=args.replay, symbol=args.symbol)
        replay_payload = replay_study(df, verdict_series, benchmark_close=bench_close)
        log("--- 回放验证（评分是否有用，用数据说话） ---")
        for line in format_replay_report(replay_payload):
            log(line)

    # ---------- P2：阈值自校准 ----------
    calibrate_payload = None
    if args.calibrate is not None:
        log()
        log(f"阈值自校准：回放最近 {args.calibrate} 日，前瞻 {args.calibrate_horizon} 日收益...")
        calibrate_payload = calibrate_threshold(
            df, benchmark_close=bench_close, days=args.calibrate,
            horizon=args.calibrate_horizon, symbol=args.symbol,
        )
        log("--- 阈值校准结果 ---")
        if calibrate_payload.get("best_threshold") is not None:
            log(f"  最优阈值    : alpha_score >= {calibrate_payload['best_threshold']:.0f}")
            log(f"  胜率        : {calibrate_payload['best_hit_rate'] * 100:.1f}%"
                f"（{calibrate_payload['best_n']} 个样本）")
            log(f"  平均前瞻收益: {calibrate_payload['best_avg_return'] * 100:+.2f}%"
                f"（{args.calibrate_horizon} 日）")
            log(f"  当前预设    : ALPHA_YES=60（原著值，未经样本外验证）")
            if calibrate_payload["best_threshold"] != 60.0:
                log(f"  建议        : 可考虑将入场阈值调整为 {calibrate_payload['best_threshold']:.0f}"
                    "（需自知偏离原著标准的风险）")
        else:
            log(f"  无法校准：{calibrate_payload.get('note', '样本不足')}")
        if calibrate_payload.get("grid"):
            log("  阈值网格（胜率 / 样本数 / 平均收益）：")
            for g in calibrate_payload["grid"]:
                marker = " ←" if g["threshold"] == calibrate_payload.get("best_threshold") else ""
                log(f"    >= {g['threshold']:3.0f} : {g['hit_rate']*100:5.1f}% / {g['n']:3d} / {g['avg_return']*100:+5.2f}%{marker}")

    log()
    log(DISCLAIMER)

    if args.plot:
        from scoring.plot import plot_score

        close = _close_series(df)
        output = args.output or default_output("score", args.symbol)
        path = plot_score(
            close,
            plan=result.plan,
            verdicts=verdict_series,
            title=f"{args.symbol} 纪律评分：{result.verdict_cn}（截至 {result.asof}）",
            output=output,
        )
        log(f"图表已保存：{path}")

    if args.json is not None:
        digest = hashlib.sha1(
            f"{args.symbol}|{args.period}|{args.count}|{bench_symbol}|{result.asof}".encode()
        ).hexdigest()[:12]
        # Agent 友好：自然语言结论 + 结构化下一步
        layer_reason = ""
        for ly in result.layers:
            if ly["status"] in ("veto", "cap", "downgrade"):
                tag = LAYER_CN.get(ly["name"], ly["name"])
                layer_reason = f"（{tag}层拦截）"
                break
        plan_str = ""
        if result.plan:
            plan_str = f"参考计划：入场 {result.plan.get('entry')} / 止损 {result.plan.get('stop')}。"
        summary = (
            f"{args.symbol} 纪律评分结论：{result.verdict_cn}{layer_reason}。"
            f"{plan_str}"
            f"评分是纪律过滤而非涨跌预测，不构成投资建议。"
        )
        next_steps = build_next_steps(
            {"action": "paper", "reason": "按评分裁决每日纸面跟踪",
             "command": f"run_paper.py --symbol {args.symbol} --mode score --json"},
            {"action": "replay", "reason": "回放验证评分有效性",
             "command": f"run_score.py --symbol {args.symbol} --replay 120 --json"},
            {"action": "backtest", "reason": "用策略回测验证历史表现",
             "command": f"run_backtest.py --symbol {args.symbol} --strategy ma_cross --json"},
        )
        payload = attach_meta(
            {
                **result.to_dict(),
                "period": args.period,
                "count": args.count,
                "regime": regime,
                "replay": replay_payload,
                "calibrate": calibrate_payload,
                "data_hash": digest,
                "summary": summary,
                "next_steps": next_steps,
            },
            command="score",
        )
        emit_json(args.json, payload, log)


if __name__ == "__main__":
    run_cli(main)
