"""情绪打分契约与工具（agent-in-the-loop）。

工作流约定（三步）：
1. ``run_sentiment.py --stage fetch``：抓新闻落 ``news_<symbol>.csv``，并生成
   待填的 ``sentiment_<symbol>.csv`` 模板 + 打分提示。
2. agent（LLM）读取 ``news_<symbol>.csv``，按提示逐条判断情绪，将分数写入
   ``sentiment_<symbol>.csv``。
3. ``run_sentiment.py --stage backtest``：读取打分，聚合为信号并回测。

打分文件契约 ``sentiment_<symbol>.csv``：
- 列：``date``（YYYY-MM-DD HH:MM:SS，与新闻一一对应）、``score``（浮点，取值 [-1, 1]）。
- score 语义：+1 极度利好、0 中性、-1 极度利空。
- 缺失/无法判断记 0。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

#: 打分文件列
SCORE_COLUMNS = ["date", "score"]

#: 给 agent 的打分说明（写入模板头部，指导逐条打分）
SCORING_GUIDE = (
    "# 情绪打分说明（供 AI 逐条填写 score 列）\n"
    "# 阅读 news_<symbol>.csv 的每条 title/content，判断其对该股票的情绪倾向：\n"
    "#   +1.0 = 极度利好，  +0.5 = 偏利好，  0.0 = 中性/无关，\n"
    "#   -0.5 = 偏利空，    -1.0 = 极度利空。\n"
    "# 将判断写入本文件 score 列（date 已与新闻对齐，勿改动 date 顺序）。\n"
)


def build_scoring_prompt(news: pd.DataFrame, symbol: str) -> str:
    """生成给 agent 的逐条打分提示文本。"""
    lines = [
        f"请为标的 {symbol} 的以下 {len(news)} 条新闻逐条打情绪分（-1 到 1）：",
        "评分标准：+1 极度利好 / +0.5 偏利好 / 0 中性 / -0.5 偏利空 / -1 极度利空。",
        f"完成后把分数按顺序写入 sentiment_{_tag(symbol)}.csv 的 score 列。",
        "",
    ]
    for i, row in news.reset_index(drop=True).iterrows():
        title = str(row.get("title", "")).strip()
        lines.append(f"[{i}] {row['date']:%Y-%m-%d} | {title}")
    return "\n".join(lines)


def write_template(news: pd.DataFrame, path: str) -> str:
    """生成待填打分模板：date 已对齐、score 预置为空，返回路径。"""
    out = Path(path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmpl = pd.DataFrame({"date": news["date"], "score": ""})
    with open(out, "w", encoding="utf-8-sig") as f:
        f.write(SCORING_GUIDE)
        tmpl.to_csv(f, index=False)
    return str(out)


def load_scores(path: str) -> pd.DataFrame:
    """读取打分文件，返回含 date(datetime)/score(float) 的 DataFrame。

    以 ``#`` 开头的说明行会被跳过；无法解析的 score 记为 0。
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(
            f"未找到打分文件 {p}。请先运行 `--stage fetch`，由 AI 填好情绪分后再回测。"
        )
    df = pd.read_csv(p, comment="#")
    if "date" not in df.columns or "score" not in df.columns:
        raise ValueError(f"打分文件需包含 date/score 两列，实际列：{list(df.columns)}")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["score"] = pd.to_numeric(df["score"], errors="coerce").fillna(0.0).clip(-1.0, 1.0)
    return df.dropna(subset=["date"])[SCORE_COLUMNS]


#: 关键词词典兜底（agent 未参与时仍可端到端跑通，质量较粗糙）
_POSITIVE = [
    "涨", "增长", "利好", "盈利", "超预期", "中标", "回购", "增持", "突破",
    "创新高", "扭亏", "分红", "签约", "获批", "提升", "受益",
]
_NEGATIVE = [
    "跌", "下滑", "利空", "亏损", "不及预期", "减持", "违规", "处罚", "退市",
    "下调", "风险", "诉讼", "问询", "商誉", "爆雷", "质押",
]


def lexicon_score(news: pd.DataFrame) -> pd.DataFrame:
    """关键词词典兜底打分：按标题+正文命中正负词计净情绪，返回 date/score。

    仅作为 agent 未打分时的降级方案，质量有限。
    """
    scores = []
    for _, row in news.iterrows():
        text = f"{row.get('title', '')}{row.get('content', '')}"
        pos = sum(w in text for w in _POSITIVE)
        neg = sum(w in text for w in _NEGATIVE)
        total = pos + neg
        scores.append(0.0 if total == 0 else (pos - neg) / total)
    return pd.DataFrame({"date": news["date"].values, "score": scores})


def _tag(symbol: str) -> str:
    """标的代码转文件名安全片段（复用命名口径）。"""
    import re

    return re.sub(r"[^0-9A-Za-z_-]+", "", str(symbol))
