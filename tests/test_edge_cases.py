"""
Edge case and error path tests.

Tests that nvidia-smi failures, missing tools, malformed output, and
boundary conditions are handled correctly and never crash.

All tests are hardware-free.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ashiba_scanprobe.checks.nvidia_smi import (
    check_all_nvidia_smi, count_gpus, _parse_int, _parse_float,
    _decode_throttle_reasons,
)
from ashiba_scanprobe.scoring import compute_risk_score
import unittest
from unittest.mock import patch, MagicMock
import importlib.util
from pathlib import Path


# ── nvidia-smi failure modes ──────────────────────────────────────────────────

def test_nvidia_smi_not_found_returns_error():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        results = check_all_nvidia_smi([0, 1])
    assert results[0].error is not None
    assert not results[0].passed
    assert results[1].error is not None

def test_nvidia_smi_timeout_returns_error():
    import subprocess
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("nvidia-smi", 30)):
        results = check_all_nvidia_smi([0])
    assert not results[0].passed
    assert "timed out" in results[0].error.lower()

def test_nvidia_smi_nonzero_exit_returns_error():
    fake = MagicMock()
    fake.returncode = 1
    fake.stderr = "NVIDIA-SMI has failed"
    fake.stdout = ""
    with patch("subprocess.run", return_value=fake):
        results = check_all_nvidia_smi([0])
    assert not results[0].passed

def test_nvidia_smi_fewer_gpus_than_requested():
    """If we ask for GPU 7 but nvidia-smi only reports 4, handle cleanly."""
    fake = MagicMock()
    fake.returncode = 0
    # Only 2 GPUs in output
    fake.stdout = (
        "0, Tesla T4, GPU-abc, Enabled, 0, 0, 0, 0, 45, 40, 70.5, 70.0, "
        "585, 5000, 0x0000000000000000, 1234, 16160, 4, 16\n"
        "1, Tesla T4, GPU-def, Enabled, 0, 0, 0, 0, 46, 41, 71.0, 70.0, "
        "585, 5000, 0x0000000000000000, 1234, 16160, 4, 16\n"
    )
    with patch("subprocess.run", return_value=fake):
        results = check_all_nvidia_smi([0, 1, 7])
    assert results[0].passed
    assert results[1].passed
    assert not results[7].passed
    assert "not found" in results[7].error.lower()

def test_count_gpus_not_found_returns_zero():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        assert count_gpus() == 0

def test_count_gpus_nonzero_exit_returns_zero():
    fake = MagicMock()
    fake.returncode = 1
    with patch("subprocess.run", return_value=fake):
        assert count_gpus() == 0


# ── Parser edge cases ─────────────────────────────────────────────────────────

def test_parse_int_na_values():
    for s in ("N/A", "[Not Supported]", "[N/A]", ""):
        assert _parse_int(s) == 0

def test_parse_int_comma_separated():
    assert _parse_int("1,234") == 1234

def test_parse_float_na_values():
    for s in ("N/A", "[Not Supported]", "[N/A]", ""):
        assert _parse_float(s) is None

def test_parse_float_strips_units():
    """nvidia-smi may include units even with nounits flag on some drivers."""
    assert _parse_float("72 C") == 72.0
    assert _parse_float("250.5 W") == 250.5
    assert _parse_float("1234 MiB") == 1234.0
    assert _parse_float("1410 MHz") == 1410.0


# ── Throttle bitmask edge cases ───────────────────────────────────────────────

def test_throttle_zero_returns_empty():
    assert _decode_throttle_reasons("0x0000000000000000") == []

def test_throttle_gpu_idle_only():
    reasons = _decode_throttle_reasons("0x0000000000000001")
    assert reasons == ["GpuIdle"]

def test_throttle_hw_thermal():
    reasons = _decode_throttle_reasons("0x0000000000000040")
    assert "HwThermalSlowdown" in reasons

def test_throttle_multiple_flags():
    # GpuIdle (0x1) | SwPowerCap (0x4)
    reasons = _decode_throttle_reasons("0x0000000000000005")
    assert "GpuIdle" in reasons
    assert "SwPowerCap" in reasons

def test_throttle_na_returns_empty():
    assert _decode_throttle_reasons("N/A") == []

def test_throttle_invalid_hex_returns_empty():
    assert _decode_throttle_reasons("not_a_hex_value") == []

def test_throttle_decimal_string():
    """Some driver versions return decimal instead of hex."""
    # 0x40 = 64 decimal = HwThermalSlowdown
    # If the driver returns "64" instead of "0x40", we need to handle it.
    # Current implementation may not handle this — mark as known issue if it fails.
    # This test documents the expected behavior when it's fixed.
    pass  # TODO: validate on real hardware what format this field uses


# ── Scoring edge cases ────────────────────────────────────────────────────────

def test_none_inputs_score_zero():
    rs = compute_risk_score()
    assert rs.score == 0.0
    assert rs.tier == "HEALTHY"

def test_score_never_exceeds_one():
    """No combination of signals should produce score > 1.0."""
    from tests.test_scoring import FakeNvidia, FakeXid
    nv = FakeNvidia(ecc_dbe_volatile=99, temperature_gpu=120.0,
                    clock_throttle_reasons=["HwThermalSlowdown"])
    xr = FakeXid(drain_xids_found=[95, 79, 48], passed=False)
    rs = compute_risk_score(nvidia_result=nv, xid_result=xr)
    assert rs.score <= 1.0

def test_score_is_deterministic():
    """Same inputs always produce same score."""
    from tests.test_scoring import FakeNvidia
    nv = FakeNvidia(ecc_dbe_volatile=1, temperature_gpu=85.0)
    rs1 = compute_risk_score(nvidia_result=nv)
    rs2 = compute_risk_score(nvidia_result=nv)
    assert rs1.score == rs2.score
    assert rs1.tier == rs2.tier


def test_single_file_xid_classification_matches_package():
    """The curl edition must not drift from the installable package Xid table."""
    from ashiba_scanprobe.checks import xid as package_xid

    path = Path(__file__).resolve().parents[1] / "scanprobe.py"
    spec = importlib.util.spec_from_file_location("scanprobe_single_file", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.DRAIN_XIDS == package_xid.DRAIN_XIDS
    assert module.WATCH_XIDS == package_xid.WATCH_XIDS
    for code in module.DRAIN_XIDS | module.WATCH_XIDS:
        assert module.XID_DESC[code] == package_xid.XID_DESCRIPTIONS[code]


# ── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
