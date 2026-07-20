"""情绪打分模块回归测试：词典兜底打分与打分文件契约。"""

from __future__ import annotations

import pandas as pd
import pytest

from sentiment.score import lexicon_score, load_scores, write_template


def _news(rows: list[tuple[str, str]]) -> pd.DataFrame:
    """构造新闻 DataFrame：(title, content) 列表 -> date/title/content。"""
    dates = pd.date_range("2024-01-01 09:00", periods=len(rows), freq="D")
    return pd.DataFrame(
        {
            "date": dates,
            "title": [t for t, _ in rows],
            "content": [c for _, c in rows],
        }
    )


# ------------------------------------------------------------ 词典打分


def test_lexicon_score_polarity():
    news = _news(
        [
            ("公司业绩增长 获重大合同中标", ""),  # 全正面词
            ("年报亏损扩大 存在退市风险", ""),  # 全负面词
            ("公司召开股东大会", ""),  # 无命中，中性
            ("利好落地但商誉减值", ""),  # 正负各一，抵消
        ]
    )
    scores = lexicon_score(news)
    assert list(scores.columns) == ["date", "score"]
    assert len(scores) == len(news)
    assert scores["score"].iloc[0] == pytest.approx(1.0)
    assert scores["score"].iloc[1] == pytest.approx(-1.0)
    assert scores["score"].iloc[2] == 0.0
    assert scores["score"].iloc[3] == 0.0
    # 取值域 [-1, 1]
    assert scores["score"].between(-1.0, 1.0).all()


def test_lexicon_score_uses_content_too():
    """正文中的关键词也应参与打分。"""
    news = _news([("中性标题", "公司股价突破创新高")])
    assert lexicon_score(news)["score"].iloc[0] > 0


# ------------------------------------------------------ 打分文件契约


def test_write_template_load_roundtrip(tmp_path):
    """模板：# 说明行被跳过、空 score 记 0、date 与新闻对齐。"""
    news = _news([("标题一", ""), ("标题二", "")])
    path = str(tmp_path / "sentiment_TEST.csv")
    write_template(news, path)

    scores = load_scores(path)
    assert len(scores) == len(news)
    assert (scores["score"] == 0.0).all()
    assert list(scores["date"]) == list(news["date"])


def test_load_scores_parses_and_clips(tmp_path):
    path = tmp_path / "scores.csv"
    path.write_text(
        "# 说明行应被跳过\n"
        "date,score\n"
        "2024-01-01,0.7\n"
        "2024-01-02,abc\n"  # 非法 -> 0
        "2024-01-03,1.5\n"  # 越界 -> 裁剪到 1
        "2024-01-04,-2\n"  # 越界 -> 裁剪到 -1
        "bad-date,0.5\n",  # 日期非法 -> 丢弃
        encoding="utf-8",
    )
    scores = load_scores(str(path))
    assert len(scores) == 4
    assert scores["score"].tolist() == [0.7, 0.0, 1.0, -1.0]


def test_load_scores_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_scores(str(tmp_path / "not_exist.csv"))


def test_load_scores_missing_columns_raises(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_scores(str(path))
