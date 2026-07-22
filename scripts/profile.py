"""用户风险画像：跨命令共享的个性化风控偏好（outputs/profile.json）。

动机：不同用户的风险承受能力与资金规模差异巨大。本模块提供轻量的画像登记表，
让评分/扫描/定投等命令的建议仓位与风控提示**因人而异**，而非一套固定默认值。

联动方式（显式 CLI 参数始终优先）：
- ``run_profile.py``：登记 / 查看 / 重置画像；
- ``run_score.py``：未显式传 ``--capital``/``--risk-pct`` 时读取画像的建议仓位参数；
- 画像含 ``max_drawdown`` 时，回测/扫描类输出可据此给出个性化回撤告警。

画像文件为单一 JSON（版本化，字段只增不删）::

    {
      "version": 1,
      "updated_at": "2026-07-22 10:00:00",
      "risk_tolerance": "balanced",
      "capital": 100000,
      "risk_pct": 0.01,
      "max_drawdown": 0.15,
      "max_single_position": 0.3,
      "note": ""
    }

仅作研究辅助的偏好登记，不含任何交易执行能力。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from naming import outputs_dir

#: 画像文件结构版本（破坏性变更时递增并做迁移）
PROFILE_VERSION = 1

#: 风险偏好预设：保守 / 平衡 / 激进
#: 每档给出建议的单笔风险预算比例与可接受最大回撤（用于个性化提示）
RISK_PRESETS: dict[str, dict] = {
    "conservative": {
        "label": "保守",
        "risk_pct": 0.005,
        "max_drawdown": 0.10,
        "max_single_position": 0.2,
    },
    "balanced": {
        "label": "平衡",
        "risk_pct": 0.01,
        "max_drawdown": 0.20,
        "max_single_position": 0.3,
    },
    "aggressive": {
        "label": "激进",
        "risk_pct": 0.02,
        "max_drawdown": 0.35,
        "max_single_position": 0.5,
    },
}


def profile_path() -> Path:
    """画像文件路径：默认 outputs/profile.json；
    环境变量 ``ALPHA_FORGE_PROFILE_FILE`` 可覆盖（测试/多用户隔离）。"""
    from envconfig import get_env_config

    override = get_env_config().profile_file
    if override:
        path = Path(override).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
    return outputs_dir() / "profile.json"


def load_profile() -> dict | None:
    """读取画像；不存在返回 None（表示用户未登记，命令走各自默认值）。

    文件损坏时抛 RuntimeError（不静默覆盖，由保存方/用户处理）。
    """
    path = profile_path()
    if not path.exists():
        return None
    try:
        prof = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(
            f"画像文件损坏或不可读：{path}（{exc}）。"
            "请手动检查修复，或删除后用 run_profile.py --set 重新登记。"
        ) from exc
    prof.setdefault("version", PROFILE_VERSION)
    return prof


def save_profile(prof: dict) -> Path:
    """保存画像（原子写：先写临时文件再替换，避免中断产生半个 JSON）。"""
    prof["version"] = PROFILE_VERSION
    prof["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    path = profile_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(prof, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def set_profile(
    risk_tolerance: str | None = None,
    capital: float | None = None,
    risk_pct: float | None = None,
    max_drawdown: float | None = None,
    max_single_position: float | None = None,
    note: str | None = None,
) -> dict:
    """登记/更新画像（增量合并：未传的字段保留原值）。

    ``risk_tolerance`` 取 conservative/balanced/aggressive 时，会用预设填充
    未显式给出的 risk_pct/max_drawdown/max_single_position（显式值优先）。
    """
    if risk_tolerance is not None and risk_tolerance not in RISK_PRESETS:
        available = ", ".join(RISK_PRESETS)
        raise ValueError(f"风险偏好应为 {available} 之一，收到 '{risk_tolerance}'。")
    if risk_pct is not None and not 0 < risk_pct <= 0.1:
        raise ValueError(f"单笔风险比例 risk_pct 应在 (0, 0.1] 区间，收到 {risk_pct}。")
    if max_drawdown is not None and not 0 < max_drawdown <= 1:
        raise ValueError(f"最大回撤 max_drawdown 应在 (0, 1] 区间，收到 {max_drawdown}。")
    if capital is not None and capital < 0:
        raise ValueError(f"可用资金 capital 不能为负，收到 {capital}。")

    prof = load_profile() or {}
    preset = RISK_PRESETS.get(risk_tolerance, {}) if risk_tolerance else {}

    if risk_tolerance is not None:
        prof["risk_tolerance"] = risk_tolerance
    # 预设填充（仅当用户未显式给出且当前无值时）
    for key in ("risk_pct", "max_drawdown", "max_single_position"):
        explicit = {"risk_pct": risk_pct, "max_drawdown": max_drawdown,
                    "max_single_position": max_single_position}[key]
        if explicit is not None:
            prof[key] = explicit
        elif key not in prof and key in preset:
            prof[key] = preset[key]
    if capital is not None:
        prof["capital"] = capital
    if note is not None:
        prof["note"] = note

    save_profile(prof)
    return prof


def reset_profile() -> None:
    """删除画像文件（恢复命令默认行为）；不存在时静默。"""
    path = profile_path()
    if path.exists():
        path.unlink()


def effective_risk_params(log=None) -> dict:
    """返回画像生效的风控参数（供 run_score 等命令在未显式传参时读取）。

    Returns:
        ``{capital, risk_pct, max_drawdown, max_single_position, risk_tolerance, source}``；
        未登记画像时各字段为 None（调用方据此回退到自身默认值）。
    """
    try:
        prof = load_profile()
    except RuntimeError:
        prof = None
    if not prof:
        return {
            "capital": None, "risk_pct": None, "max_drawdown": None,
            "max_single_position": None, "risk_tolerance": None, "source": None,
        }
    if log:
        tol = prof.get("risk_tolerance")
        label = RISK_PRESETS.get(tol, {}).get("label", tol or "自定义")
        log(f"读取用户风险画像：{label}（显式 CLI 参数优先）")
    return {
        "capital": prof.get("capital"),
        "risk_pct": prof.get("risk_pct"),
        "max_drawdown": prof.get("max_drawdown"),
        "max_single_position": prof.get("max_single_position"),
        "risk_tolerance": prof.get("risk_tolerance"),
        "source": "profile",
    }
