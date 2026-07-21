"""模拟盘状态文件管理（原子读写、幂等保障）。"""

from __future__ import annotations

import json
from pathlib import Path

from naming import outputs_dir, sanitize


def state_path(symbol: str, strategy: str) -> Path:
    """模拟盘状态文件路径：outputs/paper_<标的>_<策略>.json。"""
    return outputs_dir() / f"paper_{sanitize(symbol)}_{sanitize(strategy)}.json"


def load_state(path: Path) -> dict | None:
    """读取状态文件；不存在返回 None。"""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: dict) -> None:
    """保存状态文件（JSON 格式）。"""
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
