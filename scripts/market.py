"""市场领域常量与标的代码工具。

从 cli_common.py 提取的领域知识（市场后缀、交易单位、代码校验），
使数据层（datafeed.py）不再反向依赖 CLI 工具模块。

cli_common.py 通过 re-export 保持向后兼容。
"""

from __future__ import annotations

import re

# 标的代码统一格式：代码.市场后缀（如 600000.SH / AAPL.US / 00700.HK / cu2501.SHF）
SYMBOL_RE = re.compile(r"^[0-9A-Za-z]+\.[A-Za-z]{2,4}$")

# 向后兼容别名（cli_common 中原名 _SYMBOL_RE）
_SYMBOL_RE = SYMBOL_RE

SYMBOL_FORMAT_HINT = (
    "标的代码格式应为「代码.市场后缀」，如 600000.SH（A股）/ AAPL.US（美股）/ "
    "00700.HK（港股）/ cu2501.SHF（期货）；完整后缀见 references/data-fetching.md。"
)

# ---------------------------------------------------------------------------
# 市场常量：消除各模块中重复的硬编码
# ---------------------------------------------------------------------------

#: A 股市场后缀（沪深北）
ASTOCK_SUFFIXES = (".SH", ".SZ", ".BJ")

#: A 股默认最小交易单位（一手 = 100 股）
ASTOCK_LOT_SIZE = 100

#: 默认最小交易单位（非 A 股）
DEFAULT_LOT_SIZE = 1


def is_astock(symbol: str) -> bool:
    """判断标的是否为 A 股（沪深北）。"""
    return symbol.upper().endswith(ASTOCK_SUFFIXES)


def default_lot_size(market: str = "generic", symbol: str = "") -> int:
    """根据市场预设或标的后缀返回默认最小交易单位。

    优先级：market=="astock" > 标的后缀判断 > 默认 1。
    """
    if market == "astock":
        return ASTOCK_LOT_SIZE
    if symbol and is_astock(symbol):
        return ASTOCK_LOT_SIZE
    return DEFAULT_LOT_SIZE


def validate_symbol(symbol: str) -> str:
    """校验单个标的代码格式，合法时返回去除首尾空白的代码，非法时抛 ValueError。

    统一校验入口，消除 cli_common.check_symbol 与 datafeed._check_symbol 的重复逻辑。
    CLI 层捕获 ValueError 后转为 SystemExit（exit 2），数据层直接使用。

    Raises:
        ValueError: 标的代码格式非法，消息包含可操作的格式提示。
    """
    sym = (symbol or "").strip()
    if not SYMBOL_RE.match(sym):
        raise ValueError(f"标的代码不合法：'{symbol}'。{SYMBOL_FORMAT_HINT}")
    return sym
