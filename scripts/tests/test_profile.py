"""用户风险画像模块测试。"""

from __future__ import annotations

import pytest

from profile import (
    RISK_PRESETS,
    effective_risk_params,
    load_profile,
    reset_profile,
    set_profile,
)


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    """每个测试用独立画像文件，避免互相污染。"""
    from envconfig import reset_env_config

    monkeypatch.setenv("ALPHA_FORGE_PROFILE_FILE", str(tmp_path / "profile.json"))
    reset_env_config()
    yield
    reset_env_config()


class TestSetProfile:
    def test_preset_fills_defaults(self):
        prof = set_profile(risk_tolerance="balanced", capital=200000)
        assert prof["risk_tolerance"] == "balanced"
        assert prof["capital"] == 200000
        # 预设自动填充
        assert prof["risk_pct"] == RISK_PRESETS["balanced"]["risk_pct"]
        assert prof["max_drawdown"] == RISK_PRESETS["balanced"]["max_drawdown"]

    def test_explicit_overrides_preset(self):
        prof = set_profile(risk_tolerance="aggressive", max_drawdown=0.4)
        assert prof["max_drawdown"] == 0.4  # 显式值优先
        assert prof["risk_pct"] == RISK_PRESETS["aggressive"]["risk_pct"]

    def test_incremental_merge(self):
        set_profile(risk_tolerance="conservative", capital=100000)
        prof = set_profile(capital=300000)  # 只改资金
        assert prof["capital"] == 300000
        assert prof["risk_tolerance"] == "conservative"  # 保留原值

    def test_invalid_tolerance(self):
        with pytest.raises(ValueError, match="风险偏好"):
            set_profile(risk_tolerance="yolo")

    def test_invalid_risk_pct(self):
        with pytest.raises(ValueError, match="risk_pct"):
            set_profile(risk_pct=0.5)  # 超过 0.1 上限

    def test_invalid_max_drawdown(self):
        with pytest.raises(ValueError, match="max_drawdown"):
            set_profile(max_drawdown=1.5)


class TestLoadReset:
    def test_load_none_when_absent(self):
        assert load_profile() is None

    def test_load_after_set(self):
        set_profile(risk_tolerance="balanced")
        prof = load_profile()
        assert prof is not None
        assert prof["risk_tolerance"] == "balanced"
        assert "updated_at" in prof

    def test_reset(self):
        set_profile(risk_tolerance="balanced")
        reset_profile()
        assert load_profile() is None

    def test_reset_idempotent(self):
        reset_profile()  # 不存在时静默
        assert load_profile() is None


class TestEffectiveRiskParams:
    def test_no_profile_returns_none(self):
        params = effective_risk_params()
        assert params["capital"] is None
        assert params["risk_pct"] is None
        assert params["source"] is None

    def test_with_profile(self):
        set_profile(risk_tolerance="conservative", capital=50000)
        params = effective_risk_params()
        assert params["capital"] == 50000
        assert params["risk_pct"] == RISK_PRESETS["conservative"]["risk_pct"]
        assert params["risk_tolerance"] == "conservative"
        assert params["source"] == "profile"
