"""声明式自定义策略 DSL 引擎。

允许用户（或 Agent）通过 TOML 规则文件定义自定义交易策略，无需编写 Python 代码。
规则文件定义指标（白名单算子）+ 入场/离场条件（受限表达式），引擎解析后生成
与内置策略相同接口的信号 Series，可直接接入回测/寻优/模拟盘等全部下游能力。

设计原则：
- **安全**：不 eval 任何代码，仅解析受限表达式（指标引用 + 比较运算符）；
- **LLM 友好**：TOML 格式简洁，Agent 可根据用户自然语言描述直接生成规则文件；
- **可组合**：指标可引用其他指标（如 ``source = "fast_ma"``），条件支持 AND/OR 逻辑。

规则文件格式（TOML）::

    [meta]
    name = "golden_cross_rsi"
    description = "金叉且 RSI 未过热时买入，死叉或 RSI 超买时卖出"

    [indicators.fast_ma]
    type = "sma"
    period = 10

    [indicators.slow_ma]
    type = "sma"
    period = 30

    [indicators.rsi14]
    type = "rsi"
    period = 14

    [entry]
    # 所有条件同时满足（AND）
    conditions = [
        "fast_ma crosses_above slow_ma",
        "rsi14 < 70",
    ]

    [exit]
    # 任一条件满足即离场（OR）
    logic = "or"
    conditions = [
        "fast_ma crosses_below slow_ma",
        "rsi14 > 80",
    ]
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .base import Strategy

# ---------------------------------------------------------------------------
# 指标计算白名单
# ---------------------------------------------------------------------------

#: 支持的指标类型及其必需/可选参数
INDICATOR_SPEC: dict[str, dict] = {
    "sma": {"required": ["period"], "optional": {"source": "close"}},
    "ema": {"required": ["period"], "optional": {"source": "close"}},
    "rsi": {"required": ["period"], "optional": {"source": "close"}},
    "macd_line": {"required": [], "optional": {"fast": 12, "slow": 26, "signal": 9, "source": "close"}},
    "macd_signal": {"required": [], "optional": {"fast": 12, "slow": 26, "signal": 9, "source": "close"}},
    "macd_hist": {"required": [], "optional": {"fast": 12, "slow": 26, "signal": 9, "source": "close"}},
    "bollinger_upper": {"required": ["period"], "optional": {"std": 2.0, "source": "close"}},
    "bollinger_lower": {"required": ["period"], "optional": {"std": 2.0, "source": "close"}},
    "bollinger_mid": {"required": ["period"], "optional": {"std": 2.0, "source": "close"}},
    "atr": {"required": ["period"], "optional": {}},
    "donchian_upper": {"required": ["period"], "optional": {}},
    "donchian_lower": {"required": ["period"], "optional": {}},
    "kdj_k": {"required": [], "optional": {"period": 9, "k_smooth": 3, "d_smooth": 3}},
    "kdj_d": {"required": [], "optional": {"period": 9, "k_smooth": 3, "d_smooth": 3}},
    "momentum": {"required": ["period"], "optional": {"source": "close"}},
    "roc": {"required": ["period"], "optional": {"source": "close"}},
    "close": {"required": [], "optional": {}},
    "open": {"required": [], "optional": {}},
    "high": {"required": [], "optional": {}},
    "low": {"required": [], "optional": {}},
    "volume": {"required": [], "optional": {}},
}

#: 支持的比较运算符
OPERATORS = (">", "<", ">=", "<=", "crosses_above", "crosses_below")

#: 条件表达式正则（指标/数值 运算符 指标/数值）
_CONDITION_RE = re.compile(
    r"^\s*(?P<left>[\w.]+)\s+"
    r"(?P<op>>=|<=|>|<|crosses_above|crosses_below)\s+"
    r"(?P<right>[\w.+-]+)\s*$"
)


class DSLValidationError(ValueError):
    """DSL 规则文件校验失败。"""


# ---------------------------------------------------------------------------
# 规则解析与校验
# ---------------------------------------------------------------------------


def load_rules(path: str | Path) -> dict:
    """加载并校验 TOML 规则文件，返回解析后的规则字典。

    Raises:
        DSLValidationError: 格式/语义错误（含修复建议）。
    """
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    file = Path(path).expanduser()
    if not file.exists():
        raise DSLValidationError(f"规则文件不存在：{path}")

    with open(file, "rb") as f:
        try:
            rules = tomllib.load(f)
        except Exception as exc:
            raise DSLValidationError(f"TOML 解析失败：{exc}") from exc

    _validate_rules(rules)
    return rules


def _validate_rules(rules: dict) -> None:
    """校验规则结构完整性与语义合法性。"""
    # [meta]
    meta = rules.get("meta", {})
    if not meta.get("name"):
        raise DSLValidationError("[meta] 缺少 name 字段（策略名称）")

    # [indicators]
    indicators = rules.get("indicators", {})
    if not indicators:
        raise DSLValidationError("[indicators] 至少定义一个指标")
    for ind_name, ind_cfg in indicators.items():
        ind_type = ind_cfg.get("type", "")
        if ind_type not in INDICATOR_SPEC:
            available = ", ".join(sorted(INDICATOR_SPEC))
            raise DSLValidationError(
                f"指标 '{ind_name}' 的类型 '{ind_type}' 不支持。可选：{available}"
            )
        spec = INDICATOR_SPEC[ind_type]
        for req in spec["required"]:
            if req not in ind_cfg:
                raise DSLValidationError(
                    f"指标 '{ind_name}'（{ind_type}）缺少必需参数 '{req}'"
                )

    # [entry] / [exit]
    entry = rules.get("entry", {})
    exit_ = rules.get("exit", {})
    if not entry.get("conditions"):
        raise DSLValidationError("[entry] 缺少 conditions（入场条件列表）")
    if not exit_.get("conditions"):
        raise DSLValidationError("[exit] 缺少 conditions（离场条件列表）")

    # 校验条件表达式语法
    all_conds = entry["conditions"] + exit_["conditions"]
    for cond in all_conds:
        _validate_condition(cond, indicators)


#: 内置 OHLCV 列引用（无需在 [indicators] 中定义）
_BUILTIN_REFS = {"close", "open", "high", "low", "volume"}


def _validate_condition(cond: str, indicators: dict) -> None:
    """校验单条条件表达式。"""
    m = _CONDITION_RE.match(cond)
    if not m:
        raise DSLValidationError(
            f"条件表达式语法错误：'{cond}'。"
            f"格式：'<指标名> <运算符> <指标名或数值>'，"
            f"运算符可选：{', '.join(OPERATORS)}"
        )
    left, right = m.group("left"), m.group("right")
    # 非数值则必须是已定义的指标名或内置 OHLCV 列
    for ref in (left, right):
        if not _is_number(ref) and ref not in indicators and ref not in _BUILTIN_REFS:
            defined = ", ".join(sorted(indicators))
            raise DSLValidationError(
                f"条件 '{cond}' 引用了未定义的指标 '{ref}'。已定义：{defined}；"
                f"内置可用：{', '.join(sorted(_BUILTIN_REFS))}"
            )


def _is_number(s: str) -> bool:
    """判断字符串是否为数值。"""
    try:
        float(s)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# 指标计算
# ---------------------------------------------------------------------------


def compute_indicators(df: pd.DataFrame, indicators: dict[str, dict]) -> dict[str, pd.Series]:
    """按定义顺序计算全部指标，返回 {指标名: Series}。

    指标可引用其他已计算指标作为 source（按 TOML 定义顺序，先定义先计算）。
    """
    results: dict[str, pd.Series] = {}
    for ind_name, ind_cfg in indicators.items():
        ind_type = ind_cfg["type"]
        source_name = ind_cfg.get("source", "close")
        # source 可以是 OHLCV 列或已计算的指标
        if source_name in results:
            source = results[source_name]
        elif source_name in df.columns:
            source = df[source_name].astype(float)
        else:
            source = df["close"].astype(float)

        series = _compute_one(df, source, ind_type, ind_cfg)
        results[ind_name] = series
    return results


def _compute_one(df: pd.DataFrame, source: pd.Series, ind_type: str, cfg: dict) -> pd.Series:
    """计算单个指标。"""
    period = int(cfg.get("period", 14))

    if ind_type == "sma":
        return source.rolling(period).mean()
    elif ind_type == "ema":
        return source.ewm(span=period, adjust=False).mean()
    elif ind_type == "rsi":
        return _rsi(source, period)
    elif ind_type in ("macd_line", "macd_signal", "macd_hist"):
        fast = int(cfg.get("fast", 12))
        slow = int(cfg.get("slow", 26))
        sig = int(cfg.get("signal", 9))
        ema_fast = source.ewm(span=fast, adjust=False).mean()
        ema_slow = source.ewm(span=slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=sig, adjust=False).mean()
        if ind_type == "macd_line":
            return dif
        elif ind_type == "macd_signal":
            return dea
        else:
            return (dif - dea) * 2
    elif ind_type in ("bollinger_upper", "bollinger_lower", "bollinger_mid"):
        std_mult = float(cfg.get("std", 2.0))
        mid = source.rolling(period).mean()
        std = source.rolling(period).std(ddof=0)
        if ind_type == "bollinger_upper":
            return mid + std_mult * std
        elif ind_type == "bollinger_lower":
            return mid - std_mult * std
        else:
            return mid
    elif ind_type == "atr":
        return _atr(df, period)
    elif ind_type == "donchian_upper":
        return df["high"].astype(float).rolling(period).max()
    elif ind_type == "donchian_lower":
        return df["low"].astype(float).rolling(period).min()
    elif ind_type in ("kdj_k", "kdj_d"):
        k, d, _ = _kdj(df, int(cfg.get("period", 9)), int(cfg.get("k_smooth", 3)), int(cfg.get("d_smooth", 3)))
        return k if ind_type == "kdj_k" else d
    elif ind_type == "momentum":
        return source - source.shift(period)
    elif ind_type == "roc":
        return source / source.shift(period) - 1.0
    elif ind_type in ("close", "open", "high", "low", "volume"):
        col = ind_type
        if col in df.columns:
            return df[col].astype(float)
        return df["close"].astype(float)
    else:
        raise DSLValidationError(f"未知指标类型：{ind_type}")


def _rsi(source: pd.Series, period: int) -> pd.Series:
    """Wilder RSI。"""
    delta = source.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Average True Range。"""
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def _kdj(df: pd.DataFrame, period: int, k_smooth: int, d_smooth: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    """KDJ 指标。"""
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    lowest = low.rolling(period).min()
    highest = high.rolling(period).max()
    rsv = (close - lowest) / (highest - lowest).replace(0, np.nan) * 100.0
    k = rsv.ewm(alpha=1.0 / k_smooth, adjust=False).mean()
    d = k.ewm(alpha=1.0 / d_smooth, adjust=False).mean()
    j = 3.0 * k - 2.0 * d
    return k, d, j


# ---------------------------------------------------------------------------
# 条件求值
# ---------------------------------------------------------------------------


def evaluate_condition(cond: str, indicators: dict[str, pd.Series], df: pd.DataFrame | None = None) -> pd.Series:
    """求值单条条件表达式，返回布尔 Series。"""
    m = _CONDITION_RE.match(cond)
    if not m:
        raise DSLValidationError(f"条件表达式无法解析：'{cond}'")
    left_ref = m.group("left")
    op = m.group("op")
    right_ref = m.group("right")

    left = _resolve_ref(left_ref, indicators, df)
    right = _resolve_ref(right_ref, indicators, df)

    if op == ">":
        return left > right
    elif op == "<":
        return left < right
    elif op == ">=":
        return left >= right
    elif op == "<=":
        return left <= right
    elif op == "crosses_above":
        return _crosses_above(left, right)
    elif op == "crosses_below":
        return _crosses_below(left, right)
    else:
        raise DSLValidationError(f"不支持的运算符：{op}")


def _resolve_ref(
    ref: str, indicators: dict[str, pd.Series], df: pd.DataFrame | None = None
) -> pd.Series | float:
    """解析引用：数值返回 float，指标名返回 Series，内置 OHLCV 列从 df 取。"""
    if _is_number(ref):
        return float(ref)
    if ref in indicators:
        return indicators[ref]
    if ref in _BUILTIN_REFS and df is not None and ref in df.columns:
        return df[ref].astype(float)
    raise DSLValidationError(f"条件中引用了未定义的指标：'{ref}'")


def _crosses_above(a: pd.Series, b: pd.Series | float) -> pd.Series:
    """a 从下方穿越 b（金叉）：前一根 a<=b 且当前 a>b。"""
    if isinstance(b, (int, float)):
        b_series = pd.Series(b, index=a.index)
    else:
        b_series = b
    prev_below = a.shift(1) <= b_series.shift(1)
    curr_above = a > b_series
    return prev_below & curr_above


def _crosses_below(a: pd.Series, b: pd.Series | float) -> pd.Series:
    """a 从上方穿越 b（死叉）：前一根 a>=b 且当前 a<b。"""
    if isinstance(b, (int, float)):
        b_series = pd.Series(b, index=a.index)
    else:
        b_series = b
    prev_above = a.shift(1) >= b_series.shift(1)
    curr_below = a < b_series
    return prev_above & curr_below


def evaluate_conditions(
    conditions: list[str],
    indicators: dict[str, pd.Series],
    logic: str = "and",
    df: pd.DataFrame | None = None,
) -> pd.Series:
    """求值条件列表，按 logic（and/or）组合。"""
    if not conditions:
        raise DSLValidationError("条件列表为空")
    masks = [evaluate_condition(c, indicators, df) for c in conditions]
    combined = masks[0]
    for mask in masks[1:]:
        if logic == "or":
            combined = combined | mask
        else:
            combined = combined & mask
    return combined.fillna(False)


# ---------------------------------------------------------------------------
# 自定义策略类（接入 Strategy 接口）
# ---------------------------------------------------------------------------


class CustomStrategy(Strategy):
    """基于 DSL 规则文件的自定义策略。

    通过 ``CustomStrategy.from_rules(rules)`` 或 ``CustomStrategy.from_file(path)`` 构造。
    生成信号逻辑：
    - 入场条件满足 → 1（做多）
    - 离场条件满足 → 0（空仓）
    - 其余保持前一状态
    """

    name = "custom"
    display_name = "自定义规则"
    param_grid: dict[str, list] = {}  # DSL 策略不参与默认寻优网格
    register = False  # 需要 rules 才能构造，不进入 STRATEGIES 注册表

    def __init__(self, rules: dict, **params):
        self._rules = rules
        meta = rules.get("meta", {})
        self.name = meta.get("name", "custom")
        self.display_name = meta.get("description", meta.get("name", "自定义规则"))
        self._indicators_def = rules.get("indicators", {})
        self._entry_conds = rules.get("entry", {}).get("conditions", [])
        self._entry_logic = rules.get("entry", {}).get("logic", "and")
        self._exit_conds = rules.get("exit", {}).get("conditions", [])
        self._exit_logic = rules.get("exit", {}).get("logic", "or")
        self._allow_short = rules.get("exit", {}).get("allow_short", False)
        # 不调用 super().__init__ 以避免 default_params 冲突
        self.params = params

    @classmethod
    def from_file(cls, path: str | Path, **params) -> CustomStrategy:
        """从 TOML 文件加载规则并构造策略实例。"""
        rules = load_rules(path)
        return cls(rules, **params)

    @classmethod
    def from_rules(cls, rules: dict, **params) -> CustomStrategy:
        """从已解析的规则字典构造策略实例。"""
        _validate_rules(rules)
        return cls(rules, **params)

    @classmethod
    def default_params(cls) -> dict:
        return {}

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """根据 DSL 规则生成持仓信号。"""
        # 1. 计算全部指标
        indicators = compute_indicators(df, self._indicators_def)

        # 2. 求值入场/离场条件（内置 OHLCV 列可直接引用）
        entry_mask = evaluate_conditions(self._entry_conds, indicators, self._entry_logic, df)
        exit_mask = evaluate_conditions(self._exit_conds, indicators, self._exit_logic, df)

        # 3. 生成状态机信号：入场→持有→离场→空仓
        entry_arr = entry_mask.to_numpy(dtype=bool)
        exit_arr = exit_mask.to_numpy(dtype=bool)
        sig_arr = np.zeros(len(df), dtype=int)
        position = 0

        for i in range(len(df)):
            if position == 0:
                if entry_arr[i]:
                    position = 1
            elif position == 1:
                if exit_arr[i]:
                    position = -1 if self._allow_short else 0
            elif position == -1:
                if entry_arr[i]:
                    position = 1
            sig_arr[i] = position

        # 指标未形成前不入场（NaN 区域）
        warmup = self._estimate_warmup()
        if warmup > 0:
            sig_arr[:warmup] = 0

        return pd.Series(sig_arr, index=df.index)

    def _estimate_warmup(self) -> int:
        """估算指标预热期（取所有指标 period 最大值）。"""
        max_period = 0
        for cfg in self._indicators_def.values():
            p = cfg.get("period", 0)
            if isinstance(p, (int, float)):
                max_period = max(max_period, int(p))
            # MACD 类需要 slow + signal 的预热
            if cfg.get("type", "").startswith("macd"):
                max_period = max(max_period, int(cfg.get("slow", 26)) + int(cfg.get("signal", 9)))
        return max_period

    def rules_summary(self) -> dict:
        """返回规则摘要（用于 JSON 输出与展示）。"""
        return {
            "name": self._rules.get("meta", {}).get("name", "custom"),
            "description": self._rules.get("meta", {}).get("description", ""),
            "indicators": {
                name: {"type": cfg.get("type"), "period": cfg.get("period")}
                for name, cfg in self._indicators_def.items()
            },
            "entry": {"logic": self._entry_logic, "conditions": self._entry_conds},
            "exit": {"logic": self._exit_logic, "conditions": self._exit_conds},
        }

    def __repr__(self) -> str:
        name = self._rules.get("meta", {}).get("name", "custom")
        return f"CustomStrategy({name})"
