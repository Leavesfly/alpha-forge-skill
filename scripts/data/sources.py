"""数据源抽象：TickFlow 主源 + OpenBB 港美主力 + baostock / akshare / yfinance 多源链。

动机：datafeed 原先绑定 TickFlow 单一数据源，服务不可用时整个链路失效。
本模块把「拉取单标的 K 线」抽象为 ``DataSource`` 协议：

- ``OpenBBSource``：港股/美股主力源，日/周/月 K（Open Data Platform，
  免费无需 Key，可扩展财务/期权/宏观等更多数据类型）；
- ``TickFlowSource``：主源，多市场全周期；
- ``BaostockSource``：二级兜底，仅沪深 A 股日/周/月 K（免费、无需 Key、API 级稳定）；
- ``AkshareSource``：三级兜底，仅 A 股日/周/月 K（免费、无需 Key）；
- ``YFinanceSource``：港股/美股兜底，日/周/月 K（Yahoo Finance，免费、无需 Key）；
- ``get_sources``：按环境变量 ``ALPHA_FORGE_DATA_SOURCE`` 返回数据源链
  （``tickflow`` / ``openbb`` / ``baostock`` / ``akshare`` / ``yfinance`` 强制单源，
  缺省 auto = 港美股 OpenBB 主力＋A 股三级降级＋yfinance 兜底）。

所有源返回列名归一的升序 DataFrame：``trade_date/open/high/low/close/volume``，
并统一经 ``_validate_and_sort`` 做列校验与数据质量校验（见 ``quality.py``）。
"""

from __future__ import annotations

import os
import sys
import threading
from typing import Protocol

import pandas as pd

from envconfig import get_env_config
from errors import DataFetchError, DataQualityError

from .quality import validate_ohlcv

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
        raise DataFetchError(
            f"周期 {period} 需要实时/分钟数据，请先配置环境变量 TICKFLOW_API_KEY 后重试。\n"
            + API_KEY_HELP
        )
    return TickFlow.free()


def _validate_and_sort(
    df: pd.DataFrame,
    symbol: str,
    period: str = "1d",
    tail: int | None = None,
) -> pd.DataFrame:
    """校验必需列、按时间升序、截取尾部并做数据质量校验。

    五个数据源的唯一公共出口，质量校验在此单点接入。

    Args:
        tail: 只保留末尾 N 行。部分源（yfinance/openbb/akshare/baostock）只能
            整段拉取再截取，而质量校验必须只针对**实际返回的行**：
            否则 Yahoo 上世纪的陈年数据（拆股前分钱、OHLC 不自洽）会把当下
            完全干净的 60 根 K 线误判为“数据有问题”（且严格模式下直接报错）。

    Raises:
        DataFetchError: 数据为空或缺少 close 列。
        DataQualityError: 存在 error 级质量问题且启用了严格模式。
    """
    if df is None or len(df) == 0:
        raise DataFetchError(f"未获取到 {symbol} 的 K 线数据，请检查代码与周期。")
    if "close" not in df.columns:
        raise DataFetchError(f"返回数据缺少 close 列，实际列：{list(df.columns)}")
    for col in ("trade_date", "date", "datetime", "time"):
        if col in df.columns:
            df = df.sort_values(col).reset_index(drop=True)
            break
    if tail is not None and tail > 0 and tail < len(df):
        df = df.tail(tail).reset_index(drop=True)
    return _check_quality(df, symbol, period)


def _check_quality(df: pd.DataFrame, symbol: str, period: str) -> pd.DataFrame:
    """执行质量校验：默认告警放行，严格模式抛错，可完全关闭。

    报告挂在 ``df.attrs["quality"]`` 供 CLI 层写入 JSON 的 ``data_quality``
    字段，不改变任何函数签名。注意：缓存命中路径不经过本函数，
    因此无新鲜报告（那份数据在首次落盘时已校验过）。
    """
    env = get_env_config()
    if env.no_quality_check:
        return df

    report = validate_ohlcv(df, symbol, period)
    if report.issues:
        print(f"[warn] {report.summary()}", file=sys.stderr)
        for issue in report.issues:
            print(f"       - [{issue.level}] {issue.detail}", file=sys.stderr)
    if not report.passed and env.strict_data:
        raise DataQualityError(
            f"{report.summary()}\n"
            "已启用严格模式（ALPHA_FORGE_STRICT_DATA=1），数据不可用。可选：\n"
            "  ① 取消该环境变量降级为仅告警；\n"
            "  ② 用 ALPHA_FORGE_DATA_SOURCE 指定其他数据源重试；\n"
            "  ③ 用 run_verify.py 对比多源数据确认哪个源有问题。"
        )
    df.attrs["quality"] = report
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
        return _validate_and_sort(df, symbol, period)


# ─── baostock ──────────────────────────────────────────────────────────────────

#: baostock 周期映射：本项目周期 -> bs frequency 参数
_BS_PERIODS = {"1d": "d", "1w": "w", "1M": "m"}

#: 复权口径映射：归一化口径 -> bs adjustflag（"2"=前复权, "1"=后复权, "3"=不复权）
_BS_ADJUSTS = {"forward": "2", "backward": "1", "none": "3"}

#: baostock 市场前缀映射
_BS_MARKET = {"SH": "sh", "SZ": "sz"}

#: baostock 串行锁：baostock 的 login/logout 操作的是**模块级全局连接**，
#: 并非每个调用方一个会话。sync.py 默认 2 线程、run_sync.py 还支持
#: --workers 4，若不串行，A 线程的 logout() 会掐断 B 线程正在进行的查询，
#: 表现为随机失败且被降级逻辑吞掉（表面上只看到“已降级使用 akshare”）。
_BAOSTOCK_LOCK = threading.Lock()


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

        # 全局连接必须串行：login → query → logout 整段持锁
        with _BAOSTOCK_LOCK:
            lg = bs.login()
            if lg.error_code != "0":
                raise DataFetchError(f"baostock 登录失败：{lg.error_msg}")
            try:
                rs = bs.query_history_k_data_plus(
                    bs_code,
                    "date,open,high,low,close,volume",
                    frequency=_BS_PERIODS[period],
                    adjustflag=_BS_ADJUSTS.get(adjust, "2"),
                )
                if rs.error_code != "0":
                    raise DataFetchError(f"baostock 查询 {symbol} 失败：{rs.error_msg}")
                rows = []
                while rs.next():
                    rows.append(rs.get_row_data())
            finally:
                bs.logout()

        if not rows:
            raise DataFetchError(f"baostock 未返回 {symbol} 的 K 线数据。")

        df = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "volume"])
        # baostock 返回字符串，需转数值
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        # 停牌日 volume 为空字符串 -> NaN，填 0
        df["volume"] = df["volume"].fillna(0)
        return _validate_and_sort(df, symbol, period, tail=count)


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
            raise DataFetchError(f"akshare 未返回 {symbol} 的 K 线数据。")
        df = df.rename(columns=_AK_COLUMNS)
        keep = [c for c in ("trade_date", "open", "high", "low", "close", "volume") if c in df.columns]
        df = df[keep].copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        return _validate_and_sort(df, symbol, period, tail=count)


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
            raise DataFetchError(
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
            raise DataFetchError(f"yfinance 未返回 {symbol}（{ticker}）的 K 线数据。")
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
        return _validate_and_sort(df, symbol, period, tail=count)


# ─── OpenBB ──────────────────────────────────────────────────────────────────────

#: OpenBB 周期映射：本项目周期 -> openbb yfinance provider interval
_OBB_PERIODS = {"1d": "1d", "1w": "1W", "1M": "1M"}

#: 复权口径映射：归一化口径 -> openbb yfinance provider adjustment 参数
#: forward -> 拆股+分红全调整（涨跌幅与前复权一致，回测可用）；none -> 仅拆股
_OBB_ADJUSTS = {"forward": "splits_and_dividends", "none": "splits_only"}

#: 估算拉取起始日的历日天数系数：count 根 K 线需回溯多少天（含休市日余量）
_OBB_CALENDAR_FACTOR = {"1d": 2, "1w": 9, "1M": 33}


class OpenBBSource:
    """OpenBB 港股/美股主力数据源：日/周/月 K（Open Data Platform，免费无需 Key）。

    经 openbb 的 yfinance provider 拉取，相比直连 yfinance 提供统一 schema，
    且后续可平滑扩展财务、期权、宏观等更多数据类型。
    复权口径：forward -> splits_and_dividends；none -> splits_only；
    backward 不支持（fetch 报错，交给其他源）。

    注意：openbb 内部走 anyio portal，anyio<4 会导致所有请求报
    "This portal is not running"，项目依赖已约束 anyio>=4.0。
    """

    name = "openbb"

    def supports(self, symbol: str, period: str) -> bool:
        if not symbol.upper().endswith(_YF_SUFFIXES) or period not in _OBB_PERIODS:
            return False
        code = symbol.rsplit(".", 1)[0]
        # 港股代码必须是纯数字才能映射到 Yahoo 格式
        if symbol.upper().endswith(".HK") and not code.isdigit():
            return False
        return True

    def fetch(self, symbol: str, period: str, count: int, adjust: str) -> pd.DataFrame:
        if adjust == "backward":
            raise DataFetchError(
                "openbb 源不支持后复权（hfq），请改用前复权或配置 TICKFLOW_API_KEY。"
            )
        from openbb import obb

        ticker = _to_yahoo_symbol(symbol)
        # 按 count 估算起始日（留余量），尾部 tail(count) 截取精确根数
        days = count * _OBB_CALENDAR_FACTOR[period] + 30
        start = (pd.Timestamp.now() - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
        out = obb.equity.price.historical(
            ticker,
            interval=_OBB_PERIODS[period],
            start_date=start,
            provider="yfinance",
            adjustment=_OBB_ADJUSTS.get(adjust, "splits_and_dividends"),
        )
        df = out.to_dataframe()
        if df is None or len(df) == 0:
            raise DataFetchError(f"openbb 未返回 {symbol}（{ticker}）的 K 线数据。")
        # 索引为 date，列含 open/high/low/close/volume（及 dividend 等额外列）
        df = df.reset_index().rename(columns={"date": "trade_date"})
        keep = [c for c in ("trade_date", "open", "high", "low", "close", "volume") if c in df.columns]
        df = df[keep].copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        if getattr(df["trade_date"].dt, "tz", None) is not None:
            df["trade_date"] = df["trade_date"].dt.tz_localize(None)
        df = df.dropna(subset=["close"])
        return _validate_and_sort(df, symbol, period, tail=count)


def source_label() -> str:
    """当前数据源配置标签（缓存键的一部分）：tickflow/openbb/baostock/akshare/yfinance/auto。"""
    forced = get_env_config().data_source
    return (
        forced
        if forced in ("tickflow", "openbb", "baostock", "akshare", "yfinance")
        else "auto"
    )


def get_sources() -> list[DataSource]:
    """按环境变量返回数据源链（顺序即优先级）。

    缺省 auto 模式：OpenBB → TickFlow → baostock → akshare → yfinance
    （港股/美股 OpenBB 主力、TickFlow/yfinance 兜底；A 股 supports 自动跳过
    OpenBB，仍走 TickFlow → baostock → akshare 三级降级）。
    """
    label = source_label()
    if label == "tickflow":
        return [TickFlowSource()]
    if label == "openbb":
        return [OpenBBSource()]
    if label == "baostock":
        return [BaostockSource()]
    if label == "akshare":
        return [AkshareSource()]
    if label == "yfinance":
        return [YFinanceSource()]
    return [
        OpenBBSource(),
        TickFlowSource(),
        BaostockSource(),
        AkshareSource(),
        YFinanceSource(),
    ]
