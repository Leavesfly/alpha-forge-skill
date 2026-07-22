"""环境变量配置聚合：集中读取 ALPHA_FORGE_* 环境变量。

将散落在 datafeed.py、naming.py、account.py、data/sources.py、cli_common.py
等模块中的环境变量读取集中到此处，便于：
- 统一文档化所有可配置项
- 测试时注入配置（无需 monkeypatch.setenv）
- 未来扩展为支持 .env 文件

当前采用渐进式迁移：仅 naming.py 和 data/sources.py 已接入，
其余模块保持现有读取方式，后续迭代迁移。
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EnvConfig:
    """ALPHA_FORGE_* 环境变量聚合配置。

    Attributes:
        debug: ALPHA_FORGE_DEBUG=1 时输出完整堆栈。
        data_source: ALPHA_FORGE_DATA_SOURCE 强制指定数据源
            （tickflow/baostock/akshare/yfinance），空串为 auto 模式。
        retries: ALPHA_FORGE_RETRIES 数据拉取重试次数（0 关闭）。
        output_dir: ALPHA_FORGE_OUTPUT_DIR 覆盖默认输出目录。
        account_file: ALPHA_FORGE_ACCOUNT_FILE 覆盖账户文件路径。
        profile_file: ALPHA_FORGE_PROFILE_FILE 覆盖用户风险画像文件路径。
    """

    debug: bool = False
    data_source: str = ""
    retries: int = 2
    output_dir: str = ""
    account_file: str = ""
    profile_file: str = ""


_cached: EnvConfig | None = None


def get_env_config() -> EnvConfig:
    """获取环境变量配置（进程内缓存，首次调用时读取）。

    缓存策略：环境变量在进程生命周期内通常不变，
    缓存避免每次调用都访问 os.environ。
    测试中需修改环境变量时，先调用 reset_env_config() 清除缓存。
    """
    global _cached
    if _cached is None:
        try:
            retries = int(os.environ.get("ALPHA_FORGE_RETRIES", "2"))
        except ValueError:
            retries = 2
        _cached = EnvConfig(
            debug=bool(os.environ.get("ALPHA_FORGE_DEBUG")),
            data_source=os.environ.get("ALPHA_FORGE_DATA_SOURCE", "").strip().lower(),
            retries=max(0, retries),
            output_dir=os.environ.get("ALPHA_FORGE_OUTPUT_DIR", ""),
            account_file=os.environ.get("ALPHA_FORGE_ACCOUNT_FILE", ""),
            profile_file=os.environ.get("ALPHA_FORGE_PROFILE_FILE", ""),
        )
    return _cached


def reset_env_config() -> None:
    """清除缓存（测试用：修改环境变量后调用以重新读取）。"""
    global _cached
    _cached = None
