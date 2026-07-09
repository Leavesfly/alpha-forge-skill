"""新闻情绪策略包（akshare 新闻 + agent LLM 打分 + 情绪信号回测）。"""

from __future__ import annotations

from .model import SentimentResult, aggregate_daily, run_sentiment_strategy
from .news import fetch_stock_news, save_news, to_ak_symbol
from .score import (
    build_scoring_prompt,
    lexicon_score,
    load_scores,
    write_template,
)

__all__ = [
    "SentimentResult",
    "aggregate_daily",
    "run_sentiment_strategy",
    "fetch_stock_news",
    "save_news",
    "to_ak_symbol",
    "build_scoring_prompt",
    "lexicon_score",
    "load_scores",
    "write_template",
]
