"""数据源健康度与熔断：整体不可用的源不再逐只标的重复付重试代价。

动机：``datafeed._fetch_ohlcv_raw`` 对每个标的独立走「重试 → 降级」。当某个源
整体不可用（Yahoo 限流、TickFlow 服务故障、无 API Key）时，每只标的都要付满
``retries + 1`` 次尝试和 1s + 2s 退避——全市场 5000 只标的光 sleep 就是数小时，
而结论从第 3 只标的起就已经确定了。

本模块记录进程内的连续失败计数：某源连续失败达阈值后，本次运行的后续标的
直接跳过它，直接走下一个源。任一次成功立即清零（源恢复后自动放行）。

**保底规则**：若熔断过滤后候选源为空，则忽略熔断状态使用完整链。宁可慢，
也不能让「所有源都被熔断」导致全市场扫描一次性全军覆没——毕竟熔断只是
基于历史失败的启发式推断，不是事实判定。

计数用 ``threading.Lock`` 保护：``sync.py`` 是多线程批量同步。
状态刻意只存进程内（不落盘）：跨进程共享会让「上次运行的故障」影响本次运行，
而数据源可用性变化很快，陈旧的熔断状态比没有更糟。
"""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass, field

from envconfig import get_env_config

_LOCK = threading.Lock()

#: source_name -> 连续失败次数（成功即清零）
_failures: dict[str, int] = {}

#: 已告警过的源（避免每只标的都打一遍熔断提示）
_warned: set[str] = set()


@dataclass
class HealthSnapshot:
    """健康度快照（供 run_doctor 与测试观察）。"""

    failures: dict[str, int] = field(default_factory=dict)
    tripped: list[str] = field(default_factory=list)
    threshold: int = 0


def _threshold() -> int:
    """熔断阈值：连续失败达此次数后跳过该源；0 表示关闭熔断。"""
    return get_env_config().source_failfast


def record_success(source_name: str) -> None:
    """记录一次成功：清零该源的连续失败计数（源恢复后自动放行）。"""
    with _LOCK:
        _failures.pop(source_name, None)
        _warned.discard(source_name)


def record_failure(source_name: str) -> None:
    """记录一次失败：累加该源的连续失败计数。"""
    with _LOCK:
        _failures[source_name] = _failures.get(source_name, 0) + 1


def is_tripped(source_name: str) -> bool:
    """该源是否已熔断（连续失败达阈值）。"""
    threshold = _threshold()
    if threshold <= 0:
        return False
    with _LOCK:
        return _failures.get(source_name, 0) >= threshold


def filter_sources(sources: list) -> list:
    """过滤掉已熔断的源；全被熔断时返回原列表（保底不空）。

    Args:
        sources: 候选数据源列表（已按 supports 过滤）。

    Returns:
        可用源列表；顺序保持不变。
    """
    if not sources:
        return sources
    alive = [s for s in sources if not is_tripped(getattr(s, "name", ""))]
    if not alive:
        # 全部熔断：熔断只是启发式推断，不能据此判定「无源可用」
        return sources
    for s in sources:
        name = getattr(s, "name", "")
        if s not in alive:
            _warn_once(name)
    return alive


def _warn_once(source_name: str) -> None:
    """每个源只告警一次，避免全市场扫描时刷屏。"""
    with _LOCK:
        if source_name in _warned:
            return
        _warned.add(source_name)
        count = _failures.get(source_name, 0)
    print(
        f"[warn] {source_name} 连续失败 {count} 次，本次运行后续标的将跳过该源"
        f"（设 ALPHA_FORGE_SOURCE_FAILFAST=0 可关闭熔断）。",
        file=sys.stderr,
    )


def snapshot() -> HealthSnapshot:
    """当前健康度快照。"""
    threshold = _threshold()
    with _LOCK:
        failures = dict(_failures)
    tripped = (
        [name for name, n in failures.items() if n >= threshold]
        if threshold > 0
        else []
    )
    return HealthSnapshot(failures=failures, tripped=tripped, threshold=threshold)


def reset() -> None:
    """清空所有健康度状态（测试用；也可在长驻进程中手动重置）。"""
    with _LOCK:
        _failures.clear()
        _warned.clear()
