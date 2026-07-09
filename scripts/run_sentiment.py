#!/usr/bin/env python3
"""新闻情绪交易 CLI（agent-in-the-loop 两阶段）。

让 AI 实时读新闻给出情绪判断，再转化为持仓信号回测。情绪判断由 agent 的 LLM
完成，无需本地 NLP 模型或额外 LLM Key。

三步工作流：
  1) fetch    —— 抓 A 股个股新闻，落 ../outputs/news_<标的>.csv，并生成待填的
                 ../outputs/sentiment_<标的>.csv 模板与打分提示；
  2) (人/AI)  —— agent 读 news_<标的>.csv，按提示逐条判断情绪，把分数写入
                 sentiment_<标的>.csv 的 score 列（[-1, 1]）；
  3) backtest —— 读取打分，聚合为日度情绪信号并回测出报告/图表。

示例：
    uv run python run_sentiment.py --symbol 600000.SH --stage fetch
    # （agent 填好 sentiment_600000SH.csv 后）
    uv run python run_sentiment.py --symbol 600000.SH --stage backtest --plot

    # 无 agent 参与时，可用关键词词典兜底端到端跑通（质量有限）
    uv run python run_sentiment.py --symbol 600000.SH --stage backtest --use-lexicon --plot
"""

from __future__ import annotations

import argparse

from backtest.metrics import format_report
from datafeed import fetch_ohlcv
from naming import default_output, sanitize
from sentiment import (
    build_scoring_prompt,
    fetch_stock_news,
    lexicon_score,
    load_scores,
    run_sentiment_strategy,
    save_news,
    write_template,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alpha Forge 新闻情绪交易（akshare 新闻 + agent LLM 打分）")
    parser.add_argument("--symbol", required=True, help="A 股标的代码，如 600000.SH")
    parser.add_argument("--stage", required=True, choices=["fetch", "backtest"], help="执行阶段")
    parser.add_argument("--period", default="1d", help="K 线周期，默认 1d")
    parser.add_argument("--count", type=int, default=250, help="回测 K 线数量，默认 250")
    parser.add_argument("--entry", type=float, default=0.2, help="开仓情绪阈值，默认 0.2")
    parser.add_argument("--exit", type=float, default=0.05, help="中性带下限，默认 0.05")
    parser.add_argument("--hold", type=int, default=5, help="新闻情绪持续天数（前向填充上限），默认 5")
    parser.add_argument("--smooth", type=int, default=3, help="日度情绪滚动平滑窗口，默认 3")
    parser.add_argument("--allow-short", action="store_true", help="开启做空（极端利空输出 -1）")
    parser.add_argument("--use-lexicon", action="store_true", help="backtest 阶段用关键词词典兜底打分（无 agent 时）")
    parser.add_argument("--commission", type=float, default=0.0005, help="单边手续费率")
    parser.add_argument("--slippage", type=float, default=0.0005, help="单边滑点率")
    parser.add_argument("--plot", action="store_true", help="生成新闻情绪策略图表")
    parser.add_argument("--output", default=None, help="图表输出路径；默认按 ../outputs/sentiment_<标的>.png 命名")
    return parser


def _news_path(symbol: str) -> str:
    return f"../outputs/news_{sanitize(symbol)}.csv"


def _score_path(symbol: str) -> str:
    return f"../outputs/sentiment_{sanitize(symbol)}.csv"


def stage_fetch(args) -> None:
    print(f"抓取 {args.symbol} 的个股新闻（akshare 东方财富源）...")
    news = fetch_stock_news(args.symbol)
    news_path = save_news(news, _news_path(args.symbol))
    tmpl_path = write_template(news, _score_path(args.symbol))

    print(f"\n已获取 {len(news)} 条新闻 -> {news_path}")
    print(f"打分模板（待填 score 列）-> {tmpl_path}")
    print("\n" + "=" * 60)
    print("下一步（agent-in-the-loop）：")
    print(f"  1. 阅读 {news_path} 每条 title/content；")
    print(f"  2. 逐条判断情绪，将分数（-1~1）写入 {tmpl_path} 的 score 列；")
    print(f"  3. 运行：uv run python run_sentiment.py --symbol {args.symbol} --stage backtest --plot")
    print("=" * 60)
    print("\n提示词预览（前若干条）：")
    prompt = build_scoring_prompt(news, args.symbol)
    print("\n".join(prompt.splitlines()[:12]))
    print("...")


def stage_backtest(args) -> None:
    print(f"拉取 {args.symbol} {args.period} K 线（{args.count} 根）...")
    df = fetch_ohlcv(args.symbol, period=args.period, count=args.count)

    if args.use_lexicon:
        from sentiment import fetch_stock_news as _fetch

        print("使用关键词词典兜底打分（无 agent 参与，质量有限）...")
        scores = lexicon_score(_fetch(args.symbol))
    else:
        scores = load_scores(_score_path(args.symbol))
        print(f"已加载 {len(scores)} 条情绪打分。")

    result = run_sentiment_strategy(
        df, scores,
        symbol=args.symbol, period=args.period,
        entry=args.entry, exit=args.exit, hold=args.hold, smooth=args.smooth,
        allow_short=args.allow_short,
        commission=args.commission, slippage=args.slippage,
    )

    metrics = result.backtest.metrics
    print()
    print(f"新闻条数      : {result.n_news}")
    print(f"有新闻的天数  : {result.n_days_with_news}")
    print()
    print(format_report(metrics, title=f"{args.symbol} 新闻情绪"))
    print()
    print(format_report(result.backtest.benchmark_metrics, title="基准 Buy & Hold"))

    if metrics.get("sharpe", 0.0) > 3.0:
        print(
            "\n[警惕] 夏普比率 > 3，请优先排查未来数据泄露、样本偏差或新闻覆盖不足，"
            "而非当作策略有效。新闻历史仅约 100 条，回测窗口偏短，结论谨慎对待。"
        )

    if args.plot:
        from sentiment.plot import plot_sentiment

        output = args.output or default_output("sentiment", args.symbol)
        path = plot_sentiment(result, title=f"新闻情绪策略 {args.symbol}", output=output)
        print(f"\n图表已保存：{path}")


def main() -> None:
    args = build_parser().parse_args()
    if args.stage == "fetch":
        stage_fetch(args)
    else:
        stage_backtest(args)


if __name__ == "__main__":
    main()
