"""宏观数据获取与宏观 regime 上下文（国债利率 / CPI / PMI）。

定位：现有 ``research/regime.py`` 的市场状态识别基于价格（效率比 + 波动率），
是纯技术面视角。本模块补充**宏观面上下文**：

- **10 年期国债收益率**：利率趋势（上行 → 资金收紧 / 下行 → 宽松预期）；
- **CPI 同比**：通胀压力（>3% 偏高 / <0 通缩风险）；
- **PMI**：经济景气（>50 扩张 / <50 收缩）。

三者组合给出宏观 regime 标签（描述性上下文，不参与评分裁决）：

- 扩张（PMI>50 + 利率上行）→ 经济过热，注意估值压力
- 宽松（PMI>50 + 利率下行）→ 最有利环境
- 滞胀（PMI<50 + 利率上行）→ 最不利环境
- 收缩（PMI<50 + 利率下行）→ 衰退预期，等待政策转向

数据源：akshare 免费接口（无需 API Key），仅覆盖中国宏观数据。
港美股标的同样参考中国宏观（A 股/港股直接相关；美股间接参考）。

所有接口异常返回 None（调用方跳过宏观上下文，不中断评分流程）。
"""

from __future__ import annotations

import contextlib
import sys
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class MacroSnapshot:
    """宏观数据快照。"""

    bond_yield_10y: float | None = None      # 10 年期国债收益率(%)
    bond_yield_trend: str | None = None      # rising / falling / flat
    cpi_yoy: float | None = None             # CPI 同比(%)
    cpi_trend: str | None = None             # rising / falling / flat
    pmi: float | None = None                 # 制造业 PMI
    pmi_trend: str | None = None             # rising / falling / flat
    asof: str = ""                           # 数据截止日期
    source: str = "akshare"
    errors: list[str] = field(default_factory=list)  # 拉取失败的指标

    def to_dict(self) -> dict:
        return {
            "bond_yield_10y": _safe_round(self.bond_yield_10y),
            "bond_yield_trend": self.bond_yield_trend,
            "cpi_yoy": _safe_round(self.cpi_yoy),
            "cpi_trend": self.cpi_trend,
            "pmi": _safe_round(self.pmi),
            "pmi_trend": self.pmi_trend,
            "asof": self.asof or None,
            "source": self.source,
            "errors": self.errors or None,
        }


@dataclass
class MacroRegime:
    """宏观 regime 上下文（描述性，不参与评分裁决）。"""

    label: str                # expansion / easing / stagflation / contraction / unknown
    label_cn: str             # 中文标签
    advice: str               # 一句话建议
    snapshot: MacroSnapshot   # 底层数据快照
    components: dict = field(default_factory=dict)  # 各指标判断明细

    def to_dict(self) -> dict:
        return {
            "macro_regime": self.label,
            "macro_regime_cn": self.label_cn,
            "macro_advice": self.advice,
            "macro_snapshot": self.snapshot.to_dict(),
            "macro_components": self.components,
        }


#: 宏观 regime 中文标签
MACRO_REGIME_CN = {
    "expansion": "经济扩张",
    "easing": "宽松有利",
    "stagflation": "滞胀压力",
    "contraction": "收缩衰退",
    "unknown": "数据不足",
}

#: 各 regime 的建议
_MACRO_ADVICE = {
    "expansion": "经济扩张期，顺周期板块受益，但注意利率上行对高估值成长股的压制",
    "easing": "宽松环境最有利，流动性充裕支撑估值，可适度积极",
    "stagflation": "滞胀环境最不利，股债双杀风险，优先防守降仓",
    "contraction": "衰退预期中等待政策转向信号，防御板块相对抗跌",
    "unknown": "宏观数据不足，无法判断宏观环境",
}


def _safe_round(v, ndigits: int = 2):
    if v is None:
        return None
    try:
        import math
        if math.isnan(float(v)):
            return None
        return round(float(v), ndigits)
    except (TypeError, ValueError):
        return None


def _trend_label(series: pd.Series, lookback: int = 3) -> str | None:
    """判断序列近 N 期的趋势方向。"""
    s = series.dropna()
    if len(s) < lookback + 1:
        return None
    recent = s.iloc[-lookback:]
    first = float(recent.iloc[0])
    last = float(recent.iloc[-1])
    if first == 0:
        return "flat"
    change_pct = (last - first) / abs(first)
    if change_pct > 0.02:
        return "rising"
    if change_pct < -0.02:
        return "falling"
    return "flat"


# ---------------------------------------------------------------------------
# 数据拉取
# ---------------------------------------------------------------------------


def _fetch_bond_yield() -> tuple[float | None, pd.Series | None, str]:
    """拉取中国 10 年期国债收益率。

    优先 ``ak.bond_zh_us_rate()``（含中美国债收益率），
    失败时尝试 ``ak.macro_china_bond_yield()``。

    Returns:
        (最新值, 历史序列, 错误信息)
    """
    try:
        import akshare as ak

        with contextlib.redirect_stdout(sys.stderr):
            df = ak.bond_zh_us_rate()
        if df is not None and len(df) > 0:
            # 列名：日期, 中国国债收益率10年, ...
            date_col = None
            yield_col = None
            for c in df.columns:
                cl = str(c).lower()
                if "日期" in cl or "date" in cl:
                    date_col = c
                if "中国" in cl and "10" in cl:
                    yield_col = c
            if date_col and yield_col:
                df = df.copy()
                df["_date"] = pd.to_datetime(df[date_col], errors="coerce")
                df["_yield"] = pd.to_numeric(df[yield_col], errors="coerce")
                df = df.dropna(subset=["_date", "_yield"]).sort_values("_date")
                if len(df) > 0:
                    return float(df["_yield"].iloc[-1]), df["_yield"], ""
        return None, None, "bond_zh_us_rate 返回数据格式异常"
    except Exception as exc:
        return None, None, f"国债收益率拉取失败（{type(exc).__name__}: {exc}）"


def _fetch_cpi() -> tuple[float | None, pd.Series | None, str]:
    """拉取中国 CPI 同比。

    Returns:
        (最新值, 历史序列, 错误信息)
    """
    try:
        import akshare as ak

        with contextlib.redirect_stdout(sys.stderr):
            df = ak.macro_china_cpi()
        if df is not None and len(df) > 0:
            # 列名可能是：月份, 全国-当月, ... 或 date, value
            date_col = None
            val_col = None
            for c in df.columns:
                cl = str(c).lower()
                if "月份" in cl or "日期" in cl or "date" in cl or "统计时间" in cl:
                    date_col = c
                if "当月" in cl or "同比" in cl or "全国" in cl:
                    val_col = c
            if date_col is None and len(df.columns) >= 2:
                date_col = df.columns[0]
            if val_col is None and len(df.columns) >= 2:
                val_col = df.columns[1]
            if date_col and val_col:
                df = df.copy()
                df["_date"] = pd.to_datetime(df[date_col], errors="coerce")
                df["_val"] = pd.to_numeric(df[val_col], errors="coerce")
                df = df.dropna(subset=["_date", "_val"]).sort_values("_date")
                if len(df) > 0:
                    return float(df["_val"].iloc[-1]), df["_val"], ""
        return None, None, "macro_china_cpi 返回数据格式异常"
    except Exception as exc:
        return None, None, f"CPI 拉取失败（{type(exc).__name__}: {exc}）"


def _fetch_pmi() -> tuple[float | None, pd.Series | None, str]:
    """拉取中国制造业 PMI。

    Returns:
        (最新值, 历史序列, 错误信息)
    """
    try:
        import akshare as ak

        with contextlib.redirect_stdout(sys.stderr):
            df = ak.macro_china_pmi()
        if df is not None and len(df) > 0:
            date_col = None
            val_col = None
            for c in df.columns:
                cl = str(c).lower()
                if "月份" in cl or "日期" in cl or "date" in cl or "统计时间" in cl:
                    date_col = c
                if "制造业" in cl or "pmi" in cl.lower():
                    val_col = c
            if date_col is None and len(df.columns) >= 2:
                date_col = df.columns[0]
            if val_col is None and len(df.columns) >= 2:
                # 取第二列（通常是制造业 PMI）
                val_col = df.columns[1]
            if date_col and val_col:
                df = df.copy()
                df["_date"] = pd.to_datetime(df[date_col], errors="coerce")
                df["_val"] = pd.to_numeric(df[val_col], errors="coerce")
                df = df.dropna(subset=["_date", "_val"]).sort_values("_date")
                if len(df) > 0:
                    return float(df["_val"].iloc[-1]), df["_val"], ""
        return None, None, "macro_china_pmi 返回数据格式异常"
    except Exception as exc:
        return None, None, f"PMI 拉取失败（{type(exc).__name__}: {exc}）"


# ---------------------------------------------------------------------------
# 宏观 regime 判断
# ---------------------------------------------------------------------------


def fetch_macro_snapshot() -> MacroSnapshot:
    """拉取宏观数据快照（国债利率 / CPI / PMI）。

    各指标独立拉取，单项失败不影响其他项（errors 记录失败原因）。
    """
    errors: list[str] = []

    bond_yield, bond_series, bond_err = _fetch_bond_yield()
    if bond_err:
        errors.append(bond_err)

    cpi_yoy, cpi_series, cpi_err = _fetch_cpi()
    if cpi_err:
        errors.append(cpi_err)

    pmi, pmi_series, pmi_err = _fetch_pmi()
    if pmi_err:
        errors.append(pmi_err)

    # 趋势判断
    bond_trend = _trend_label(bond_series) if bond_series is not None else None
    cpi_trend = _trend_label(cpi_series) if cpi_series is not None else None
    pmi_trend = _trend_label(pmi_series) if pmi_series is not None else None

    # 数据截止日期
    asof = ""
    for series in (bond_series, cpi_series, pmi_series):
        if series is not None and len(series) > 0:
            # 取最新的索引作为日期参考
            break

    return MacroSnapshot(
        bond_yield_10y=bond_yield,
        bond_yield_trend=bond_trend,
        cpi_yoy=cpi_yoy,
        cpi_trend=cpi_trend,
        pmi=pmi,
        pmi_trend=pmi_trend,
        asof=asof,
        errors=errors,
    )


def detect_macro_regime(snapshot: MacroSnapshot | None = None) -> MacroRegime:
    """基于宏观快照判断宏观 regime。

    分类规则（简单可解释）：
    - PMI >= 50 且利率上行 → expansion（经济扩张）
    - PMI >= 50 且利率下行/平稳 → easing（宽松有利）
    - PMI < 50 且利率上行 → stagflation（滞胀压力）
    - PMI < 50 且利率下行/平稳 → contraction（收缩衰退）
    - PMI 或利率缺失 → unknown

    CPI 作为辅助修正：CPI > 3% 时加重通胀担忧（扩张→过热提示）。

    Args:
        snapshot: 宏观快照；None 时自动拉取。

    Returns:
        MacroRegime（描述性上下文，不参与评分裁决）。
    """
    if snapshot is None:
        snapshot = fetch_macro_snapshot()

    pmi = snapshot.pmi
    bond_trend = snapshot.bond_yield_trend
    cpi = snapshot.cpi_yoy

    components: dict = {}

    # PMI 判断
    if pmi is not None:
        components["pmi"] = {
            "value": pmi,
            "signal": "扩张" if pmi >= 50 else "收缩",
            "trend": snapshot.pmi_trend,
        }

    # 利率判断
    if snapshot.bond_yield_10y is not None:
        components["bond_yield"] = {
            "value": snapshot.bond_yield_10y,
            "signal": bond_trend or "unknown",
            "interpretation": {
                "rising": "利率上行（资金收紧）",
                "falling": "利率下行（宽松预期）",
                "flat": "利率平稳",
            }.get(bond_trend, "趋势不明"),
        }

    # CPI 判断
    if cpi is not None:
        if cpi > 3.0:
            cpi_signal = "通胀偏高"
        elif cpi < 0:
            cpi_signal = "通缩风险"
        else:
            cpi_signal = "通胀温和"
        components["cpi"] = {
            "value": cpi,
            "signal": cpi_signal,
            "trend": snapshot.cpi_trend,
        }

    # 核心分类：需要 PMI + 利率趋势
    if pmi is None or bond_trend is None:
        label = "unknown"
    elif pmi >= 50:
        if bond_trend == "rising":
            label = "expansion"
        else:
            label = "easing"
    else:
        if bond_trend == "rising":
            label = "stagflation"
        else:
            label = "contraction"

    # CPI 修正提示（不改 label，加到 advice）
    advice = _MACRO_ADVICE[label]
    if cpi is not None and cpi > 3.0 and label == "expansion":
        advice += "；CPI 偏高，注意过热风险"
    if cpi is not None and cpi < 0 and label == "contraction":
        advice += "；CPI 为负，通缩压力加大"

    return MacroRegime(
        label=label,
        label_cn=MACRO_REGIME_CN[label],
        advice=advice,
        snapshot=snapshot,
        components=components,
    )


def format_macro_regime(regime: MacroRegime) -> str:
    """单行文字描述，供 CLI 输出。"""
    if regime.label == "unknown":
        errs = regime.snapshot.errors
        if errs:
            return f"宏观环境：数据不足（{errs[0]}）"
        return "宏观环境：数据不足"

    parts = []
    snap = regime.snapshot
    if snap.pmi is not None:
        parts.append(f"PMI {snap.pmi:.1f}")
    if snap.bond_yield_10y is not None:
        trend_cn = {"rising": "↑", "falling": "↓", "flat": "→"}.get(
            snap.bond_yield_trend or "", ""
        )
        parts.append(f"国债 {snap.bond_yield_10y:.2f}%{trend_cn}")
    if snap.cpi_yoy is not None:
        parts.append(f"CPI {snap.cpi_yoy:.1f}%")

    return (
        f"宏观环境：{regime.label_cn}（{'，'.join(parts)}）"
        f"→ {regime.advice}"
    )
