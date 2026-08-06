"""OHLCV 数据质量校验：脏数据在流入回测前显式暴露。

动机：原 ``sources._validate_and_sort`` 只检查「非空 + 有 close 列」，
重复交易日、NaN、零/负价、``high < low``、未复权拆股造成的巨幅跳空、
交易日缺口都会静默流进回测——绩效指标全部失真却没有任何告警。
对量化平台而言这是最大的质量风险敞口。

本模块提供纯函数式校验，不修改数据、不抛异常，只产出结构化报告，
由调用方决定告警还是中断（见 ``sources._validate_and_sort``）：

- ``error`` 级：数据本身自相矛盾，几乎必然是数据源问题
  （重复日期 / NaN / 非正价 / OHLC 关系不成立）；
- ``warn`` 级：可能合理也可能异常，需人工判断
  （极端跳空 / 交易日缺口）。

涨跌幅阈值按板块区分（主板 11% / 科创创业 21% / 北交所 31% /
港美股与周月 K 50%），留出缓冲避免正常涨跌停被误报。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .cache import find_date_column

#: 参与校验的价格列
_PRICE_COLS = ("open", "high", "low", "close")

#: 各周期「相邻 K 线间隔超过多少自然日」视为缺口；分钟级不做此校验
_GAP_DAYS = {"1d": 10, "1w": 21, "1M": 70, "1Q": 200, "1Y": 500}

#: 无涨跌幅限制市场（港美股）与周期（周/月 K）的跳空阈值：主要用于识别未复权拆股
_NO_LIMIT_JUMP_PCT = 50.0

#: 报告中 detail 字段最多列举几个样例日期
_SAMPLE_LIMIT = 3


@dataclass(frozen=True)
class QualityIssue:
    """单项质量问题。

    Attributes:
        level: ``error``（数据自相矛盾）或 ``warn``（需人工判断）。
        code: 机器可读的问题类型标识。
        count: 命中的行数。
        detail: 面向用户的说明，含样例日期/数值。
    """

    level: str
    code: str
    count: int
    detail: str


@dataclass
class QualityReport:
    """单标的 OHLCV 质量报告。"""

    symbol: str
    period: str
    rows: int
    issues: list[QualityIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[QualityIssue]:
        """error 级问题清单。"""
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[QualityIssue]:
        """warn 级问题清单。"""
        return [i for i in self.issues if i.level == "warn"]

    @property
    def passed(self) -> bool:
        """无 error 级问题即通过（warn 不影响判定）。"""
        return not self.errors

    def to_dict(self) -> dict:
        """结构化输出（供 CLI 的 ``data_quality`` 字段消费）。"""
        return {
            "symbol": self.symbol,
            "period": self.period,
            "rows": self.rows,
            "passed": self.passed,
            "issues": [
                {
                    "level": i.level,
                    "code": i.code,
                    "count": i.count,
                    "detail": i.detail,
                }
                for i in self.issues
            ]
            or None,
        }

    def summary(self) -> str:
        """一句话摘要（用于 stderr 告警）。"""
        if not self.issues:
            return f"{self.symbol} {self.period} 数据质量校验通过（{self.rows} 行）。"
        parts = [f"{i.code}×{i.count}" for i in self.issues]
        status = "PASS" if self.passed else "FAIL"
        return (
            f"{self.symbol} {self.period} 数据质量 [{status}]"
            f"（{self.rows} 行）：{', '.join(parts)}"
        )


def _jump_threshold_pct(symbol: str, period: str) -> float:
    """按板块与周期返回「单周期涨跌幅超过多少视为异常」的阈值（%）。

    A 股有涨跌停约束，超限必属数据问题；港美股无涨跌停，只有巨幅跳空
    （多为未复权拆股）才值得告警。周/月 K 无单周期约束，统一用宽阈值。
    """
    if period not in ("1d",) and not str(period).endswith("m"):
        return _NO_LIMIT_JUMP_PCT
    sym = symbol.upper()
    code = sym.rsplit(".", 1)[0]
    if sym.endswith(".BJ"):
        return 31.0
    if sym.endswith(".SH"):
        return 21.0 if code.startswith("688") else 11.0
    if sym.endswith(".SZ"):
        return 21.0 if code.startswith(("300", "301")) else 11.0
    return _NO_LIMIT_JUMP_PCT


def _fmt_dates(dates: pd.Series | pd.DatetimeIndex) -> str:
    """取前若干个日期拼成样例串（超出则加省略号）。"""
    items = [str(pd.Timestamp(d).date()) for d in list(dates)[:_SAMPLE_LIMIT]]
    suffix = " 等" if len(dates) > _SAMPLE_LIMIT else ""
    return ", ".join(items) + suffix


def validate_ohlcv(
    df: pd.DataFrame,
    symbol: str,
    period: str = "1d",
) -> QualityReport:
    """校验 OHLCV 数据质量，返回结构化报告（不修改数据、不抛异常）。

    Args:
        df: 待校验的 DataFrame（应已按时间升序、列名归一）。
        symbol: 标的代码（用于按板块选择涨跌幅阈值）。
        period: K 线周期（用于选择缺口与跳空阈值）。

    Returns:
        QualityReport；``passed`` 为 False 表示存在 error 级问题。
    """
    report = QualityReport(symbol=symbol, period=period, rows=int(len(df)))
    if df is None or len(df) == 0:
        report.issues.append(
            QualityIssue("error", "empty", 0, "数据为空，无任何 K 线。")
        )
        return report

    date_col = find_date_column(df)
    dates = None
    if date_col is not None:
        try:
            dates = pd.to_datetime(df[date_col])
        except (ValueError, TypeError):
            report.issues.append(
                QualityIssue(
                    "error",
                    "bad_date_column",
                    len(df),
                    f"时间列 {date_col} 无法解析为日期，跳过时序类校验。",
                )
            )

    _check_duplicate_dates(report, dates)
    _check_price_sanity(report, df, dates)
    _check_ohlc_relations(report, df, dates)
    _check_extreme_jump(report, df, dates, symbol, period)
    _check_date_gap(report, dates, period)
    return report


def _check_duplicate_dates(report: QualityReport, dates: pd.Series | None) -> None:
    """重复交易日：同一日多行会让回测重复计算同一根 K 线。"""
    if dates is None:
        return
    dup_mask = dates.duplicated(keep=False)
    if not dup_mask.any():
        return
    dup_dates = dates[dates.duplicated(keep="first")]
    report.issues.append(
        QualityIssue(
            "error",
            "duplicate_dates",
            int(dup_mask.sum()),
            f"存在重复交易日（{_fmt_dates(dup_dates)}），回测会重复计入同一根 K 线。",
        )
    )


def _check_price_sanity(
    report: QualityReport,
    df: pd.DataFrame,
    dates: pd.Series | None,
) -> None:
    """NaN 与零/负价：两者都会让收益率计算产出 NaN 或 inf。"""
    cols = [c for c in _PRICE_COLS if c in df.columns]
    if not cols:
        return
    prices = df[cols].apply(pd.to_numeric, errors="coerce")

    nan_mask = prices.isna().any(axis=1)
    if nan_mask.any():
        detail = f"OHLC 存在 NaN（列 {', '.join(cols)}）"
        if dates is not None:
            detail += f"，样例日期：{_fmt_dates(dates[nan_mask])}"
        report.issues.append(
            QualityIssue("error", "nan_values", int(nan_mask.sum()), detail + "。")
        )

    nonpos_mask = (prices <= 0).any(axis=1) & ~nan_mask
    if nonpos_mask.any():
        detail = "OHLC 存在零或负价"
        if dates is not None:
            detail += f"，样例日期：{_fmt_dates(dates[nonpos_mask])}"
        report.issues.append(
            QualityIssue(
                "error", "nonpositive_price", int(nonpos_mask.sum()), detail + "。"
            )
        )


def _check_ohlc_relations(
    report: QualityReport,
    df: pd.DataFrame,
    dates: pd.Series | None,
) -> None:
    """OHLC 关系：最高价必须 >= max(开,收) 且 >= 最低价，反之最低价同理。"""
    if not all(c in df.columns for c in _PRICE_COLS):
        return
    p = df[list(_PRICE_COLS)].apply(pd.to_numeric, errors="coerce")
    valid = p.notna().all(axis=1)
    if not valid.any():
        return
    hi, lo = p["high"], p["low"]
    upper = p[["open", "close"]].max(axis=1)
    lower = p[["open", "close"]].min(axis=1)
    # 用相对容差吸收数据源的浮点舍入（万分之一），避免误报
    tol = hi.abs() * 1e-4
    bad = valid & ((hi < lo - tol) | (hi < upper - tol) | (lo > lower + tol))
    if not bad.any():
        return
    detail = "OHLC 关系不成立（high < low 或 high/low 未包住 open/close）"
    if dates is not None:
        detail += f"，样例日期：{_fmt_dates(dates[bad])}"
    report.issues.append(
        QualityIssue("error", "ohlc_inconsistent", int(bad.sum()), detail + "。")
    )


def _check_extreme_jump(
    report: QualityReport,
    df: pd.DataFrame,
    dates: pd.Series | None,
    symbol: str,
    period: str,
) -> None:
    """极端跳空：A 股超涨跌停必属数据问题；港美股巨幅跳空多为未复权拆股。"""
    if "close" not in df.columns or len(df) < 2:
        return
    close = pd.to_numeric(df["close"], errors="coerce")
    ret_pct = (close.pct_change().abs() * 100.0)
    threshold = _jump_threshold_pct(symbol, period)
    bad = ret_pct > threshold
    if not bad.any():
        return
    detail = (
        f"单周期涨跌幅超过 {threshold:.0f}%（最大 {ret_pct.max():.1f}%）"
        f"，疑似未复权拆股或数据源错价"
    )
    if dates is not None:
        detail += f"，样例日期：{_fmt_dates(dates[bad])}"
    report.issues.append(
        QualityIssue("warn", "extreme_jump", int(bad.sum()), detail + "。")
    )


def _check_date_gap(
    report: QualityReport,
    dates: pd.Series | None,
    period: str,
) -> None:
    """交易日缺口：长假属正常，但超过阈值多为数据源漏取整段行情。"""
    max_gap = _GAP_DAYS.get(str(period))
    if dates is None or max_gap is None or len(dates) < 2:
        return
    gaps = dates.diff().dt.days
    bad = gaps > max_gap
    if not bad.any():
        return
    detail = (
        f"相邻 K 线间隔超过 {max_gap} 天（最大 {int(gaps.max())} 天）"
        f"，可能缺失整段行情"
    )
    detail += f"，缺口结束日：{_fmt_dates(dates[bad])}"
    report.issues.append(
        QualityIssue("warn", "date_gap", int(bad.sum()), detail + "。")
    )
