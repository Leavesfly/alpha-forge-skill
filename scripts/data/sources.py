"""数据源抽象：TickFlow 主源 + baostock / akshare / yfinance 兜底源。

动机：datafeed 原先绑定 TickFlow 单一数据源，服务不可用时整个链路失效。
本模块把「拉取单标的 K 线」抽象为 ``DataSource`` 协议：

- ``TickFlowSource``：主源，多市场全周期；
- ``BaostockSource``：二级兜底，仅沪深 A 股日/周/月 K（免费、无需 Key、API 级稳定）；
- ``AkshareSource``：三级兜底，仅 A 股日/周/月 K（免费、无需 Key）；
- ``YFinanceSource``：港股/美股兜底，日/周/月 K（Yahoo Finance，免费、无需 Key）；
- ``get_sources``：按环境变量 ``ALPHA_FORGE_DATA_SOURCE`` 返回数据源链
  （``tickflow`` / ``baostock`` / ``akshare`` / ``yfinance`` 强制单源，
  缺省 auto = A 股三级降级 + 港美股 yfinance 兜底）。

所有源返回列名归一的升序 DataFrame：``trade_date/open/high/low/close/volume``。
"""

from __future__ import annotations

import os
from typing import Protocol

import pandas as pd

from envconfig import get_env_config

# 需要 TICKFLOW_API_KEY 的接口在报错/告警时统一附带此指引，
# 提醒用户去哪里申请、如何设置与验证。
API_KEY_HELP = (
    "如何获取并配置 TICKFLOW_API_KEY：\n"
    "  1. 前往 https://tickflow.org 注册并在控制台申请 API Key；\n"
    '  2. 设置环境变量（macOS/Linux）：export TICKFLOW_API_KEY="your-api-key"；\n'
    "     持久化写入 shell 配置：\n"
    "       echo 'export TICKFLOW_API_KEY=\"your-api-key\"' >> ~/.zshrc && source ~/.zshrc\n"
    "  3. 验证：执行 echo $TICKFLOW_API_KEY 应输出你的 Key。"
)

#: A 股市场后缀（akshare 兜底仅覆盖这些市场）
_ASTOCK_SUFFIXES = (".SH", ".SZ", ".BJ")

#: baostock 仅覆盖沪深（不含北交所）
_BAOSTOCK_SUFFIXES = (".SH", ".SZ")


class DataSource(Protocol):
    """单标的 K 线数据源协议。"""

    name: str

    def supports(self, symbol: str, period: str) -> bool:
        """该源是否覆盖此标的与周期。"""
        ...

    def fetch(self, symbol: str, period: str, count: int, adjust: str) -> pd.DataFrame:
        """拉取 K 线，返回列名归一、按时间升序的 DataFrame。"""
        ...


def _needs_api_key(period: str) -> bool:
    """分钟级周期需要 TickFlow 完整服务。"""
    return period.endswith("m")


def get_tickflow_client(period: str = "1d"):
    """根据周期与环境变量选择 TickFlow 客户端。"""
    from tickflow import TickFlow

    has_key = bool(os.environ.get("TICKFLOW_API_KEY"))
    if has_key:
        return TickFlow()
    if _needs_api_key(period):
        raise RuntimeError(
            f"周期 {period} 需要实时/分钟数据，请先配置环境变量 TICKFLOW_API_KEY 后重试。\n"
            + API_KEY_HELP
        )
    return TickFlow.free()


def _validate_and_sort(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """校验必需列并按时间升序。"""
    if df is None or len(df) == 0:
        raise RuntimeError(f"未获取到 {symbol} 的 K 线数据，请检查代码与周期。")
    if "close" not in df.columns:
        raise RuntimeError(f"返回数据缺少 close 列，实际列：{list(df.columns)}")
    for col in ("trade_date", "date", "datetime", "time"):
        if col in df.columns:
            df = df.sort_values(col).reset_index(drop=True)
            break
    return df


class TickFlowSource:
    """TickFlow 主数据源：多市场、全周期。"""

    name = "tickflow"

    def supports(self, symbol: str, period: str) -> bool:
        return True

    def fetch(self, symbol: str, period: str, count: int, adjust: str) -> pd.DataFrame:
        tf = get_tickflow_client(period)
        df = tf.klines.get(
            symbol, period=period, count=count, adjust=adjust, as_dataframe=True
        )
        return _validate_and_sort(df, symbol)


# ─── baostock ──────────────────────────────────────────────────────────────────

#: baostock 周期映射：本项目周期 -> bs frequency 参数
_BS_PERIODS = {"1d": "d", "1w": "w", "1M": "m"}

#: 复权口径映射：归一化口径 -> bs adjustflag（"2"=前复权, "1"=后复权, "3"=不复权）
_BS_ADJUSTS = {"forward": "2", "backward": "1", "none": "3"}

#: baostock 市场前缀映射
_BS_MARKET = {"SH": "sh", "SZ": "sz"}


class BaostockSource:
    """baostock 二级兜底数据源：仅沪深 A 股日/周/月 K，免费、API 级稳定。

    相比 akshare（爬虫），baostock 走专用 API 接口，不受网站改版影响，
    稳定性显著更高；但不覆盖北交所（.BJ）。
    """

    name = "baostock"

    def supports(self, symbol: str, period: str) -> bool:
        return (
            symbol.upper().endswith(_BAOSTOCK_SUFFIXES) and period in _BS_PERIODS
        )

    def fetch(self, symbol: str, period: str, count: int, adjust: str) -> pd.DataFrame:
        import baostock as bs

        code, market = symbol.rsplit(".", 1)
        bs_code = f"{_BS_MARKET[market.upper()]}.{code}"

        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"baostock 登录失败：{lg.error_msg}")
        try:
            rs = bs.query_history_k_data_plus(
                bs_code,
                "date,open,high,low,close,volume",
                frequency=_BS_PERIODS[period],
                adjustflag=_BS_ADJUSTS.get(adjust, "2"),
            )
            if rs.error_code != "0":
                raise RuntimeError(f"baostock 查询 {symbol} 失败：{rs.error_msg}")
            rows = []
            while rs.next():
                rows.append(rs.get_row_data())
        finally:
            bs.logout()

        if not rows:
            raise RuntimeError(f"baostock 未返回 {symbol} 的 K 线数据。")

        df = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "volume"])
        # baostock 返回字符串，需转数值
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        # 停牌日 volume 为空字符串 -> NaN，填 0
        df["volume"] = df["volume"].fillna(0)
        df = _validate_and_sort(df, symbol)
        return df.tail(count).reset_index(drop=True)


# ─── akshare ───────────────────────────────────────────────────────────────────

#: akshare 周期映射：本项目周期 -> ak period 参数
_AK_PERIODS = {"1d": "daily", "1w": "weekly", "1M": "monthly"}

#: 复权口径映射：归一化口径 -> ak adjust 参数
_AK_ADJUSTS = {"forward": "qfq", "backward": "hfq", "none": ""}

#: akshare 中文列名 -> 标准列名
_AK_COLUMNS = {
    "日期": "trade_date",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
}


class AkshareSource:
    """akshare 三级兜底数据源：仅 A 股日/周/月 K，免费无需 Key。"""

    name = "akshare"

    def supports(self, symbol: str, period: str) -> bool:
        return (
            symbol.upper().endswith(_ASTOCK_SUFFIXES) and period in _AK_PERIODS
        )

    def fetch(self, symbol: str, period: str, count: int, adjust: str) -> pd.DataFrame:
        import akshare as ak

        code = symbol.split(".")[0]
        df = ak.stock_zh_a_hist(
            symbol=code,
            period=_AK_PERIODS[period],
            adjust=_AK_ADJUSTS.get(adjust, "qfq"),
        )
        if df is None or len(df) == 0:
            raise RuntimeError(f"akshare 未返回 {symbol} 的 K 线数据。")
        df = df.rename(columns=_AK_COLUMNS)
        keep = [c for c in ("trade_date", "open", "high", "low", "close", "volume") if c in df.columns]
        df = df[keep].copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = _validate_and_sort(df, symbol)
        return df.tail(count).reset_index(drop=True)


# ─── yfinance ────────────────────────────────────────────────────────────────

#: yfinance 周期映射：本项目周期 -> yf interval 参数
_YF_PERIODS = {"1d": "1d", "1w": "1wk", "1M": "1mo"}

#: yfinance 仅覆盖港股/美股（A 股已有 baostock/akshare 兜底）
_YF_SUFFIXES = (".HK", ".US")


def _to_yahoo_symbol(symbol: str) -> str:
    """本项目代码 -> Yahoo Finance 代码。

    美股去后缀（AAPL.US -> AAPL）；港股 Yahoo 用 4 位数字代码
    （00700.HK -> 0700.HK）。
    """
    code, market = symbol.rsplit(".", 1)
    market = market.upper()
    if market == "US":
        return code
    return f"{int(code):04d}.HK"


class YFinanceSource:
    """yfinance 兜底数据源：港股/美股日/周/月 K，免费无需 Key。

    复权口径：forward -> ``auto_adjust=True``（Yahoo 后复权价归一，
    涨跌幅与前复权一致，回测可用）；none -> 不复权；backward 不支持
    （supports 返回 False，交给其他源或报错）。
    """

    name = "yfinance"

    def supports(self, symbol: str, period: str) -> bool:
        if not symbol.upper().endswith(_YF_SUFFIXES) or period not in _YF_PERIODS:
            return False
        code = symbol.rsplit(".", 1)[0]
        # 港股代码必须是纯数字才能映射到 Yahoo 格式
        if symbol.upper().endswith(".HK") and not code.isdigit():
            return False
        return True

    def fetch(self, symbol: str, period: str, count: int, adjust: str) -> pd.DataFrame:
        if adjust == "backward":
            raise RuntimeError(
                "yfinance 兜底源不支持后复权（hfq），请改用前复权或配置 TICKFLOW_API_KEY。"
            )
        import yfinance as yf

        ticker = _to_yahoo_symbol(symbol)
        df = yf.download(
            ticker,
            period="max",
            interval=_YF_PERIODS[period],
            auto_adjust=(adjust != "none"),
            progress=False,
            threads=False,
        )
        if df is None or len(df) == 0:
            raise RuntimeError(f"yfinance 未返回 {symbol}（{ticker}）的 K 线数据。")
        # 新版 yfinance 单标的也返回 (字段, ticker) 两层列，压平取字段层
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.reset_index()
        df = df.rename(
            columns={
                "Date": "trade_date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )
        keep = [c for c in ("trade_date", "open", "high", "low", "close", "volume") if c in df.columns]
        df = df[keep].copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.tz_localize(None)
        df = df.dropna(subset=["close"])
        df = _validate_and_sort(df, symbol)
        return df.tail(count).reset_index(drop=True)


def source_label() -> str:
    """当前数据源配置标签（缓存键的一部分）：tickflow/baostock/akshare/yfinance/auto。"""
    forced = get_env_config().data_source
    return forced if forced in ("tickflow", "baostock", "akshare", "yfinance") else "auto"


def get_sources() -> list[DataSource]:
    """按环境变量返回数据源链（顺序即优先级）。

    缺省 auto 模式：TickFlow → baostock → akshare → yfinance
    （A 股走前三级；港股/美股由 yfinance 兜底，supports 自动分流）。
    """
    label = source_label()
    if label == "tickflow":
        return [TickFlowSource()]
    if label == "baostock":
        return [BaostockSource()]
    if label == "akshare":
        return [AkshareSource()]
    if label == "yfinance":
        return [YFinanceSource()]
    return [TickFlowSource(), BaostockSource(), AkshareSource(), YFinanceSource()]
