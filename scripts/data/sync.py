"""批量预同步：把「数据工程」从扫描/回测流程中剥离（借鉴 free-stockdb 本地优先思路）。

动机：run_screener / run_scan 等全市场流程逐只惰性拉取，首次运行耗时长且易触发
限频。本模块提供「先同步、后研究」的工作流：一次性把股票池的 K 线预热到本地
缓存，之后配合 ``ALPHA_FORGE_OFFLINE=1`` 可完全离线研究。

实现上直接复用 ``datafeed.fetch_ohlcv``：缓存命中跳过、尾部增量更新、多源降级
都天然生效，本模块只负责批量调度、并发控制与失败容忍；股票池解析
（需要 screener 快照降级）在 CLI 层 run_sync.py，避免 data → screener 反向依赖。
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

#: 默认并发数：akshare/baostock 等免费源对高并发不友好，保守起步
DEFAULT_WORKERS = 2

#: 进度报告间隔（每完成 N 只输出一次）
PROGRESS_EVERY = 50


@dataclass
class SyncReport:
    """一次批量同步的结果汇总。

    Attributes:
        total: 计划同步的标的总数。
        synced: 成功（新拉取或缓存命中）的数量。
        failed: 失败清单，元素为 ``(symbol, 错误摘要)``。
        elapsed_seconds: 同步耗时（秒）。
    """

    total: int = 0
    synced: int = 0
    failed: list[tuple[str, str]] = field(default_factory=list)
    elapsed_seconds: float = 0.0


def sync_symbols(
    symbols: list[str],
    period: str = "1d",
    count: int = 1250,
    adjust: str = "forward",
    workers: int = DEFAULT_WORKERS,
    log: Callable[..., None] | None = None,
) -> SyncReport:
    """批量同步标的 K 线到本地缓存（失败容忍，不中断整体）。

    Args:
        symbols: 标的代码列表（带市场后缀）。
        period: K 线周期。
        count: 每只标的同步的 K 线数量。
        adjust: 复权口径。
        workers: 并发线程数（默认 2，调高自担免费源限频风险）。
        log: 进度输出回调（stderr），None 时静默。

    Returns:
        SyncReport 汇总（synced 含缓存命中跳过的标的，缓存层自行判断新鲜度）。
    """
    from datafeed import fetch_ohlcv

    report = SyncReport(total=len(symbols))
    if not symbols:
        return report

    start = time.time()
    done = 0

    def _one(sym: str) -> None:
        fetch_ohlcv(sym, period=period, count=count, adjust=adjust)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(_one, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            done += 1
            try:
                fut.result()
                report.synced += 1
            except Exception as exc:
                report.failed.append((sym, f"{type(exc).__name__}: {exc}"))
            if log and (done % PROGRESS_EVERY == 0 or done == len(symbols)):
                log(
                    f"  进度 {done}/{len(symbols)}："
                    f"成功 {report.synced}，失败 {len(report.failed)}"
                )

    report.elapsed_seconds = time.time() - start
    return report
