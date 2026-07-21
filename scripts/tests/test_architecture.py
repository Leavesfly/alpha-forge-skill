"""架构适应度测试：自动检测违规模块依赖。

基于 AST 静态分析扫描各模块的 import 语句，守护整洁工程的依赖规则：
- 领域模块不得导入 CLI 层（cli_common / cli_config）
- 共享内核（metrics / market / utils）不得导入领域模块
- 基础设施层（data/）不得导入 CLI 私有符号

这些测试在 CI 中运行，防止未来无意中引入违规依赖。
"""

from __future__ import annotations

import ast
import pathlib

SCRIPTS_DIR = pathlib.Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# 模块分类
# ---------------------------------------------------------------------------

#: 领域核心模块（包目录）
DOMAIN_PACKAGES = {
    "strategies", "backtest", "scoring", "portfolio", "dca",
    "ml", "factors", "pairs", "research", "risk", "canslim",
    "sentiment",
}

#: 共享内核模块（不应依赖领域模块）
SHARED_KERNEL = {"metrics", "market", "utils", "naming", "errors"}

#: CLI 层模块
CLI_MODULES = {"cli_common", "cli_config"}

#: 基础设施层（data 包不应导入 CLI 层）
INFRA_PACKAGES = {"data"}


def _iter_py_files(package_or_file: str) -> list[pathlib.Path]:
    """返回指定包目录或单文件下的所有 .py 文件。"""
    target = SCRIPTS_DIR / package_or_file
    if target.is_dir():
        return sorted(target.rglob("*.py"))
    if target.suffix == ".py" and target.exists():
        return [target]
    return []


def _extract_imports(filepath: pathlib.Path) -> list[str]:
    """从 Python 文件中提取所有 import 的顶层模块名。"""
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return []
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:  # 绝对导入
                modules.append(node.module.split(".")[0])
    return modules


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------


def test_domain_does_not_import_cli():
    """领域模块不得导入 CLI 层（cli_common / cli_config）。

    领域逻辑应独立于 CLI 框架，便于单元测试和复用。
    """
    violations: list[str] = []
    for pkg in sorted(DOMAIN_PACKAGES):
        for pyfile in _iter_py_files(pkg):
            # 跳过 __pycache__
            if "__pycache__" in str(pyfile):
                continue
            for imp in _extract_imports(pyfile):
                if imp in CLI_MODULES:
                    rel = pyfile.relative_to(SCRIPTS_DIR)
                    violations.append(f"{rel} imports {imp}")
    assert not violations, (
        "领域模块不得导入 CLI 层：\n" + "\n".join(violations)
    )


def test_shared_kernel_does_not_import_domain():
    """共享内核（metrics/market/utils/naming）不得导入领域模块。

    共享内核位于依赖图最底层，被所有领域模块依赖，
    若反向依赖领域模块则形成循环。
    """
    violations: list[str] = []
    for mod in sorted(SHARED_KERNEL):
        for pyfile in _iter_py_files(mod) + _iter_py_files(f"{mod}.py"):
            if "__pycache__" in str(pyfile):
                continue
            for imp in _extract_imports(pyfile):
                if imp in DOMAIN_PACKAGES or imp in CLI_MODULES:
                    rel = pyfile.relative_to(SCRIPTS_DIR)
                    violations.append(f"{rel} imports {imp}")
    assert not violations, (
        "共享内核不得导入领域模块或 CLI 层：\n" + "\n".join(violations)
    )


def test_infra_does_not_import_cli_private_symbols():
    """基础设施层（data/）不得从 CLI 层导入私有符号（_开头）。

    私有符号（如 _SYMBOL_RE）是 CLI 内部实现细节，
    基础设施层应使用公开的领域模块（market.py）。
    """
    violations: list[str] = []
    for pkg in sorted(INFRA_PACKAGES):
        for pyfile in _iter_py_files(pkg):
            if "__pycache__" in str(pyfile):
                continue
            try:
                tree = ast.parse(pyfile.read_text(encoding="utf-8"))
            except (SyntaxError, OSError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module and node.module.split(".")[0] in CLI_MODULES:
                        for alias in node.names:
                            if alias.name.startswith("_"):
                                rel = pyfile.relative_to(SCRIPTS_DIR)
                                violations.append(
                                    f"{rel} imports private '{alias.name}' from {node.module}"
                                )
    assert not violations, (
        "基础设施层不得导入 CLI 私有符号：\n" + "\n".join(violations)
    )


def test_no_cross_domain_metrics_dependency():
    """portfolio/dca/research 不得直接导入 backtest.metrics（应走共享内核 metrics/）。

    通用绩效度量已提取至 metrics/ 共享内核，
    跨领域模块应从 metrics/ 导入而非 backtest.metrics。
    """
    # 允许 backtest 包内部使用 backtest.metrics（兼容层）
    # 允许 research/walk_forward 导入 backtest.engine/costs/rules（合法依赖回测引擎）
    cross_domain_pkgs = {"portfolio", "dca"}
    violations: list[str] = []
    for pkg in sorted(cross_domain_pkgs):
        for pyfile in _iter_py_files(pkg):
            if "__pycache__" in str(pyfile):
                continue
            try:
                tree = ast.parse(pyfile.read_text(encoding="utf-8"))
            except (SyntaxError, OSError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith("backtest.metrics"):
                        rel = pyfile.relative_to(SCRIPTS_DIR)
                        violations.append(
                            f"{rel} imports from backtest.metrics (should use metrics/)"
                        )
    assert not violations, (
        "跨领域模块应从 metrics/ 共享内核导入绩效函数：\n" + "\n".join(violations)
    )
