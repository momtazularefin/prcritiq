from __future__ import annotations

import pytest

from prcritiq.config import AccelerationMode, ConfigError, parse_acceleration


def test_parse_acceleration_defaults_to_none() -> None:
    assert parse_acceleration(None) is AccelerationMode.NONE


def test_parse_acceleration_accepts_gpu_and_npu() -> None:
    assert parse_acceleration("gpu") is AccelerationMode.GPU
    assert parse_acceleration("NPU") is AccelerationMode.NPU


def test_parse_acceleration_rejects_unknown_mode() -> None:
    with pytest.raises(ConfigError, match="ACCELERATION"):
        parse_acceleration("cpu")
