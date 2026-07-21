"""CAN SLIM 七项检查清单引擎（欧奈尔成长股法则）。

CAN SLIM 是威廉·欧奈尔在《笑傲股市》中提出的成长股选股法则，
本模块把七个字母落地为**可核查的纪律检查项**（与 scoring 四层评分同属
纪律过滤，不是收益预测）：

- **C** 当季 EPS 同比增长 ≥ 25%（单季拆分，基数非正时「扭亏为盈」视为通过）；
- **A** 年度 EPS 复合增长 ≥ 25%（近 3~4 个完整年度，ROE ≥ 17% 作为质量注记）；
- **N** 新高：收盘距 52 周（250 日）高点 15% 以内（买强不买弱）；
- **S** 供求：近 50 日上涨日均量 / 下跌日均量 ≥ 1.0（吸筹重于派发）；
- **L** 龙头：加权相对强度（3/6/9/12 个月，近端加倍）跑赢基准；
  多标的横截面扫描时改用 RS 百分位 ≥ 70；
- **I** 机构认同：公开数据源不可得，诚实标注 unavailable（不猜测）；
- **M** 大势：基准收盘站上 MA50 与 MA200 才算确认上行，否决项。

结论纪律（单向、利好不救场）：M 不过直接「否」；有 2 项及以上失败「否」；
C/A 基本面缺失时结论封顶「观察」（没有基本面就不是完整的 CAN SLIM）。
阈值为欧奈尔原著预设，未经 A 股样本外验证，可用 CLI 阈值参数本土化。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from utils import resolve_time_index, safe_round, series_last

#: 结论三态（复用 scoring 的机器码约定，另有 unrated）
VERDICT_CN = {
    "yes": "是",
    "watch": "观察",
    "no": "否",
    "unrated": "无法评分",
}

#: 七个字母的中文名（固定输出顺序）
LETTERS_CN = {
    "C": "当季EPS增长",
    "A": "年度EPS复合增长",
    "N": "新高（距52周高点）",
    "S": "供求（量能配合）",
    "L": "龙头（相对强度）",
    "I": "机构认同",
    "M": "市场方向",
}

#: 有效检查所需最少 K 线数（52 周高点 + 12 个月相对强度）
MIN_BARS = 260

#: 欧奈尔原著阈值（CLI 可覆盖）
C_EPS_YOY_MIN = 0.25
A_EPS_CAGR_MIN = 0.25
A_ROE_MIN = 0.17
N_FROM_HIGH_MAX = 0.15
S_UPDOWN_VOL_MIN = 1.0
L_RS_PERCENTILE_MIN = 0.70

#: 相对强度加权窗口：近 3 个月权重加倍（IBD RS 近似）
RS_WEIGHTS = ((63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2))


@dataclass
class CanSlimResult:
    """单标的 CAN SLIM 检查结果。"""

    symbol: str
    verdict: str  # yes / watch / no / unrated
    checks: list[dict]  # 七项 {letter, name, status, value, threshold, reasons}
    passed: int
    failed: int
    unavailable: int
    rs_raw: float | None  # 加权相对强度原始值（横截面排名用）
    snapshot: dict
    asof: str
    n_bars: int
    notes: list[str] = field(default_factory=list)  # 降级/口径说明

    @property
    def verdict_cn(self) -> str:
        return VERDICT_CN[self.verdict]

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "verdict": self.verdict,
            "verdict_cn": self.verdict_cn,
            "checks": self.checks,
            "passed": self.passed,
            "failed": self.failed,
            "unavailable": self.unavailable,
            "rs_raw": self.rs_raw,
            "snapshot": self.snapshot,
            "asof": self.asof,
            "n_bars": self.n_bars,
            "notes": self.notes,
        }


def canslim_check(
    df: pd.DataFrame,
    symbol: str = "",
    benchmark_close: pd.Series | None = None,
    fundamentals: dict | None = None,
    rs_percentile: float | None = None,
    c_growth: float = C_EPS_YOY_MIN,
    a_growth: float = A_EPS_CAGR_MIN,
    roe_min: float = A_ROE_MIN,
) -> CanSlimResult:
    """对单标的执行 CAN SLIM 七项检查。

    Args:
        df: 含 ``close``（可选 high/volume）的 OHLCV DataFrame（时间升序）。
        symbol: 标的代码（展示用）。
        benchmark_close: 基准收盘价序列（L 相对强度与 M 大势判断依赖）。
        fundamentals: 基本面数据 ``{"eps": Series, "roe": Series}``，
            eps 为报告期累计（YTD）每股收益、roe 为报告期净资产收益率
            （小数），索引均为报告期末 DatetimeIndex；None 时 C/A 标注
            unavailable 并把结论封顶「观察」。
        rs_percentile: 横截面 RS 百分位（0~1，扫描模式传入）；None 时
            L 退化为「与基准比加权相对强度」。
        c_growth / a_growth / roe_min: 阈值本土化入口（默认欧奈尔原著值）。

    Returns:
        CanSlimResult。仅使用截至最近一根已完成 K 线的数据，无前视。
    """
    close = df["close"].astype(float).reset_index(drop=True)
    index = resolve_time_index(df)
    close.index = index
    asof = str(index[-1])[:10] if len(index) else ""
    n = int(close.notna().sum())

    if n < MIN_BARS:
        return CanSlimResult(
            symbol=symbol,
            verdict="unrated",
            checks=[],
            passed=0,
            failed=0,
            unavailable=len(LETTERS_CN),
            rs_raw=None,
            snapshot={"close": series_last(close), "n_bars": n},
            asof=asof,
            n_bars=n,
            notes=[f"有效 K 线仅 {n} 根，低于检查所需 {MIN_BARS} 根（52 周高点/12 个月 RS 无法形成）"],
        )

    volume = df["volume"].astype(float).reset_index(drop=True) if "volume" in df.columns else None
    notes: list[str] = []
    checks: list[dict] = []
    snapshot: dict = {"close": series_last(close)}

    asof_ts = index[-1] if isinstance(index, pd.DatetimeIndex) else None
    checks.append(_check_c(fundamentals, c_growth, asof_ts, notes))
    checks.append(_check_a(fundamentals, a_growth, roe_min, asof_ts, notes))
    checks.append(_check_n(close, snapshot))
    checks.append(_check_s(close, volume, snapshot))
    rs_raw = rs_weighted_return(close)
    checks.append(_check_l(close, benchmark_close, rs_raw, rs_percentile, snapshot, notes))
    checks.append(_check_i())
    checks.append(_check_m(benchmark_close, snapshot, notes))

    passed = sum(1 for c in checks if c["status"] == "pass")
    failed = sum(1 for c in checks if c["status"] == "fail")
    unavailable = sum(1 for c in checks if c["status"] == "unavailable")
    verdict = _decide(checks, failed, notes)

    return CanSlimResult(
        symbol=symbol,
        verdict=verdict,
        checks=checks,
        passed=passed,
        failed=failed,
        unavailable=unavailable,
        rs_raw=round(rs_raw, 4) if rs_raw is not None else None,
        snapshot={k: safe_round(v) for k, v in snapshot.items()},
        asof=asof,
        n_bars=n,
        notes=notes,
    )


def rs_weighted_return(close: pd.Series) -> float | None:
    """加权相对强度原始值：3/6/9/12 个月收益按 0.4/0.2/0.2/0.2 加权。"""
    close = close.dropna()
    if len(close) < RS_WEIGHTS[-1][0] + 1:
        return None
    total = 0.0
    for window, weight in RS_WEIGHTS:
        total += weight * (float(close.iloc[-1] / close.iloc[-window - 1]) - 1.0)
    return total


# ---------------------------------------------------------------- 各字母实现


def _check_c(fundamentals: dict | None, threshold: float, asof_ts, notes: list[str]) -> dict:
    """C：最近报告期单季 EPS 同比增速 ≥ threshold；基数非正时扭亏视为通过。"""
    base = {"letter": "C", "name": LETTERS_CN["C"], "threshold": threshold}
    eps_q = _quarterly_eps(fundamentals, asof_ts)
    if eps_q is None or len(eps_q) < 5:
        notes.append("C：无可用季度 EPS 数据，跳过（结论封顶「观察」）")
        return {**base, "status": "unavailable", "value": None,
                "reasons": ["无可用季度 EPS（非 A 股或数据源不可达），无法核查当季增长"]}

    cur_period, cur = eps_q.index[-1], float(eps_q.iloc[-1])
    prev_mask = (eps_q.index.month == cur_period.month) & (eps_q.index.year == cur_period.year - 1)
    if not prev_mask.any():
        return {**base, "status": "unavailable", "value": None,
                "reasons": [f"缺少 {cur_period.year - 1} 年同期单季 EPS，无法计算同比"]}
    prev = float(eps_q[prev_mask].iloc[-1])
    tag = f"{cur_period.year}Q{(cur_period.month - 1) // 3 + 1}"

    if prev <= 0:
        if cur > 0:
            return {**base, "status": "pass", "value": None,
                    "reasons": [f"{tag} 单季 EPS {cur:.3f}（去年同期 {prev:.3f}），扭亏为盈视为通过"]}
        return {**base, "status": "fail", "value": None,
                "reasons": [f"{tag} 单季 EPS {cur:.3f} 仍未转正（去年同期 {prev:.3f}）"]}
    yoy = cur / prev - 1.0
    status = "pass" if yoy >= threshold else "fail"
    cmp = "≥" if status == "pass" else "<"
    return {**base, "status": status, "value": round(yoy, 4),
            "reasons": [f"{tag} 单季 EPS {cur:.3f}，同比 {yoy * 100:+.1f}% {cmp} 阈值 {threshold * 100:.0f}%"]}


def _check_a(
    fundamentals: dict | None, threshold: float, roe_min: float, asof_ts, notes: list[str]
) -> dict:
    """A：近 3~4 个完整年度 EPS 复合增速 ≥ threshold；ROE 达标与否写入注记。"""
    base = {"letter": "A", "name": LETTERS_CN["A"], "threshold": threshold}
    annual = _annual_eps(fundamentals, asof_ts)
    if annual is None or len(annual) < 3:
        notes.append("A：完整年度 EPS 不足 3 年，跳过（结论封顶「观察」）")
        return {**base, "status": "unavailable", "value": None,
                "reasons": ["完整年度 EPS 不足 3 年（或数据不可达），无法核查持续增长"]}

    annual = annual.iloc[-4:]
    first, last = float(annual.iloc[0]), float(annual.iloc[-1])
    span = len(annual) - 1
    reasons: list[str] = []
    if first <= 0 or last <= 0:
        status = "fail"
        value = None
        reasons.append(
            f"年度 EPS 存在非正值（{annual.index[0].year} 年 {first:.3f} → "
            f"{annual.index[-1].year} 年 {last:.3f}），无法确认持续增长"
        )
    else:
        cagr = (last / first) ** (1.0 / span) - 1.0
        status = "pass" if cagr >= threshold else "fail"
        value = round(cagr, 4)
        cmp = "≥" if status == "pass" else "<"
        reasons.append(
            f"{annual.index[0].year}→{annual.index[-1].year} 年 EPS "
            f"{first:.3f}→{last:.3f}，复合增速 {cagr * 100:+.1f}% {cmp} 阈值 {threshold * 100:.0f}%"
        )

    roe = _latest_roe(fundamentals, asof_ts)
    if roe is not None:
        mark = "达标（质量佳）" if roe >= roe_min else f"低于 {roe_min * 100:.0f}%，质量偏弱"
        reasons.append(f"最新 ROE {roe * 100:.1f}%：{mark}（注记项，不单独否决）")
    return {**base, "status": status, "value": value, "reasons": reasons}


def _check_n(close: pd.Series, snapshot: dict) -> dict:
    """N：收盘距 52 周（250 日）高点 15% 以内——买强不买弱。"""
    base = {"letter": "N", "name": LETTERS_CN["N"], "threshold": N_FROM_HIGH_MAX}
    high250 = float(close.rolling(250).max().iloc[-1])
    last = float(close.iloc[-1])
    from_high = 1.0 - last / high250 if high250 > 0 else float("nan")
    snapshot["high52w"] = high250
    snapshot["from_high"] = from_high
    if math.isnan(from_high):
        return {**base, "status": "unavailable", "value": None, "reasons": ["52 周高点无效，无法判断"]}
    if from_high <= N_FROM_HIGH_MAX:
        tag = "创出新高" if from_high <= 1e-9 else f"距 52 周高点 {from_high * 100:.1f}%"
        return {**base, "status": "pass", "value": round(from_high, 4),
                "reasons": [f"收盘 {last:.2f}，{tag}（≤15%），处于强势区"]}
    return {**base, "status": "fail", "value": round(from_high, 4),
            "reasons": [f"收盘 {last:.2f} 距 52 周高点 {high250:.2f} 达 {from_high * 100:.1f}%（>15%），远离强势区"]}


def _check_s(close: pd.Series, volume: pd.Series | None, snapshot: dict) -> dict:
    """S：近 50 日上涨日均量 / 下跌日均量 ≥ 1.0（吸筹重于派发）。"""
    base = {"letter": "S", "name": LETTERS_CN["S"], "threshold": S_UPDOWN_VOL_MIN}
    if volume is None or float(volume.tail(50).sum()) <= 0:
        return {**base, "status": "unavailable", "value": None,
                "reasons": ["无成交量数据，无法核查量能供求"]}
    chg = close.reset_index(drop=True).diff().tail(50)
    vol = volume.tail(50).reset_index(drop=True)
    chg = chg.reset_index(drop=True)
    up_vol = float(vol[chg > 0].mean()) if (chg > 0).any() else 0.0
    down_vol = float(vol[chg < 0].mean()) if (chg < 0).any() else 0.0
    if down_vol <= 0:
        ratio = float("inf") if up_vol > 0 else 0.0
    else:
        ratio = up_vol / down_vol
    snapshot["updown_vol_ratio"] = None if math.isinf(ratio) else ratio
    status = "pass" if ratio >= S_UPDOWN_VOL_MIN else "fail"
    ratio_str = "∞" if math.isinf(ratio) else f"{ratio:.2f}"
    verb = "上涨日放量、下跌日缩量（吸筹特征）" if status == "pass" else "下跌日量能占优（派发特征）"
    return {**base, "status": status,
            "value": None if math.isinf(ratio) else round(ratio, 4),
            "reasons": [f"近 50 日上涨日均量/下跌日均量 = {ratio_str}，{verb}"]}


def _check_l(
    close: pd.Series,
    benchmark_close: pd.Series | None,
    rs_raw: float | None,
    rs_percentile: float | None,
    snapshot: dict,
    notes: list[str],
) -> dict:
    """L：横截面模式用 RS 百分位 ≥ 70；单标的退化为加权相对强度跑赢基准。"""
    base = {"letter": "L", "name": LETTERS_CN["L"]}
    if rs_percentile is not None:
        snapshot["rs_percentile"] = rs_percentile
        status = "pass" if rs_percentile >= L_RS_PERCENTILE_MIN else "fail"
        cmp = "≥" if status == "pass" else "<"
        return {**base, "status": status, "threshold": L_RS_PERCENTILE_MIN,
                "value": round(rs_percentile, 4),
                "reasons": [f"横截面 RS 百分位 {rs_percentile * 100:.0f} {cmp} {L_RS_PERCENTILE_MIN * 100:.0f}（欧奈尔标准 RS≥80 对应强势前 20%）"]}

    if rs_raw is None or benchmark_close is None:
        notes.append("L：无基准且非横截面扫描，相对强度无法核查")
        return {**base, "status": "unavailable", "threshold": 0.0, "value": None,
                "reasons": ["无可用基准（且非多标的扫描），无法计算相对强度"]}
    bench_rs = rs_weighted_return(benchmark_close.dropna().astype(float))
    if bench_rs is None:
        return {**base, "status": "unavailable", "threshold": 0.0, "value": None,
                "reasons": ["基准样本不足 12 个月，无法计算相对强度"]}
    excess = rs_raw - bench_rs
    snapshot["rs_excess"] = excess
    status = "pass" if excess > 0 else "fail"
    verb = "跑赢基准（相对强势）" if status == "pass" else "跑输基准（弱于大盘，非龙头）"
    return {**base, "status": status, "threshold": 0.0, "value": round(excess, 4),
            "reasons": [f"加权相对强度 {rs_raw * 100:+.1f}% vs 基准 {bench_rs * 100:+.1f}%，超额 {excess * 100:+.1f}%，{verb}"]}


def _check_i() -> dict:
    """I：机构持仓数据公开源不可得，诚实标注 unavailable。"""
    return {
        "letter": "I", "name": LETTERS_CN["I"], "status": "unavailable",
        "threshold": None, "value": None,
        "reasons": ["机构持仓明细无免费数据源，不猜测；可人工核查十大流通股东/基金重仓变化"],
    }


def _check_m(benchmark_close: pd.Series | None, snapshot: dict, notes: list[str]) -> dict:
    """M：基准收盘站上 MA50 与 MA200 才算确认上行；否决项。"""
    base = {"letter": "M", "name": LETTERS_CN["M"], "threshold": None}
    if benchmark_close is None or len(benchmark_close.dropna()) < 200:
        notes.append("M：基准数据不足，无法判断大势（结论封顶「观察」）")
        return {**base, "status": "unavailable", "value": None,
                "reasons": ["无基准或基准样本不足 200 根，无法判断市场方向"]}
    bench = benchmark_close.dropna().astype(float)
    last = float(bench.iloc[-1])
    ma50 = float(bench.rolling(50).mean().iloc[-1])
    ma200 = float(bench.rolling(200).mean().iloc[-1])
    snapshot["bench_close"] = last
    snapshot["bench_ma50"] = ma50
    snapshot["bench_ma200"] = ma200
    if last > ma200 and last > ma50:
        return {**base, "status": "pass", "value": None,
                "reasons": [f"基准收盘 {last:.2f} 站上 MA50 {ma50:.2f} 与 MA200 {ma200:.2f}，市场处于确认上行"]}
    if last > ma200:
        return {**base, "status": "fail", "value": None,
                "reasons": [f"基准收盘 {last:.2f} 跌破 MA50 {ma50:.2f}（仍在 MA200 上），市场处于调整，M 不满足"]}
    return {**base, "status": "fail", "value": None,
            "reasons": [f"基准收盘 {last:.2f} 低于 MA200 {ma200:.2f}，市场下行趋势，约 3/4 个股随大盘走弱"]}


def _decide(checks: list[dict], failed: int, notes: list[str]) -> str:
    """结论纪律：M 不过直接「否」；失败 ≥2「否」；C/A 缺失封顶「观察」。"""
    by_letter = {c["letter"]: c for c in checks}
    if by_letter["M"]["status"] == "fail":
        notes.append("结论：M（市场方向）不满足——欧奈尔纪律为大势不对不买，直接「否」")
        return "no"
    if failed >= 2:
        return "no"
    fundamentals_blind = (
        by_letter["C"]["status"] == "unavailable" and by_letter["A"]["status"] == "unavailable"
    )
    if failed == 0:
        if fundamentals_blind or by_letter["M"]["status"] == "unavailable":
            return "watch"
        return "yes"
    return "watch"


# ---------------------------------------------------------------- 基本面口径


def _cutoff(series: pd.Series | None, asof_ts) -> pd.Series | None:
    """截断到评估日之前的报告期（规避未来财报的前视）。"""
    if series is None:
        return None
    s = series.dropna().sort_index()
    if asof_ts is not None and isinstance(s.index, pd.DatetimeIndex):
        s = s[s.index <= asof_ts]
    return s if len(s) else None


def _quarterly_eps(fundamentals: dict | None, asof_ts) -> pd.Series | None:
    """单季 EPS 序列：优先用预拆分的 ``eps_quarterly``（如 yfinance 港美股），
    否则把累计（YTD）EPS 拆为单季（Q1 即累计值本身，A 股口径）。

    报告期存在缺口时（如只有 Q1 与年报）该期不参与拆分，
    避免把多个季度的增量冒充单季值。
    """
    if not fundamentals:
        return None
    pre_split = _cutoff(fundamentals.get("eps_quarterly"), asof_ts)
    if pre_split is not None and isinstance(pre_split.index, pd.DatetimeIndex):
        return pre_split
    ytd = _cutoff(fundamentals.get("eps"), asof_ts)
    if ytd is None or not isinstance(ytd.index, pd.DatetimeIndex):
        return None
    single: dict = {}
    for ts, val in ytd.items():
        if ts.month == 3:  # Q1 累计即单季
            single[ts] = float(val)
            continue
        prev_q = ytd[(ytd.index.year == ts.year) & (ytd.index.month == ts.month - 3)]
        if not len(prev_q):  # 上一季度缺失：无法拆单季，诚实跳过
            continue
        single[ts] = float(val) - float(prev_q.iloc[-1])
    if not single:
        return None
    return pd.Series(single).sort_index()


def _annual_eps(fundamentals: dict | None, asof_ts) -> pd.Series | None:
    """完整年度 EPS：优先用预拆分的 ``eps_annual``（财年口径，如 yfinance），
    否则取累计序列每年最后一个报告期（月份 =12）的累计值。"""
    if not fundamentals:
        return None
    pre_split = _cutoff(fundamentals.get("eps_annual"), asof_ts)
    if pre_split is not None and isinstance(pre_split.index, pd.DatetimeIndex):
        return pre_split
    ytd = _cutoff(fundamentals.get("eps"), asof_ts)
    if ytd is None or not isinstance(ytd.index, pd.DatetimeIndex):
        return None
    annual = ytd[ytd.index.month == 12]
    return annual if len(annual) else None


def _latest_roe(fundamentals: dict | None, asof_ts) -> float | None:
    """最新报告期 ROE（小数）；不可用返回 None。"""
    if not fundamentals:
        return None
    roe = _cutoff(fundamentals.get("roe"), asof_ts)
    if roe is None:
        return None
    return float(roe.iloc[-1])

