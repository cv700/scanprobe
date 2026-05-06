"""
Unit tests for nvidia-smi CSV parsing — no hardware required.
Uses fixture strings that match real nvidia-smi output format.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ashiba_preflight.checks.nvidia_smi import _parse_line, _decode_throttle_reasons, _parse_int, _parse_float


# ── Fixture builders ──────────────────────────────────────────────────────────

def _csv_line(
    index=0,
    name="NVIDIA H100 SXM5 80GB",
    uuid="GPU-abc123",
    ecc_mode="Enabled",
    sbe_volatile=0,
    dbe_volatile=0,
    sbe_aggregate=0,
    dbe_aggregate=0,
    temp_gpu=72,
    temp_mem=78,
    power_draw=400,
    power_limit=700,
    clock_sm=1980,
    clock_mem=2619,
    throttle="0x0000000000000001",  # GpuIdle
    mem_used=1024,
    mem_total=81920,
    pcie_gen=5,
    pcie_width=16,
):
    return ", ".join(str(v) for v in [
        index, name, uuid, ecc_mode,
        sbe_volatile, dbe_volatile, sbe_aggregate, dbe_aggregate,
        temp_gpu, temp_mem, power_draw, power_limit,
        clock_sm, clock_mem, throttle,
        mem_used, mem_total, pcie_gen, pcie_width,
    ])


# ── _parse_int / _parse_float ────────────────────────────────────────────────

def test_parse_int_basic():
    assert _parse_int("42") == 42

def test_parse_int_na():
    assert _parse_int("N/A") == 0
    assert _parse_int("[Not Supported]") == 0

def test_parse_int_comma_separated():
    assert _parse_int("1,024") == 1024

def test_parse_float_basic():
    assert _parse_float("72.5") == 72.5

def test_parse_float_with_unit():
    assert _parse_float("400 W") == 400.0
    assert _parse_float("72°C") == 72.0
    assert _parse_float("1980 MHz") == 1980.0

def test_parse_float_na():
    assert _parse_float("N/A") is None
    assert _parse_float("[N/A]") is None


# ── Throttle decoder ─────────────────────────────────────────────────────────

def test_decode_idle():
    reasons = _decode_throttle_reasons("0x0000000000000001")
    assert reasons == ["GpuIdle"]

def test_decode_hw_thermal():
    reasons = _decode_throttle_reasons("0x0000000000000040")
    assert "HwThermalSlowdown" in reasons

def test_decode_multiple_reasons():
    # GpuIdle (0x01) | HwSlowdown (0x08)
    reasons = _decode_throttle_reasons("0x0000000000000009")
    assert "GpuIdle" in reasons
    assert "HwSlowdown" in reasons

def test_decode_zero():
    assert _decode_throttle_reasons("0x0000000000000000") == []

def test_decode_invalid():
    assert _decode_throttle_reasons("N/A") == []
    assert _decode_throttle_reasons("") == []


# ── _parse_line ───────────────────────────────────────────────────────────────

def test_healthy_gpu():
    line = _csv_line()
    r = _parse_line(line, 0)
    assert r.passed is True
    assert r.ecc_dbe_volatile == 0
    assert r.ecc_dbe_aggregate == 0
    assert r.temperature_gpu == 72.0
    assert r.name == "NVIDIA H100 SXM5 80GB"
    assert r.clock_throttle_reasons == ["GpuIdle"]
    assert r.warnings == []


def test_dbe_volatile_sets_passed_false():
    line = _csv_line(dbe_volatile=1)
    r = _parse_line(line, 0)
    assert r.passed is False
    assert r.ecc_dbe_volatile == 1
    assert any("DBE" in w for w in r.warnings)


def test_dbe_aggregate_warning_not_fail():
    # Aggregate DBE gets a warning but doesn't set passed=False
    line = _csv_line(dbe_aggregate=3)
    r = _parse_line(line, 0)
    assert r.passed is True  # aggregate alone doesn't hard-fail
    assert r.ecc_dbe_aggregate == 3
    assert any("aggregate" in w for w in r.warnings)


def test_high_sbe_warning():
    line = _csv_line(sbe_volatile=50)
    r = _parse_line(line, 0)
    assert r.ecc_sbe_volatile == 50
    assert any("SBE" in w for w in r.warnings)


def test_temperature_warning():
    line = _csv_line(temp_gpu=87)
    r = _parse_line(line, 0)
    assert r.temperature_gpu == 87.0
    assert any("temperature" in w.lower() for w in r.warnings)


def test_power_at_limit_warning():
    # 98%+ of limit triggers warning
    line = _csv_line(power_draw=690, power_limit=700)
    r = _parse_line(line, 0)
    assert any("Power" in w for w in r.warnings)


def test_power_well_below_limit_no_warning():
    line = _csv_line(power_draw=400, power_limit=700)
    r = _parse_line(line, 0)
    assert not any("Power" in w for w in r.warnings)


def test_hw_thermal_throttle_warning():
    line = _csv_line(throttle="0x0000000000000040")  # HwThermalSlowdown
    r = _parse_line(line, 0)
    assert "HwThermalSlowdown" in r.clock_throttle_reasons
    assert any("throttle" in w.lower() for w in r.warnings)


def test_gpu_idle_only_no_throttle_warning():
    line = _csv_line(throttle="0x0000000000000001")  # GpuIdle only
    r = _parse_line(line, 0)
    assert r.clock_throttle_reasons == ["GpuIdle"]
    assert not any("throttle" in w.lower() for w in r.warnings)


def test_too_few_columns():
    r = _parse_line("0, H100, GPU-abc", 0)
    assert r.passed is False
    assert r.error is not None


def test_ecc_disabled_parses():
    line = _csv_line(ecc_mode="Disabled")
    r = _parse_line(line, 0)
    assert r.ecc_enabled is False


def test_ecc_enabled_parses():
    line = _csv_line(ecc_mode="Enabled")
    r = _parse_line(line, 0)
    assert r.ecc_enabled is True


# ── CLI argument parsing ──────────────────────────────────────────────────────

def test_parse_gpu_list():
    from ashiba_preflight.cli import _parse_gpu_list
    assert _parse_gpu_list("all", 4) == [0, 1, 2, 3]
    assert _parse_gpu_list("0", 8) == [0]
    assert _parse_gpu_list("0,2,3", 8) == [0, 2, 3]
    assert _parse_gpu_list("0-3", 8) == [0, 1, 2, 3]
    assert _parse_gpu_list("1-1", 8) == [1]

def test_parse_gpu_list_deduplicates():
    from ashiba_preflight.cli import _parse_gpu_list
    assert _parse_gpu_list("0,0,1", 4) == [0, 1]


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
            import traceback; traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
