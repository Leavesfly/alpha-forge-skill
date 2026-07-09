"""新闻抓取：经 akshare 拉取 A 股个股新闻。

数据源：akshare ``ak.stock_news_em``（东方财富个股新闻）。
返回列通常为：关键词/新闻标题/新闻内容/发布时间/文章来源/新闻链接。

边界与限制（务必知悉）：
- 仅返回「最近约 100 条」新闻，无法按任意历史区间深挖 → 情绪回测定位为
  「近端短窗口 + AI 原生工作流」演示，而非长周期策略验证。
- 仅支持 A 股（SH/SZ/BJ）；非 A 股标的会直接报错提示。
"""

from __future__ import annotations

import pandas as pd

#: 落地文件的统一列
NEWS_COLUMNS = ["date", "title", "content", "source", "url"]


def to_ak_symbol(symbol: str) -> str:
    """将项目代码格式转换为 akshare 所需的纯数字代码。

    例：``600000.SH`` -> ``600000``；``000001.SZ`` -> ``000001``。
    仅支持 A 股后缀（SH/SZ/BJ）。
    """
    code, _, market = symbol.partition(".")
    market = market.upper()
    if market not in ("SH", "SZ", "BJ"):
        raise ValueError(
            f"新闻情绪模块目前仅支持 A 股（SH/SZ/BJ），收到：{symbol}。"
        )
    return code.strip()


def fetch_stock_news(symbol: str) -> pd.DataFrame:
    """拉取个股新闻并规整为统一列的 DataFrame（按时间升序）。

    Args:
        symbol: 项目代码格式，如 ``600000.SH``。

    Returns:
        含 NEWS_COLUMNS 列的 DataFrame；date 为 datetime。
    """
    try:
        import akshare as ak
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "新闻情绪模块需要 akshare，请在 scripts/ 下执行 `uv sync` 安装依赖。"
        ) from exc

    code = to_ak_symbol(symbol)
    raw = ak.stock_news_em(symbol=code)
    if raw is None or len(raw) == 0:
        raise RuntimeError(f"未获取到 {symbol} 的新闻，请稍后重试或更换标的。")

    # 兼容列名（akshare 版本差异），做鲁棒映射
    colmap = {
        "发布时间": "date",
        "新闻标题": "title",
        "新闻内容": "content",
        "文章来源": "source",
        "新闻链接": "url",
    }
    df = raw.rename(columns=colmap)
    for col in NEWS_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df = df[NEWS_COLUMNS].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    return df


def save_news(df: pd.DataFrame, path: str) -> str:
    """保存新闻到 CSV，返回路径。"""
    from pathlib import Path

    out = Path(path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    return str(out)
