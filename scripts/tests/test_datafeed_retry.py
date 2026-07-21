"""数据层重试与降级回归测试：mock 数据源，不走网络。"""

from __future__ import annotations

import pandas as pd
import pytest

import datafeed
from tests.helpers import make_ohlcv


class FakeSource:
    """可编程失败次数的假数据源。"""

    def __init__(self, name: str, fail_times: int = 0):
        self.name = name
        self.fail_times = fail_times
        self.calls = 0

    def supports(self, symbol: str, period: str) -> bool:
        return True

    def fetch(self, symbol, period, count, adjust) -> pd.DataFrame:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError(f"{self.name} 模拟网络失败 #{self.calls}")
        df = make_ohlcv([100.0, 101.0, 102.0])
        df.attrs["source"] = self.name
        return df


@pytest.fixture
def no_sleep(monkeypatch):
    """替换 time.sleep：免等待并记录退避序列。"""
    waits: list[float] = []
    monkeypatch.setattr(datafeed.time, "sleep", waits.append)
    return waits


def test_retry_then_success(monkeypatch, no_sleep):
    """失败 2 次后成功：共调用 3 次，退避 1s/2s 指数递增。"""
    monkeypatch.setenv("ALPHA_FORGE_RETRIES", "2")
    source = FakeSource("flaky", fail_times=2)
    df = datafeed._fetch_with_retry(source, "600000.SH", "1d", 100, "forward")
    assert source.calls == 3
    assert no_sleep == [1.0, 2.0]
    assert len(df) == 3


def test_retry_exhausted_raises(monkeypatch, no_sleep):
    """重试耗尽仍失败：抛出最后一次异常，共调用 retries+1 次。"""
    monkeypatch.setenv("ALPHA_FORGE_RETRIES", "2")
    source = FakeSource("dead", fail_times=99)
    with pytest.raises(ConnectionError):
        datafeed._fetch_with_retry(source, "600000.SH", "1d", 100, "forward")
    assert source.calls == 3


def test_retries_zero_disables_retry(monkeypatch, no_sleep):
    """ALPHA_FORGE_RETRIES=0 关闭重试：只调用 1 次且不等待。"""
    monkeypatch.setenv("ALPHA_FORGE_RETRIES", "0")
    source = FakeSource("dead", fail_times=99)
    with pytest.raises(ConnectionError):
        datafeed._fetch_with_retry(source, "600000.SH", "1d", 100, "forward")
    assert source.calls == 1
    assert no_sleep == []


def test_retry_config_invalid_env_falls_back(monkeypatch):
    """非法环境变量值回落到默认 2 次。"""
    from envconfig import reset_env_config

    monkeypatch.setenv("ALPHA_FORGE_RETRIES", "abc")
    reset_env_config()
    retries, backoff = datafeed._retry_config()
    assert retries == 2
    assert backoff == 1.0
    # 负数被钳制为 0
    monkeypatch.setenv("ALPHA_FORGE_RETRIES", "-3")
    reset_env_config()
    assert datafeed._retry_config()[0] == 0


def test_fallback_to_next_source(monkeypatch, no_sleep, capsys):
    """主源重试后仍失败：降级次源并输出 stderr 告警。"""
    monkeypatch.setenv("ALPHA_FORGE_RETRIES", "1")
    primary = FakeSource("primary", fail_times=99)
    backup = FakeSource("backup", fail_times=0)
    monkeypatch.setattr(datafeed, "get_sources", lambda: [primary, backup])

    df = datafeed._fetch_ohlcv_raw("600000.SH", "1d", 100, "forward")
    assert df.attrs["source"] == "backup"
    assert primary.calls == 2  # 1 次 + 1 次重试
    assert backup.calls == 1
    err = capsys.readouterr().err
    assert "重试" in err
    assert "降级" in err


def test_all_sources_fail_raises_summary(monkeypatch, no_sleep):
    """所有源均失败：抛 RuntimeError 并汇总各源错误。"""
    monkeypatch.setenv("ALPHA_FORGE_RETRIES", "0")
    s1 = FakeSource("s1", fail_times=99)
    s2 = FakeSource("s2", fail_times=99)
    monkeypatch.setattr(datafeed, "get_sources", lambda: [s1, s2])

    with pytest.raises(RuntimeError, match="2 个数据源"):
        datafeed._fetch_ohlcv_raw("600000.SH", "1d", 100, "forward")
