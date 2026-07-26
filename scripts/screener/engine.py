"""价值筛选引擎：两阶段漏斗（批量快照过滤 → 逐只深度过滤）+ 综合评分。

定位：用绝对估值/质量/分红/成长阈值从全市场中筛出低估+优质+潜力标的。
与 run_scan.py（趋势动量纪律过滤）和 run_factor.py（多因子截面排名）互补。

筛选是基本面快照过滤，不是收益预测；数据为最近公开报告期，存在滞后。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .data import (
    fetch_astock_detail,
    fetch_astock_gross_margin,
    fetch_astock_rd_ratio,
    fetch_astock_snapshot,
    fetch_benchmark_close,
    fetch_dividend_years,
    fetch_drawdown_52w,
    fetch_price_position,
    fetch_sp500_symbols,
    fetch_technical_profile,
    fetch_us_snapshot,
    fetch_yfinance_metrics,
    fetch_yfinance_rd_ratio,
    is_a_share,
)


@dataclass
class ScreenCriteria:
    """十维筛选阈值（0/False 表示不启用该维度）+ 可选估值分位增强。"""

    max_pe: float = 20.0        # 市盈率上限
    max_pb: float = 3.0         # 市净率上限
    min_roe: float = 10.0       # ROE 下限(%)
    max_debt: float = 70.0      # 资产负债率上限(%)，默认会剔除高杠杆金融股，0=不筛
    min_div: float = 0.0        # 股息率下限(%)，0=不筛
    min_growth: float = 0.0     # 净利润增速下限(%)，0=不筛
    min_rev_growth: float = 0.0  # 营收增速下限(%)，0=不筛（百倍股：增长须由营收驱动）
    min_gross_margin: float = 0.0  # 毛利率下限(%)，0=不筛（DHQ：定价权与规模效应信号）
    min_rd_ratio: float = 0.0   # 研发强度下限(%)，0=不筛（费雪：研发是成长引擎）
    min_cap: float = 30.0       # 总市值下限(亿)
    max_cap: float = 0.0        # 总市值上限(亿)，0=不筛（十倍股：小市值起步）
    min_cash_yield: float = 0.0  # 现金流收益率下限(%)，0=不筛（FCF Yield 近似）
    smart_growth: bool = False  # 聪明增长：要求资产增速 < 净利润增速（仅 A 股有数据）
    max_price_pos: float = 0.0  # 52 周价格位置上限(0~1)，0=不筛（低位左侧启动）
    min_drawdown: float = 0.0   # 自 52 周高点回撤下限(%)，0=不筛（DHQ：回撤进入折扣区才买）
    min_price_pos: float = 0.0  # 52 周价格位置下限(0~1)，0=不筛（猛兽股：买强不买弱）
    trend_filter: bool = False  # 多头趋势结构：收盘 > MA50 且 MA50 > MA200
    rs_filter: bool = False     # RS 线：加权相对强度（3/6/9/12 月）跑赢基准
    min_updown_vol: float = 0.0  # 近 50 日上涨日均量/下跌日均量下限，0=不筛（吸筹特征）
    market_filter: bool = False  # 大势确认：基准站上 MA50 与 MA200（猛兽股必要条件）
    min_div_years: int = 0      # 连续分红年数下限，0=不筛（红利股：分红纪律，仅 A 股有数据）
    max_val_pct: float = 0.0    # PE/PB 历史分位均值上限(0~1)，0=不筛（红利股：防周期顶部假低估）
    use_valuation_pct: bool = False  # 是否启用估值历史分位增强（逐只拉取，较慢）
    valuation_lookback: int = 5      # 估值分位回看年数

    def to_dict(self) -> dict:
        d = {
            "max_pe": self.max_pe,
            "max_pb": self.max_pb,
            "min_roe": self.min_roe,
            "max_debt": self.max_debt,
            "min_div": self.min_div,
            "min_growth": self.min_growth,
            "min_cap": self.min_cap,
        }
        if self.min_rev_growth > 0:
            d["min_rev_growth"] = self.min_rev_growth
        if self.min_gross_margin > 0:
            d["min_gross_margin"] = self.min_gross_margin
        if self.min_rd_ratio > 0:
            d["min_rd_ratio"] = self.min_rd_ratio
        if self.min_drawdown > 0:
            d["min_drawdown"] = self.min_drawdown
        if self.max_cap > 0:
            d["max_cap"] = self.max_cap
        if self.min_cash_yield > 0:
            d["min_cash_yield"] = self.min_cash_yield
        if self.smart_growth:
            d["smart_growth"] = True
        if self.max_price_pos > 0:
            d["max_price_pos"] = self.max_price_pos
        if self.min_price_pos > 0:
            d["min_price_pos"] = self.min_price_pos
        if self.trend_filter:
            d["trend_filter"] = True
        if self.rs_filter:
            d["rs_filter"] = True
        if self.min_updown_vol > 0:
            d["min_updown_vol"] = self.min_updown_vol
        if self.market_filter:
            d["market_filter"] = True
        if self.min_div_years > 0:
            d["min_div_years"] = self.min_div_years
        if self.max_val_pct > 0:
            d["max_val_pct"] = self.max_val_pct
        if self.use_valuation_pct or self.max_val_pct > 0:
            d["use_valuation_pct"] = True
            d["valuation_lookback"] = self.valuation_lookback
        return d


#: 预设筛选方案：键为 CLI 参数名（dest 形式），值为预设默认，显式参数可覆盖。
#: multibagger 取自 Yartseva(2025) 464 只美股十倍股实证 + Alta Fox(2020) 研究，
#: 阈值按 A 股口径本土化：小市值/便宜/现金流好/聪明增长/低位左侧，不要求高增长。
#: hundredbagger 取自迈耶《如何找到100倍回报的股票》365 只百倍股研究（1962-2014）：
#: 高 ROE 持续再投资 + 营收/利润双高增 + 小市值起点 + 低杠杆 + 合理价格；
#: 与 multibagger 的关键差异：质量成长路线（不卡 PB、不左侧择时）vs 便宜左侧路线。
#: monster 取自波伊克《猛兽股》（Monster Stocks, 2007）：年内翻倍股的必要条件
#: 与共同特征——大势确认上行（必要条件）+ 盈利高增的领导股 + 接近 52 周新高
#: （买强不买弱）+ RS 线跑赢基准 + 上涨放量下跌缩量 + 沿 MA50 上行；
#: 与两个左侧/质量预设互补：monster 是右侧趋势追踪（突破后买强），非底部潜伏。
#: dhq 取自马哈尼《高增长科技股投资法》（Nothing But Net, 2021）核心策略：
#: 用折扣价买高质量公司（Dislocated High-Quality）——高质量=营收高增（20%+）
#: + 高毛利（定价权）+ 已具规模（非小盘故事股）；折扣=自 52 周高点回撤
#: 进入 20%~30% 加仓区；不看 PE/PB/ROE（高增长期利润被创新投入压低，奈飞式）。
#: 与 multibagger（便宜左侧）差异：dhq 只买高质量回调，不捡低质量便宜货。
#: superstock 取自斯泰恩《100倍超级强势股》（Insider Buy Superstocks, 2013）：
#: 低估值与爆发成长的罕见合流——PE<10 锁死下行 + 盈利爆发（增速>40%）且
#: 营收驱动 + 低杠杆（书中"无负债"）+ 小市值（对应书中低流通盘/股价 $4~15）
#: + 已突破启动（52 周上半部 + 沿 10 周线≈MA50 的多头结构）+ 突破放量回调
#: 缩量。书名核心信号"内部人士公开市场买入"无 A 股等价数据源，须人工核查
#: 大股东/高管增持与回购公告补位。与 monster 差异：monster 追接近新高的
#: 强势不看估值，superstock 要求便宜的爆发成长，条件更严候选更少。
#: fisher 取自费雪《费雪论成长股获利》（Paths to Wealth Through Common
#: Stocks, 1960）的成长质量标准：真成长由营收与研发驱动（识破削减
#: 成本/一次性收益的虚假成长）+ 利润率高于行业（定价权）+ 管理层高效
#: 再投资（高 ROE 且资产增速<利润增速）+ 财务稳健（低杠杆，少靠增发
#: 稀释）+ 合理价格（不为成长付任意高价，但不要求便宜）。书中核心
#: 定性项——管理层质量、闲聊法调研、9 条并购原则——无数据源，须人工
#: 尽调补位。与 hundredbagger（同为质量成长）差异：fisher 强调研发引擎
#: 与利润率，不卡小市值（成熟成长公司也可）。
#: dividend 为红利股左侧筛选：高股息（>3%）+ 低估值（PE<15/PB<2 且 PE/PB
#: 历史分位低于 50%，防周期顶部假低估）+ 分红纪律（连续分红 ≥5 年，仅 A 股
#: 有数据）+ 财务稳健（ROE>8% 利润支撑分红、负债<60% 杠杆不撑股息）。
#: 与 multibagger（便宜小市值博弹性）差异：dividend 买的是可持续现金流，
#: 候选偏大盘成熟股；左侧分批建仓应接 run_dca（smart/dip），不做一次性抄底。
PRESETS: dict[str, dict] = {
    "multibagger": {
        "max_pe": 0.0,          # 不看 PE：十倍股起飞前盈利普遍平庸，PE 失真
        "max_pb": 1.6,          # 便宜：Book-to-Market 前 30% 的绝对阈值近似
        "min_roe": 5.0,         # 财务健康即可（十倍股起点 ROE 中位数仅 9%）
        "min_cap": 15.0,        # 流动性/壳风险底线
        "max_cap": 200.0,       # 小市值：十倍股几乎都从中小市值起步
        "min_cash_yield": 6.0,  # 现金流收益率：研究中最强单一预测因子
        "smart_growth": True,   # 资产增速 < 利润增速（扩张有效率）
        "max_price_pos": 0.5,   # 52 周区间下半部（左侧启动，不追高）
    },
    "hundredbagger": {
        "max_pe": 50.0,         # 合理价格即可（宽松上限防纯故事股）：书中不要求便宜
        "max_pb": 0.0,          # 不看 PB：高 ROE 复利机器 PB 必然不低，卡 PB 与高 ROE 自相矛盾
        "min_roe": 20.0,        # 核心标准：ROE>20% 持续高回报再投资（复利发动机）
        "max_debt": 60.0,       # 低杠杆：百倍股靠经营复利而非债务支撑 ROE
        "min_growth": 15.0,     # 利润高增：百倍股需 ~20% 年化复合，单期同比口径留容差
        "min_rev_growth": 15.0,  # 营收驱动：利润增长须有营收支撑，防纯削减成本
        "min_cap": 15.0,        # 流动性/壳风险底线（同 multibagger）
        "max_cap": 100.0,       # 小市值起点：书中建议 <10 亿美元，A 股口径≈100 亿
    },
    "monster": {
        "max_pe": 0.0,          # 不看 PE：猛兽股为成长领导股，强势阶段 PE 普遍偏高
        "max_pb": 0.0,          # 不看 PB：同上，买强不买便宜
        "min_roe": 15.0,        # 领导股质量：欧奈尔系 ROE ≥ 17% 的 A 股宽松口径
        "min_growth": 25.0,     # 盈利高增：书中猛兽股启动前普遍利润高增（欧奈尔 C 标准）
        "min_price_pos": 0.75,  # 52 周区间上四分之一：接近新高，买强不买弱
        "trend_filter": True,   # 收盘 > MA50 且 MA50 > MA200：沿 50 日线上行的多头结构
        "rs_filter": True,      # RS 线跑赢基准：猛兽股突破时 RS 线同步/领先创新高
        "min_updown_vol": 1.2,  # 上涨日均量/下跌日均量 ≥ 1.2：放量上涨缩量回调（吸筹）
        "market_filter": True,  # 大势确认：书中必要条件——猛兽股几乎只在新一轮升势中产生
    },
    "dhq": {
        "max_pe": 0.0,             # 不看 PE：高增长科技股用收入增速定价，PE 失真（书中经验 9）
        "max_pb": 0.0,             # 不看 PB：轻资产科技公司 PB 无意义
        "min_roe": 0.0,            # 不卡 ROE：高增长期利润被创新投入压低（奈飞式）
        "min_cap": 100.0,          # 高质量门槛：已具规模的行业领导者（防小盘故事股）
        "min_rev_growth": 20.0,    # 书中高增长门槛：营收增速 20%+（增长须由收入驱动）
        "min_gross_margin": 40.0,  # 书中优秀标准毛利 60%+，按 A 股科技口径放宽到 40%
        "min_drawdown": 20.0,      # DHQ 折扣触发：自 52 周高点回撤 ≥20%（书中 20%~30% 加仓区）
    },
    "superstock": {
        "max_pe": 10.0,          # 书中标准：PE<10 用低估值锁死成长股的下行风险
        "max_pb": 0.0,           # 不看 PB：书中不用 PB，小盘成长股 PB 参考性弱
        "min_roe": 0.0,          # 不卡 ROE：爆发前盈利基数低，用增速+营收驱动代替
        "max_debt": 50.0,        # 书中"无负债"标准：A 股口径放宽为负债率<50%
        "min_growth": 40.0,      # 爆发性盈利（blockbuster earnings）：利润大增
        "min_rev_growth": 20.0,  # 可持续性：增长须由营收驱动，防一次性收益冲利润
        "min_cap": 15.0,         # 流动性/壳风险底线（同 multibagger）
        "max_cap": 100.0,        # 低流通盘/小市值：书中市值<1 亿美元，A 股口径≈100 亿
        "min_price_pos": 0.5,    # 已突破启动：52 周区间上半部（突破后回踩买，不追新高）
        "trend_filter": True,    # 神奇支撑线：沿 10 周线（≈MA50）上行的多头结构
        "min_updown_vol": 1.2,   # 突破放量回调缩量：上涨/下跌日均量比 ≥1.2（机构吸筹）
    },
    "dividend": {
        "max_pe": 15.0,        # 低估值：低价买现金流（对应 README 高分红低估值口径）
        "max_pb": 2.0,         # 低估值：账面资产不贵
        "min_roe": 8.0,        # 盈利质量底线：分红须有利润支撑，不吃老本
        "max_debt": 60.0,      # 低负债：高杠杆撑起的高股息不可持续
        "min_div": 3.0,        # 高股息：股息率 > 3%
        "min_div_years": 5,    # 分红纪律：连续分红 ≥5 年（仅 A 股有数据）
        "max_val_pct": 0.5,    # PE/PB 历史分位低于 50%：防周期股盈利顶部的假低估
    },
    "fisher": {
        "max_pe": 40.0,            # 合理价格：不为成长付任意高价（宽松上限防纯故事股）
        "max_pb": 0.0,             # 不看 PB：费雪评估企业质地与前景而非账面资产
        "min_roe": 15.0,           # 管理层高效运用资本：持续高回报再投资
        "max_debt": 60.0,          # 财务稳健：成长靠内生利润而非债务/股权稀释
        "min_growth": 15.0,        # 利润增长高于平均水平
        "min_rev_growth": 15.0,    # 真成长由营收驱动（识破削减成本的虚假成长）
        "min_gross_margin": 30.0,  # 利润率高于行业平均（定价权），A 股全行业口径
        "smart_growth": True,      # 再投资效率：资产增速 < 利润增速（仅 A 股有数据）
        "min_rd_ratio": 3.0,       # 研发引擎：研发费用/营收 ≥3%（科技对成长的驱动）
    },
}


@dataclass
class ScreenResult:
    """单标的筛选结果。"""

    symbol: str
    name: str
    metrics: dict           # pe, pb, roe, debt_ratio, div_yield, profit_growth, total_mv, close
    score: float            # 综合评分 0~100
    passed: bool            # 是否通过全部启用维度
    fail_reasons: list[str] = field(default_factory=list)
    valuation: dict | None = None  # 估值历史分位（可选）

    def to_dict(self) -> dict:
        d = {
            "symbol": self.symbol,
            "name": self.name,
            "score": round(self.score, 1),
            "passed": self.passed,
            "fail_reasons": self.fail_reasons,
            **{k: _safe_round(v) for k, v in self.metrics.items()},
        }
        if self.valuation is not None:
            d["valuation"] = self.valuation
        return d


def _safe_round(v, ndigits: int = 2):
    """安全四舍五入：None/NaN 返回 None。"""
    if v is None:
        return None
    try:
        import math
        if math.isnan(float(v)):
            return None
        return round(float(v), ndigits)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 综合评分
# ---------------------------------------------------------------------------

#: 各维度默认权重（未启用的维度权重重新归一化）
_WEIGHTS = {
    "pe": 0.25,
    "pb": 0.20,
    "roe": 0.25,
    "debt": 0.10,
    "div": 0.10,
    "growth": 0.10,
    "rev_growth": 0.10,
    "gross": 0.15,
    "rd": 0.15,
    "cash": 0.15,
    "pos": 0.10,
    "dd": 0.10,
    "rs": 0.15,
    "vol": 0.10,
    "div_years": 0.10,
}


def composite_score(metrics: dict, criteria: ScreenCriteria) -> float:
    """综合评分：各维度达标程度加权，映射到 0~100。

    子分计算：
    - PE/PB/负债率（越低越好）：threshold / value，cap 到 2.0，再 /2 * 100
    - ROE/股息率/增速（越高越好）：value / threshold，cap 到 2.0，再 /2 * 100
    未启用的维度（阈值=0）不参与加权。
    """
    scores: dict[str, float] = {}
    weights: dict[str, float] = {}

    pe = metrics.get("pe")
    if criteria.max_pe > 0 and pe is not None and pe > 0:
        # PE 越低越好：阈值/实际值
        ratio = criteria.max_pe / pe
        scores["pe"] = min(ratio, 2.0) / 2.0 * 100.0
        weights["pe"] = _WEIGHTS["pe"]

    pb = metrics.get("pb")
    if criteria.max_pb > 0 and pb is not None and pb > 0:
        ratio = criteria.max_pb / pb
        scores["pb"] = min(ratio, 2.0) / 2.0 * 100.0
        weights["pb"] = _WEIGHTS["pb"]

    roe = metrics.get("roe")
    if criteria.min_roe > 0 and roe is not None:
        ratio = roe / criteria.min_roe if criteria.min_roe > 0 else 0
        scores["roe"] = min(max(ratio, 0), 2.0) / 2.0 * 100.0
        weights["roe"] = _WEIGHTS["roe"]

    debt = metrics.get("debt_ratio")
    if criteria.max_debt > 0 and debt is not None and debt > 0:
        ratio = criteria.max_debt / debt
        scores["debt"] = min(ratio, 2.0) / 2.0 * 100.0
        weights["debt"] = _WEIGHTS["debt"]

    div = metrics.get("div_yield")
    if criteria.min_div > 0 and div is not None:
        ratio = div / criteria.min_div if criteria.min_div > 0 else 0
        scores["div"] = min(max(ratio, 0), 2.0) / 2.0 * 100.0
        weights["div"] = _WEIGHTS["div"]

    growth = metrics.get("profit_growth")
    if criteria.min_growth > 0 and growth is not None:
        ratio = growth / criteria.min_growth if criteria.min_growth > 0 else 0
        scores["growth"] = min(max(ratio, 0), 2.0) / 2.0 * 100.0
        weights["growth"] = _WEIGHTS["growth"]

    rev = metrics.get("revenue_growth")
    if criteria.min_rev_growth > 0 and rev is not None:
        ratio = rev / criteria.min_rev_growth
        scores["rev_growth"] = min(max(ratio, 0), 2.0) / 2.0 * 100.0
        weights["rev_growth"] = _WEIGHTS["rev_growth"]

    gross = metrics.get("gross_margin")
    if criteria.min_gross_margin > 0 and gross is not None:
        ratio = gross / criteria.min_gross_margin
        scores["gross"] = min(max(ratio, 0), 2.0) / 2.0 * 100.0
        weights["gross"] = _WEIGHTS["gross"]

    rd = metrics.get("rd_ratio")
    if criteria.min_rd_ratio > 0 and rd is not None:
        ratio = rd / criteria.min_rd_ratio
        scores["rd"] = min(max(ratio, 0), 2.0) / 2.0 * 100.0
        weights["rd"] = _WEIGHTS["rd"]

    cash = metrics.get("cash_yield")
    if criteria.min_cash_yield > 0 and cash is not None:
        ratio = cash / criteria.min_cash_yield
        scores["cash"] = min(max(ratio, 0), 2.0) / 2.0 * 100.0
        weights["cash"] = _WEIGHTS["cash"]

    pos = metrics.get("price_pos")
    if criteria.max_price_pos > 0 and pos is not None:
        # 位置越低（越靠近 52 周低点）得分越高
        scores["pos"] = (1.0 - min(max(pos, 0.0), 1.0)) * 100.0
        weights["pos"] = _WEIGHTS["pos"]
    elif criteria.min_price_pos > 0 and pos is not None:
        # 猛兽股口径相反：位置越高（越接近 52 周新高）得分越高，买强不买弱
        scores["pos"] = min(max(pos, 0.0), 1.0) * 100.0
        weights["pos"] = _WEIGHTS["pos"]

    dd = metrics.get("drawdown")
    if criteria.min_drawdown > 0 and dd is not None:
        # DHQ 口径：回撤越深折扣越大得分越高（阈值处 50 分，2 倍阈值封顶）
        ratio = dd / criteria.min_drawdown
        scores["dd"] = min(max(ratio, 0), 2.0) / 2.0 * 100.0
        weights["dd"] = _WEIGHTS["dd"]

    rs = metrics.get("rs_excess")
    if criteria.rs_filter and rs is not None:
        # RS 超额 0~50 个百分点线性映射到 0~100 分
        scores["rs"] = min(max(rs, 0.0), 50.0) / 50.0 * 100.0
        weights["rs"] = _WEIGHTS["rs"]

    updown = metrics.get("updown_vol_ratio")
    if criteria.min_updown_vol > 0 and updown is not None:
        ratio = updown / criteria.min_updown_vol
        scores["vol"] = min(max(ratio, 0), 2.0) / 2.0 * 100.0
        weights["vol"] = _WEIGHTS["vol"]

    div_years = metrics.get("div_years")
    if criteria.min_div_years > 0 and div_years is not None:
        ratio = div_years / criteria.min_div_years
        scores["div_years"] = min(max(ratio, 0), 2.0) / 2.0 * 100.0
        weights["div_years"] = _WEIGHTS["div_years"]

    if not weights:
        return 0.0

    # 权重归一化
    total_w = sum(weights.values())
    score = sum(scores[k] * weights[k] / total_w for k in scores)
    return round(score, 1)


# ---------------------------------------------------------------------------
# Phase 1: A 股批量快照过滤
# ---------------------------------------------------------------------------


def screen_astock_phase1(
    criteria: ScreenCriteria,
    log: Callable[..., None] | None = None,
) -> tuple[list[dict], int]:
    """A 股 Phase 1：批量快照过滤（PE/PB/市值/ST）。

    Returns:
        (存活标的列表[{code, name, pe, pb, total_mv, div_yield, close}], 总扫描数)
    """
    snapshot = fetch_astock_snapshot(log)
    if snapshot is None or len(snapshot) == 0:
        return [], 0

    total = len(snapshot)
    df = snapshot.copy()

    # 排除 ST/*ST
    if "name" in df.columns:
        mask_st = df["name"].astype(str).str.contains("ST", case=False, na=False)
        df = df[~mask_st]

    # PE 过滤（正值且 < max_pe）
    if criteria.max_pe > 0 and "pe" in df.columns:
        df = df[(df["pe"] > 0) & (df["pe"] <= criteria.max_pe)]

    # PB 过滤（正值且 < max_pb）
    if criteria.max_pb > 0 and "pb" in df.columns:
        df = df[(df["pb"] > 0) & (df["pb"] <= criteria.max_pb)]

    # 市值过滤（下限/上限）
    if criteria.min_cap > 0 and "total_mv" in df.columns:
        df = df[df["total_mv"] >= criteria.min_cap]
    if criteria.max_cap > 0 and "total_mv" in df.columns:
        df = df[df["total_mv"] <= criteria.max_cap]

    survivors = df.to_dict("records")
    if log:
        log(f"Phase 1 快照过滤：{total} 只 → {len(survivors)} 只存活")
    return survivors, total


# ---------------------------------------------------------------------------
# 美股 universe: Phase 1 批量快照过滤（降级 S&P 500 名单）
# ---------------------------------------------------------------------------


def screen_us_phase1(
    criteria: ScreenCriteria,
    log: Callable[..., None] | None = None,
) -> tuple[list[dict], int]:
    """美股 universe Phase 1：全市场快照批量过滤（PE/PB/市值，单位亿美元）。

    东财快照不可用时降级 S&P 500 成分股名单（无快照指标，全部交给
    Phase 2 逐只核查）；两级都失败返回 ([], 0)。

    Returns:
        (存活标的列表[{code(如 AAPL.US), name, pe, pb, total_mv, close}], 总扫描数)
    """
    snapshot = fetch_us_snapshot(log)
    if snapshot is not None and len(snapshot):
        total = len(snapshot)
        df = snapshot.copy()
        # PE 过滤（正值且 < max_pe，同 A 股口径）
        if criteria.max_pe > 0 and df["pe"].notna().any():
            df = df[(df["pe"] > 0) & (df["pe"] <= criteria.max_pe)]
        # PB 过滤（快照可能无 PB 列：全 NaN 时跳过，交给 Phase 2）
        if criteria.max_pb > 0 and df["pb"].notna().any():
            df = df[(df["pb"] > 0) & (df["pb"] <= criteria.max_pb)]
        # 市值过滤（注意：universe=us 时阈值单位为亿美元）
        if criteria.min_cap > 0:
            df = df[df["total_mv"] >= criteria.min_cap]
        if criteria.max_cap > 0:
            df = df[df["total_mv"] <= criteria.max_cap]
        survivors = df.to_dict("records")
        if log:
            log(f"Phase 1 美股快照过滤：{total} 只 → {len(survivors)} 只存活")
        return survivors, total

    # 降级：S&P 500 名单（无快照指标，不做批量过滤）
    symbols = fetch_sp500_symbols(log)
    if not symbols:
        return [], 0
    if log:
        log(f"东财美股快照不可用，降级 S&P 500 成分股名单（{len(symbols)} 只，全部逐只核查）")
    return [{"code": s} for s in symbols], len(symbols)


# ---------------------------------------------------------------------------
# Phase 2: A 股逐只深度过滤
# ---------------------------------------------------------------------------


def screen_astock_phase2(
    survivors: list[dict],
    criteria: ScreenCriteria,
    log: Callable[..., None] | None = None,
    on_progress: Callable[[int, str], None] | None = None,
) -> list[ScreenResult]:
    """A 股 Phase 2：逐只拉取 ROE/负债率/增速并过滤。

    Args:
        survivors: Phase 1 存活列表。
        criteria: 筛选阈值。
        log: 日志函数。
        on_progress: 进度回调 (done, symbol)。

    Returns:
        通过全部启用维度的 ScreenResult 列表（按综合评分降序）。
    """
    # 判断是否需要 Phase 2（有深度指标阈值启用时才逐只拉取）
    need_detail = (
        criteria.min_roe > 0 or criteria.max_debt > 0 or criteria.min_growth > 0
        or criteria.min_rev_growth > 0 or criteria.min_gross_margin > 0
        or criteria.min_cash_yield > 0 or criteria.smart_growth
        or criteria.min_rd_ratio > 0
    )

    results: list[ScreenResult] = []
    skipped = 0

    for i, row in enumerate(survivors):
        code = str(row.get("code", ""))
        name = str(row.get("name", ""))
        symbol = _code_to_symbol(code)

        metrics = {
            "pe": row.get("pe"),
            "pb": row.get("pb"),
            "total_mv": row.get("total_mv"),
            "div_yield": row.get("div_yield"),
            "close": row.get("close"),
            "roe": None,
            "debt_ratio": None,
            "profit_growth": None,
            "revenue_growth": None,
            "asset_growth": None,
            "cash_yield": None,
            "gross_margin": None,
            "rd_ratio": None,
        }

        # 股息率 Phase 1 过滤（如果快照有该字段且阈值启用）
        if criteria.min_div > 0:
            div = metrics.get("div_yield")
            if div is None or div < criteria.min_div:
                if on_progress:
                    on_progress(i + 1, symbol)
                continue

        # 逐只深度拉取
        if need_detail:
            detail = fetch_astock_detail(code)
            if detail is None:
                skipped += 1
                if on_progress:
                    on_progress(i + 1, symbol)
                continue
            metrics["roe"] = detail.get("roe")
            metrics["debt_ratio"] = detail.get("debt_ratio")
            metrics["profit_growth"] = detail.get("profit_growth")
            metrics["revenue_growth"] = detail.get("revenue_growth")
            metrics["asset_growth"] = detail.get("asset_growth")
            metrics["gross_margin"] = detail.get("gross_margin")
            # 毛利率兜底：新浪口径近年普遍 NaN，启用该维度时用同花顺摘要补齐
            if criteria.min_gross_margin > 0 and metrics["gross_margin"] is None:
                metrics["gross_margin"] = fetch_astock_gross_margin(code)
            # 研发强度：新浪财务分析指标无该字段，启用维度时用利润表补齐
            if criteria.min_rd_ratio > 0:
                metrics["rd_ratio"] = fetch_astock_rd_ratio(code)
            # 现金流收益率 = 每股经营现金流 / 股价（FCF Yield 的 A 股免费近似）
            ocf = detail.get("ocf_per_share")
            close = metrics.get("close")
            if ocf is not None and close:
                metrics["cash_yield"] = ocf / close * 100.0

        # 深度过滤
        fail_reasons = _check_detail_criteria(metrics, criteria)
        passed = len(fail_reasons) == 0

        score = composite_score(metrics, criteria)
        results.append(ScreenResult(
            symbol=symbol,
            name=name,
            metrics=metrics,
            score=score,
            passed=passed,
            fail_reasons=fail_reasons,
        ))

        if on_progress:
            on_progress(i + 1, symbol)

    if log and skipped:
        log(f"Phase 2 跳过 {skipped} 只（财务指标拉取失败）")

    # 只保留通过的，按评分排序
    passed_results = [r for r in results if r.passed]
    passed_results.sort(key=lambda r: r.score, reverse=True)
    return passed_results


def _check_detail_criteria(metrics: dict, criteria: ScreenCriteria) -> list[str]:
    """检查深度指标是否达标，返回失败原因列表。"""
    reasons: list[str] = []

    if criteria.min_roe > 0:
        roe = metrics.get("roe")
        if roe is None:
            reasons.append("ROE 数据缺失")
        elif roe < criteria.min_roe:
            reasons.append(f"ROE {roe:.1f}% < {criteria.min_roe:.0f}%")

    if criteria.max_debt > 0:
        debt = metrics.get("debt_ratio")
        if debt is None:
            reasons.append("负债率数据缺失")
        elif debt > criteria.max_debt:
            reasons.append(f"负债率 {debt:.1f}% > {criteria.max_debt:.0f}%")

    if criteria.min_growth > 0:
        growth = metrics.get("profit_growth")
        if growth is None:
            reasons.append("增速数据缺失")
        elif growth < criteria.min_growth:
            reasons.append(f"净利润增速 {growth:.1f}% < {criteria.min_growth:.0f}%")

    if criteria.min_rev_growth > 0:
        rev = metrics.get("revenue_growth")
        if rev is None:
            reasons.append("营收增速数据缺失")
        elif rev < criteria.min_rev_growth:
            reasons.append(f"营收增速 {rev:.1f}% < {criteria.min_rev_growth:.0f}%")

    if criteria.min_gross_margin > 0:
        gross = metrics.get("gross_margin")
        if gross is None:
            reasons.append("毛利率数据缺失")
        elif gross < criteria.min_gross_margin:
            reasons.append(f"毛利率 {gross:.1f}% < {criteria.min_gross_margin:.0f}%（定价权不足）")

    if criteria.min_rd_ratio > 0:
        rd = metrics.get("rd_ratio")
        if rd is None:
            reasons.append("研发费用数据缺失（或未披露研发投入）")
        elif rd < criteria.min_rd_ratio:
            reasons.append(f"研发强度 {rd:.1f}% < {criteria.min_rd_ratio:.0f}%（研发引擎不足）")

    if criteria.min_cash_yield > 0:
        cash = metrics.get("cash_yield")
        if cash is None:
            reasons.append("现金流数据缺失")
        elif cash < criteria.min_cash_yield:
            reasons.append(f"现金流收益率 {cash:.1f}% < {criteria.min_cash_yield:.0f}%")

    if criteria.smart_growth:
        asset_g = metrics.get("asset_growth")
        profit_g = metrics.get("profit_growth")
        if asset_g is None or profit_g is None:
            reasons.append("聪明增长数据缺失（资产/利润增速）")
        elif asset_g >= profit_g:
            reasons.append(f"资产增速 {asset_g:.1f}% ≥ 利润增速 {profit_g:.1f}%（扩张低效）")

    return reasons


# ---------------------------------------------------------------------------
# 港美股：yfinance 逐只（Phase 1+2 合并）
# ---------------------------------------------------------------------------


def screen_yfinance(
    symbols: list[str],
    criteria: ScreenCriteria,
    log: Callable[..., None] | None = None,
    on_progress: Callable[[int, str], None] | None = None,
) -> list[ScreenResult]:
    """港美股逐只筛选（yfinance .info，Phase 1+2 合并）。

    Returns:
        通过全部启用维度的 ScreenResult 列表（按综合评分降序）。
    """
    results: list[ScreenResult] = []
    skipped = 0

    for i, symbol in enumerate(symbols):
        info = fetch_yfinance_metrics(symbol)
        if info is None:
            skipped += 1
            if on_progress:
                on_progress(i + 1, symbol)
            continue

        metrics = {
            "pe": info.get("pe"),
            "pb": info.get("pb"),
            "roe": info.get("roe"),
            "div_yield": info.get("div_yield"),
            "debt_ratio": info.get("debt_ratio"),
            "profit_growth": info.get("profit_growth"),
            "revenue_growth": info.get("revenue_growth"),
            "gross_margin": info.get("gross_margin"),
            "asset_growth": info.get("asset_growth"),
            "cash_yield": info.get("cash_yield"),
            "price_pos": info.get("price_pos"),
            "drawdown": info.get("drawdown"),
            "total_mv": info.get("total_mv"),
            "close": info.get("close"),
        }

        # 研发强度：.info 无该字段，启用费雪维度时额外拉年度利润表
        metrics["rd_ratio"] = (
            fetch_yfinance_rd_ratio(symbol) if criteria.min_rd_ratio > 0 else None
        )

        # 全维度过滤
        fail_reasons = _check_all_criteria(metrics, criteria)
        passed = len(fail_reasons) == 0
        score = composite_score(metrics, criteria)

        results.append(ScreenResult(
            symbol=symbol,
            name=info.get("name", symbol),
            metrics=metrics,
            score=score,
            passed=passed,
            fail_reasons=fail_reasons,
        ))

        if on_progress:
            on_progress(i + 1, symbol)

    if log and skipped:
        log(f"跳过 {skipped} 只（yfinance 拉取失败）")

    passed_results = [r for r in results if r.passed]
    passed_results.sort(key=lambda r: r.score, reverse=True)
    return passed_results


def _check_all_criteria(metrics: dict, criteria: ScreenCriteria) -> list[str]:
    """检查全部维度（港美股无分阶段，一次全检）。"""
    reasons: list[str] = []

    if criteria.max_pe > 0:
        pe = metrics.get("pe")
        if pe is None or pe <= 0:
            reasons.append("PE 无效或缺失")
        elif pe > criteria.max_pe:
            reasons.append(f"PE {pe:.1f} > {criteria.max_pe:.0f}")

    if criteria.max_pb > 0:
        pb = metrics.get("pb")
        if pb is None or pb <= 0:
            reasons.append("PB 无效或缺失")
        elif pb > criteria.max_pb:
            reasons.append(f"PB {pb:.2f} > {criteria.max_pb:.1f}")

    if criteria.min_cap > 0:
        mv = metrics.get("total_mv")
        if mv is not None and mv < criteria.min_cap:
            reasons.append(f"市值 {mv:.0f} 亿 < {criteria.min_cap:.0f} 亿")

    if criteria.max_cap > 0:
        mv = metrics.get("total_mv")
        if mv is not None and mv > criteria.max_cap:
            reasons.append(f"市值 {mv:.0f} 亿 > {criteria.max_cap:.0f} 亿")

    if criteria.max_price_pos > 0:
        pos = metrics.get("price_pos")
        if pos is None:
            reasons.append("52 周价格位置数据缺失")
        elif pos > criteria.max_price_pos:
            reasons.append(f"52 周位置 {pos:.0%} > {criteria.max_price_pos:.0%}（位置偏高）")

    if criteria.min_drawdown > 0:
        dd = metrics.get("drawdown")
        if dd is None:
            reasons.append("52 周回撤数据缺失")
        elif dd < criteria.min_drawdown:
            reasons.append(f"回撤 {dd:.0f}% < {criteria.min_drawdown:.0f}%（尚未进入折扣区）")

    if criteria.min_div > 0:
        div = metrics.get("div_yield")
        if div is None or div < criteria.min_div:
            reasons.append(f"股息率 {div if div else 0:.1f}% < {criteria.min_div:.1f}%")

    reasons.extend(_check_detail_criteria(metrics, criteria))
    return reasons


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------


def run_screen(
    criteria: ScreenCriteria,
    symbols: list[str] | None = None,
    universe: str | None = None,
    top: int = 30,
    sort_by: str = "score",
    log: Callable[..., None] | None = None,
    on_progress: Callable[[int, str], None] | None = None,
) -> dict:
    """统一筛选入口：自动分流 A 股批量 / 美股 universe / 港美股逐只。

    Args:
        criteria: 筛选阈值。
        symbols: 手动标的列表（含港美股时必须）；与 universe 互斥。
        universe: 全市场扫描范围：None=A 股全市场，"us"=美股全市场
            （东财快照，市值阈值单位变为亿美元；快照不可用时降级 S&P 500 名单）。
        top: 最多返回达标数。
        sort_by: 排序字段（score/pe/pb/roe/div/growth）。
        log: 日志函数。
        on_progress: 进度回调。

    Returns:
        {"candidates": [...], "n_scanned": int, "n_phase1": int, "n_final": int}；
        启用 market_filter 且走全市场扫描时附加 "market_regime"。
    """
    market_info: dict | None = None
    if symbols:
        # 手动列表：按市场分流
        a_symbols = [s for s in symbols if is_a_share(s)]
        other_symbols = [s for s in symbols if not is_a_share(s)]

        all_results: list[ScreenResult] = []

        # A 股部分：也走两阶段（但 Phase 1 用手动列表而非全市场快照）
        if a_symbols:
            # 手动 A 股：直接逐只（无批量快照优势，合并为 yfinance 式逐只）
            a_results = _screen_astock_manual(a_symbols, criteria, log, on_progress)
            all_results.extend(a_results)

        # 港美股部分
        if other_symbols:
            yf_results = screen_yfinance(other_symbols, criteria, log, on_progress)
            all_results.extend(yf_results)

        n_scanned = len(symbols)
        n_phase1 = n_scanned  # 手动模式无分阶段
    elif universe == "us":
        # 大势前置检查（美股基准 SPY）：大势不对不筛，避免白跑逐只漏斗
        if criteria.market_filter:
            market_info = market_regime(fetch_benchmark_close("SPY.US"))
            if market_info is not None and not market_info["uptrend"]:
                if log:
                    log(
                        "大势过滤：基准（SPY）未站上 MA50/MA200，"
                        "猛兽股纪律为大势不对不买，本次不筛选"
                    )
                return {
                    "candidates": [], "n_scanned": 0, "n_phase1": 0,
                    "n_final": 0, "market_regime": market_info,
                }
        # 美股全市场：快照批量过滤 → yfinance 逐只深度核查（同手动港美股路径）
        survivors, n_scanned = screen_us_phase1(criteria, log)
        n_phase1 = len(survivors)
        us_symbols = [str(r["code"]) for r in survivors]
        if us_symbols and log:
            log(f"Phase 2 逐只核查 {len(us_symbols)} 只（yfinance，较慢）...")
        all_results = screen_yfinance(us_symbols, criteria, log, on_progress)
    else:
        # 大势前置检查（猛兽股必要条件）：大势不对不筛，避免白跑全市场漏斗
        if criteria.market_filter:
            market_info = market_regime(fetch_benchmark_close("510300.SH"))
            if market_info is not None and not market_info["uptrend"]:
                if log:
                    log(
                        "大势过滤：基准（沪深300）未站上 MA50/MA200，"
                        "猛兽股纪律为大势不对不买，本次不筛选"
                    )
                return {
                    "candidates": [], "n_scanned": 0, "n_phase1": 0,
                    "n_final": 0, "market_regime": market_info,
                }
        # A 股全市场批量
        survivors, n_scanned = screen_astock_phase1(criteria, log)
        n_phase1 = len(survivors)
        all_results = screen_astock_phase2(survivors, criteria, log, on_progress)

        # Phase 3：52 周价格位置过滤（仅对通过基本面的候选逐只拉日 K，较慢）
        if criteria.max_price_pos > 0 and all_results:
            all_results = _filter_price_position(all_results, criteria, log, on_progress)

        # DHQ 折扣过滤：自 52 周高点回撤达阈才保留（同样逐只拉日 K，较慢）
        if criteria.min_drawdown > 0 and all_results:
            all_results = _filter_drawdown(all_results, criteria, log, on_progress)

    # 猛兽股技术面过滤（52 周高位/均线多头/RS 线/量价，逐只拉日 K 较慢）
    tech_needed = (
        criteria.min_price_pos > 0 or criteria.trend_filter or criteria.rs_filter
        or criteria.min_updown_vol > 0 or criteria.market_filter
    )
    if tech_needed and all_results:
        all_results = _filter_monster_tech(all_results, criteria, log, on_progress)

    # 红利股维度：连续分红年数过滤（逐只拉分红历史，仅 A 股有数据源）
    if criteria.min_div_years > 0 and all_results:
        all_results = _filter_dividend_years(all_results, criteria, log, on_progress)

    # 估值分位增强（可选，逐只拉取历史 PE/PB）；启用分位上限时强制拉取
    if (criteria.use_valuation_pct or criteria.max_val_pct > 0) and all_results:
        all_results = _enrich_valuation(
            all_results, criteria.valuation_lookback, log, on_progress
        )

    # 红利股维度：PE/PB 历史分位上限过滤（防周期股盈利顶部的假低估）
    if criteria.max_val_pct > 0 and all_results:
        all_results = _filter_valuation_pct(all_results, criteria, log)

    # 排序
    all_results = _sort_results(all_results, sort_by)
    candidates = all_results[:top]

    out = {
        "candidates": [r.to_dict() for r in candidates],
        "n_scanned": n_scanned,
        "n_phase1": None if symbols else n_phase1,
        "n_final": len(all_results),
    }
    if market_info is not None:
        out["market_regime"] = market_info
    return out


def market_regime(bench_close) -> dict | None:
    """基准大势判定：收盘站上 MA50 与 MA200 才算确认上行。

    《猛兽股》必要条件：猛兽股几乎只在新一轮升势中产生（与 CAN SLIM
    的 M 判定同口径）。

    Returns:
        ``{"uptrend": bool, "close", "ma50", "ma200"}``；基准数据不足 200 根
        或缺失返回 None（调用方自行决定降级策略）。
    """
    if bench_close is None:
        return None
    bench = bench_close.dropna().astype(float)
    if len(bench) < 200:
        return None
    last = float(bench.iloc[-1])
    ma50 = float(bench.rolling(50).mean().iloc[-1])
    ma200 = float(bench.rolling(200).mean().iloc[-1])
    return {
        "uptrend": bool(last > ma50 and last > ma200),
        "close": round(last, 2),
        "ma50": round(ma50, 2),
        "ma200": round(ma200, 2),
    }


def _check_tech_criteria(profile: dict | None, criteria: ScreenCriteria, regime: dict | None) -> list[str]:
    """检查猛兽股技术面维度是否达标，返回失败原因列表。

    数据缺失视为不达标（书中纪律：无法核查即不买）。
    """
    if profile is None:
        return ["技术面数据缺失（日 K 不足 200 根或拉取失败）"]

    reasons: list[str] = []
    if criteria.min_price_pos > 0:
        pos = profile.get("price_pos")
        if pos is None:
            reasons.append("52 周价格位置数据缺失")
        elif pos < criteria.min_price_pos:
            reasons.append(
                f"52 周位置 {pos:.0%} < {criteria.min_price_pos:.0%}（远离强势区，买强不买弱）"
            )

    if criteria.trend_filter and not profile.get("trend_ok"):
        reasons.append(
            f"趋势结构不满足：收盘 {profile.get('close'):.2f} 需站上 "
            f"MA50 {profile.get('ma50'):.2f} 且 MA50 > MA200 {profile.get('ma200'):.2f}"
        )

    if criteria.rs_filter:
        rs = profile.get("rs_excess")
        if rs is None:
            reasons.append("RS 相对强度数据缺失（无基准或样本不足 12 个月）")
        elif rs <= 0:
            reasons.append(f"RS 线弱势：加权相对强度落后基准 {rs:+.1f} 个百分点")

    if criteria.min_updown_vol > 0:
        updown = profile.get("updown_vol_ratio")
        if updown is None:
            reasons.append("量能数据缺失，无法核查量价关系")
        elif updown < criteria.min_updown_vol:
            reasons.append(
                f"上涨/下跌日均量比 {updown:.2f} < {criteria.min_updown_vol:.1f}（派发重于吸筹）"
            )

    if criteria.market_filter:
        if regime is None:
            reasons.append("大势数据缺失（基准不足 200 根），无法确认市场方向")
        elif not regime["uptrend"]:
            reasons.append(
                f"大势未确认上行：基准收盘 {regime['close']} 未同时站上 "
                f"MA50 {regime['ma50']} 与 MA200 {regime['ma200']}"
            )

    return reasons


def _filter_monster_tech(
    results: list[ScreenResult],
    criteria: ScreenCriteria,
    log: Callable[..., None] | None = None,
    on_progress: Callable[[int, str], None] | None = None,
) -> list[ScreenResult]:
    """猛兽股技术面过滤：52 周高位 + 均线多头 + RS 跑赢基准 + 量价吸筹 + 大势。

    逐只拉取近 260 根日 K 计算技术画像；同一基准（如沪深300）只拉一次。
    技术指标写入 metrics 并重算综合评分；数据缺失视为不达标剔除。
    """
    if log:
        log(f"技术面过滤：拉取 {len(results)} 只候选的日 K 与基准 RS 线...")

    need_bench = criteria.rs_filter or criteria.market_filter
    regime_cache: dict[str, dict | None] = {}
    kept: list[ScreenResult] = []
    n_fail, n_missing = 0, 0

    for i, r in enumerate(results):
        bench_close = fetch_benchmark_close(r.symbol) if need_bench else None
        regime = None
        if criteria.market_filter:
            bench_key = r.symbol.rsplit(".", 1)[-1].upper() if "." in r.symbol else ""
            if bench_key not in regime_cache:
                regime_cache[bench_key] = market_regime(bench_close)
            regime = regime_cache[bench_key]

        profile = fetch_technical_profile(r.symbol, bench_close)
        reasons = _check_tech_criteria(profile, criteria, regime)
        if reasons:
            if profile is None:
                n_missing += 1
            else:
                n_fail += 1
        else:
            r.metrics["price_pos"] = profile.get("price_pos")
            r.metrics["rs_excess"] = profile.get("rs_excess")
            r.metrics["updown_vol_ratio"] = profile.get("updown_vol_ratio")
            r.score = composite_score(r.metrics, criteria)
            kept.append(r)
        if on_progress:
            on_progress(i + 1, r.symbol)

    if log:
        log(
            f"技术面过滤：{len(results)} 只 → {len(kept)} 只存活"
            f"（技术面不达标 {n_fail} 只，数据缺失 {n_missing} 只）"
        )
    return kept


def _filter_price_position(
    results: list[ScreenResult],
    criteria: ScreenCriteria,
    log: Callable[..., None] | None = None,
    on_progress: Callable[[int, str], None] | None = None,
) -> list[ScreenResult]:
    """Phase 3：逐只拉取日 K 计算 52 周价格位置，保留低位（左侧）标的。

    位置计入综合评分（越低越高分）；日 K 拉取失败视为数据缺失剔除。
    """
    if log:
        log(f"Phase 3 位置过滤：拉取 {len(results)} 只候选的近 52 周日 K...")

    kept: list[ScreenResult] = []
    n_high, n_missing = 0, 0
    for i, r in enumerate(results):
        pos = r.metrics.get("price_pos")
        if pos is None:
            pos = fetch_price_position(r.symbol)
        if pos is None:
            n_missing += 1
        elif pos > criteria.max_price_pos:
            n_high += 1
        else:
            r.metrics["price_pos"] = pos
            r.score = composite_score(r.metrics, criteria)
            kept.append(r)
        if on_progress:
            on_progress(i + 1, r.symbol)

    if log:
        log(
            f"Phase 3 位置过滤：{len(results)} 只 → {len(kept)} 只存活"
            f"（位置偏高 {n_high} 只，数据缺失 {n_missing} 只）"
        )
    return kept


def _filter_drawdown(
    results: list[ScreenResult],
    criteria: ScreenCriteria,
    log: Callable[..., None] | None = None,
    on_progress: Callable[[int, str], None] | None = None,
) -> list[ScreenResult]:
    """DHQ 折扣过滤：逐只拉日 K 计算自 52 周高点回撤，保留回撤达阈的标的。

    回撤计入综合评分（越深折扣越大得分越高）；日 K 拉取失败视为数据缺失剔除。
    """
    if log:
        log(f"DHQ 折扣过滤：拉取 {len(results)} 只候选的近 52 周日 K 计算回撤...")

    kept: list[ScreenResult] = []
    n_shallow, n_missing = 0, 0
    for i, r in enumerate(results):
        dd = r.metrics.get("drawdown")
        if dd is None:
            dd = fetch_drawdown_52w(r.symbol)
        if dd is None:
            n_missing += 1
        elif dd < criteria.min_drawdown:
            n_shallow += 1
        else:
            r.metrics["drawdown"] = dd
            r.score = composite_score(r.metrics, criteria)
            kept.append(r)
        if on_progress:
            on_progress(i + 1, r.symbol)

    if log:
        log(
            f"DHQ 折扣过滤：{len(results)} 只 → {len(kept)} 只存活"
            f"（回撤未达阈 {n_shallow} 只，数据缺失 {n_missing} 只）"
        )
    return kept


def _filter_dividend_years(
    results: list[ScreenResult],
    criteria: ScreenCriteria,
    log: Callable[..., None] | None = None,
    on_progress: Callable[[int, str], None] | None = None,
) -> list[ScreenResult]:
    """红利股分红纪律过滤：逐只拉分红历史，保留连续分红年数达阈的标的。

    连续年数计入综合评分（越长分红纪律越好得分越高）；数据源仅支持
    A 股，非 A 股/拉取失败视为数据缺失剔除（无法核查即不买）。
    """
    if log:
        log(f"分红纪律过滤：拉取 {len(results)} 只候选的历史分红记录（仅 A 股有数据）...")

    kept: list[ScreenResult] = []
    n_short, n_missing = 0, 0
    for i, r in enumerate(results):
        years = fetch_dividend_years(r.symbol)
        if years is None:
            n_missing += 1
        elif years < criteria.min_div_years:
            n_short += 1
        else:
            r.metrics["div_years"] = years
            r.score = composite_score(r.metrics, criteria)
            kept.append(r)
        if on_progress:
            on_progress(i + 1, r.symbol)

    if log:
        log(
            f"分红纪律过滤：{len(results)} 只 → {len(kept)} 只存活"
            f"（连续分红不足 {criteria.min_div_years} 年 {n_short} 只，数据缺失 {n_missing} 只）"
        )
    return kept


def _filter_valuation_pct(
    results: list[ScreenResult],
    criteria: ScreenCriteria,
    log: Callable[..., None] | None = None,
) -> list[ScreenResult]:
    """估值分位上限过滤：PE/PB 分位均值超阈剔除（防周期顶部假低估）。

    需先经 :func:`_enrich_valuation` 附加分位数据；分位拉取失败的标的
    视为数据缺失剔除（低分位是硬条件而非加分项，无法核查即不买）。
    """
    kept: list[ScreenResult] = []
    n_high, n_missing = 0, 0
    for r in results:
        pcts = []
        if r.valuation is not None:
            pcts = [
                p for p in (r.valuation.get("pe_percentile"), r.valuation.get("pb_percentile"))
                if p is not None
            ]
        if not pcts:
            n_missing += 1
        elif sum(pcts) / len(pcts) > criteria.max_val_pct:
            n_high += 1
        else:
            kept.append(r)

    if log:
        log(
            f"估值分位过滤：{len(results)} 只 → {len(kept)} 只存活"
            f"（分位高于 {criteria.max_val_pct:.0%} 共 {n_high} 只，数据缺失 {n_missing} 只）"
        )
    return kept


def _screen_astock_manual(
    symbols: list[str],
    criteria: ScreenCriteria,
    log: Callable[..., None] | None = None,
    on_progress: Callable[[int, str], None] | None = None,
) -> list[ScreenResult]:
    """手动 A 股列表：逐只拉取全部指标（无批量快照优势）。"""
    results: list[ScreenResult] = []
    skipped = 0

    for i, symbol in enumerate(symbols):
        code = symbol.split(".")[0]
        detail = fetch_astock_detail(code)

        # 尝试从快照获取 PE/PB（逐只无批量接口，用 yfinance 兜底）
        info = fetch_yfinance_metrics(symbol)
        if info is None and detail is None:
            skipped += 1
            if on_progress:
                on_progress(i + 1, symbol)
            continue

        metrics = {
            "pe": (info or {}).get("pe"),
            "pb": (info or {}).get("pb"),
            "roe": (detail or {}).get("roe"),
            "div_yield": (info or {}).get("div_yield"),
            "debt_ratio": (detail or {}).get("debt_ratio"),
            "profit_growth": (detail or {}).get("profit_growth"),
            "revenue_growth": (detail or {}).get("revenue_growth")
            if detail else (info or {}).get("revenue_growth"),
            "asset_growth": (detail or {}).get("asset_growth"),
            "gross_margin": (detail or {}).get("gross_margin")
            if detail else (info or {}).get("gross_margin"),
            "total_mv": (info or {}).get("total_mv"),
            "close": (info or {}).get("close"),
            "cash_yield": (info or {}).get("cash_yield"),
            "price_pos": (info or {}).get("price_pos"),
            "drawdown": (info or {}).get("drawdown"),
        }

        # A 股优先用财报口径：每股经营现金流 / 股价（与全市场批量路径一致）
        ocf = (detail or {}).get("ocf_per_share")
        close = metrics.get("close")
        if ocf is not None and close:
            metrics["cash_yield"] = ocf / close * 100.0

        # 回撤/位置维度：yfinance 缺失时用免费日 K 兜底（与全市场路径同口径）
        if criteria.min_drawdown > 0 and metrics.get("drawdown") is None:
            metrics["drawdown"] = fetch_drawdown_52w(symbol)
        if criteria.max_price_pos > 0 and metrics.get("price_pos") is None:
            metrics["price_pos"] = fetch_price_position(symbol)

        # 毛利率兜底：新浪/yfinance 都缺失时用同花顺摘要补齐
        if criteria.min_gross_margin > 0 and metrics.get("gross_margin") is None:
            metrics["gross_margin"] = fetch_astock_gross_margin(code)

        # 研发强度：启用费雪维度时用新浪利润表补齐（与全市场路径同口径）
        if criteria.min_rd_ratio > 0:
            metrics["rd_ratio"] = fetch_astock_rd_ratio(code)

        fail_reasons = _check_all_criteria(metrics, criteria)
        passed = len(fail_reasons) == 0
        score = composite_score(metrics, criteria)

        results.append(ScreenResult(
            symbol=symbol,
            name=(info or {}).get("name", symbol),
            metrics=metrics,
            score=score,
            passed=passed,
            fail_reasons=fail_reasons,
        ))

        if on_progress:
            on_progress(i + 1, symbol)

    if log and skipped:
        log(f"跳过 {skipped} 只 A 股（数据拉取失败）")

    passed_results = [r for r in results if r.passed]
    passed_results.sort(key=lambda r: r.score, reverse=True)
    return passed_results


def _enrich_valuation(
    results: list[ScreenResult],
    lookback_years: int,
    log: Callable[..., None] | None = None,
    on_progress: Callable[[int, str], None] | None = None,
) -> list[ScreenResult]:
    """为候选标的附加估值历史分位（逐只拉取，较慢）。

    估值分位作为评分加成：低分位（便宜）加分，高分位（贵）减分。
    加成幅度：±10 分（在原始综合评分基础上）。
    """
    from data.valuation import fetch_valuation_percentile

    if log:
        log(f"估值分位增强：拉取 {len(results)} 只候选的历史 PE/PB（近 {lookback_years} 年）...")

    enriched: list[ScreenResult] = []
    for i, r in enumerate(results):
        vp = fetch_valuation_percentile(r.symbol, lookback_years)
        if vp is not None:
            r.valuation = vp.to_dict()
            # 评分加成：分位越低（越便宜）加分越多
            pcts = [p for p in (vp.pe_percentile, vp.pb_percentile) if p is not None]
            if pcts:
                avg_pct = sum(pcts) / len(pcts)
                # 分位 0% → +10，分位 50% → 0，分位 100% → -10
                bonus = (0.5 - avg_pct) * 20.0
                r.score = max(0.0, min(100.0, r.score + bonus))
        enriched.append(r)
        if on_progress:
            on_progress(i + 1, r.symbol)

    if log:
        n_ok = sum(1 for r in enriched if r.valuation is not None)
        log(f"估值分位增强完成：{n_ok}/{len(results)} 只成功获取")

    return enriched


def _sort_results(results: list[ScreenResult], sort_by: str) -> list[ScreenResult]:
    """按指定字段排序（降序，PE/PB 为升序）。"""
    if sort_by == "pe":
        return sorted(results, key=lambda r: r.metrics.get("pe") or 9999)
    if sort_by == "pb":
        return sorted(results, key=lambda r: r.metrics.get("pb") or 9999)
    if sort_by == "roe":
        return sorted(results, key=lambda r: r.metrics.get("roe") or 0, reverse=True)
    if sort_by == "div":
        return sorted(results, key=lambda r: r.metrics.get("div_yield") or 0, reverse=True)
    if sort_by == "growth":
        return sorted(results, key=lambda r: r.metrics.get("profit_growth") or 0, reverse=True)
    # 默认按综合评分
    return sorted(results, key=lambda r: r.score, reverse=True)


def _code_to_symbol(code: str) -> str:
    """A 股纯数字代码 → 带市场后缀（已上移至 market.code_to_symbol，委托保兼容）。"""
    from market import code_to_symbol

    return code_to_symbol(code)
