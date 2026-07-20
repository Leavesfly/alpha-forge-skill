"""统一持仓账户：跨命令共享的真实持仓注册表（outputs/account.json）。

动机：run_paper 的状态文件是「单标的 × 单策略」的虚拟盘，而用户的真实
场景是一个账户持有多只股票。本模块提供轻量的持仓登记表，供各命令联动：

- ``run_account.py``：登记 / 查看 / 移除持仓；
- ``run_score.py``：未显式传 ``--cost`` 时自动读取账户持仓（优先于模拟盘探测）；
- ``run_scan.py``：扫描结果标注「已持有」，可 ``--exclude-held`` 排除。

账户文件为单一 JSON（版本化，字段只增不删）::

    {
      "version": 1,
      "updated_at": "2026-07-20 10:00:00",
      "positions": {
        "600000.SH": {"shares": 1000, "cost": 8.5, "note": "", "added_at": "..."}
      }
    }

仅作研究辅助的登记表，不含任何交易执行能力。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

#: 账户文件结构版本（破坏性变更时递增并做迁移）
ACCOUNT_VERSION = 1


def account_path() -> Path:
    """账户文件路径：默认 outputs/account.json；
    环境变量 ``ALPHA_FORGE_ACCOUNT_FILE`` 可覆盖（测试/多账户隔离）。"""
    override = os.environ.get("ALPHA_FORGE_ACCOUNT_FILE")
    if override:
        path = Path(override).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    out_dir = Path(__file__).resolve().parent.parent / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / "account.json"


def load_account() -> dict:
    """读取账户；不存在或损坏时返回空账户（损坏时不静默覆盖，由保存方处理）。"""
    path = account_path()
    if not path.exists():
        return {"version": ACCOUNT_VERSION, "updated_at": None, "positions": {}}
    try:
        acct = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(
            f"账户文件损坏或不可读：{path}（{exc}）。"
            "请手动检查修复，或删除后用 run_account.py --set 重新登记。"
        ) from exc
    acct.setdefault("version", ACCOUNT_VERSION)
    acct.setdefault("positions", {})
    return acct


def save_account(acct: dict) -> Path:
    """保存账户（原子写：先写临时文件再替换，避免中断产生半个 JSON）。"""
    acct["version"] = ACCOUNT_VERSION
    acct["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    path = account_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(acct, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)
    return path


def set_position(
    symbol: str, shares: float, cost: float, note: str = ""
) -> dict:
    """登记/更新一笔持仓（shares<=0 视为参数错误，移除请用 remove_position）。"""
    if shares <= 0:
        raise ValueError(f"持仓数量应为正数，收到 {shares}；清仓请用 --remove。")
    if cost <= 0:
        raise ValueError(f"持仓成本应为正数，收到 {cost}。")
    acct = load_account()
    prev = acct["positions"].get(symbol) or {}
    acct["positions"][symbol] = {
        "shares": shares,
        "cost": cost,
        "note": note or prev.get("note", ""),
        "added_at": prev.get("added_at") or time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_account(acct)
    return acct


def remove_position(symbol: str) -> dict:
    """移除一笔持仓；标的不存在时报错并列出当前持仓便于纠正。"""
    acct = load_account()
    if symbol not in acct["positions"]:
        held = ", ".join(sorted(acct["positions"])) or "（空）"
        raise ValueError(f"账户中没有 {symbol} 的持仓；当前持仓：{held}")
    del acct["positions"][symbol]
    save_account(acct)
    return acct


def get_position(symbol: str) -> dict | None:
    """查询单标的持仓；无持仓返回 None。"""
    pos = load_account()["positions"].get(symbol)
    if not pos:
        return None
    return {**pos, "source": "account"}


def held_symbols() -> list[str]:
    """返回账户当前持有的全部标的代码（排序稳定）。"""
    return sorted(load_account()["positions"])
