"""领域异常层次：统一的可预期异常分类。

共享内核层模块，被所有领域模块和基础设施层使用，
由 CLI 适配层（cli_common.run_cli）统一映射为退出码与 stderr 输出。

异常层次::

    AlphaForgeError（基类）
    ├── ValidationError      参数/格式错误 → exit 2
    ├── DataFetchError       数据拉取失败 → exit 1
    └── InsufficientDataError 数据不足 → exit 1

设计原则：
- 领域模块抛出语义化异常（如 ``DataFetchError``），不再直接使用
  ``RuntimeError``/``ValueError``（旧代码保持兼容，run_cli 同时映射两者）；
- 异常消息面向终端用户，包含「怎么改」的可操作提示；
- 本模块零外部依赖（仅继承内置 Exception），可被任何层级安全导入。
"""

from __future__ import annotations


class AlphaForgeError(Exception):
    """Alpha Forge 领域异常基类。

    所有可预期的业务异常继承此类，CLI 层据此区分「可预期错误」
    与「未预期异常」，给出不同的退出码与错误格式。
    """


class ValidationError(AlphaForgeError):
    """参数/格式校验失败（→ exit 2）。

    典型场景：标的代码格式非法、策略参数组合不合法、配置文件含未知键。
    """


class DataFetchError(AlphaForgeError):
    """数据拉取失败（→ exit 1）。

    典型场景：所有数据源均不可用、网络超时、API Key 缺失。
    """


class InsufficientDataError(AlphaForgeError):
    """数据量不足以完成计算（→ exit 1）。

    典型场景：K 线数量低于策略/评分所需最小窗口、多标的对齐后为空。
    """
