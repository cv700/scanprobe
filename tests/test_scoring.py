"""
Unit tests for the scoring module.
Exercises the truth table documented in scoring.py — no hardware required.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dataclasses import dataclass, field
from typing import Optional
from ashiba_scanprobe.scoring import compute_risk_score, _aggregate, WATCH_THRESHOLD, DRAIN_THRESHOLD


# ── Minimal stub dataclasses ─────────────────────────────────────────────────
# We don't import the check modules; stubs match only the fields scoring.py reads.

@dataclass
class FakeNvidia:
    ecc_dbe_volatile: int = 0
    ecc_dbe_aggregate: int = 0
    ecc_sbe_volatile: int = 0
    temperature_gpu: Optional[float] = None
    clock_throttle_reasons: list = field(default_factory=list)
    passed: bool = True
    error: Optional[str] = None

# ── _aggregate unit tests ────────────────────────────────────────────────────

def test_aggregate_empty():
    assert _aggregate([]) == 0.0

def test_aggregate_single():
    assert _aggregate([0.90]) == 0.90

def test_aggregate_geometric_decay():
    # 0.40 + 0.35*0.5 = 0.575
    result = _aggregate([0.40, 0.35])
    assert abs(result - (0.40 + 0.35 * 0.5)) < 1e-9

def test_aggregate_capped_at_one():
    assert _aggregate([0.90, 0.80, 0.70, 0.60]) == 1.0

def test_aggregate_order_independence():
    # Should produce same result regardless of input order
    a = _aggregate([0.35, 0.40])
    b = _aggregate([0.40, 0.35])
    assert a == b


# ── Truth table tests ────────────────────────────────────────────────────────

def score(nvidia=None, xid=None, gpu_index=0):
    return compute_risk_score(
        nvidia_result=nvidia,
        xid_result=xid,
        gpu_index=gpu_index,
    )


def test_all_none_is_healthy():
    rs = score()
    assert rs.score == 0.0
    assert rs.tier == "HEALTHY"


def test_healthy_gpu():
    nv = FakeNvidia(temperature_gpu=65.0)
    rs = score(nvidia=nv)
    assert rs.tier == "HEALTHY"
    assert rs.score < WATCH_THRESHOLD


def test_dbe_volatile_is_drain():
    nv = FakeNvidia(ecc_dbe_volatile=1)
    rs = score(nvidia=nv)
    assert rs.tier == "DRAIN"
    assert rs.score >= DRAIN_THRESHOLD
    assert any("DBE" in r for r in rs.recommendations)


def test_dbe_volatile_multiple_raises_score():
    nv1 = FakeNvidia(ecc_dbe_volatile=1)
    nv3 = FakeNvidia(ecc_dbe_volatile=3)
    rs1 = score(nvidia=nv1)
    rs3 = score(nvidia=nv3)
    assert rs3.score > rs1.score


def test_dbe_aggregate_is_watch():
    nv = FakeNvidia(ecc_dbe_aggregate=1)
    rs = score(nvidia=nv)
    assert rs.tier == "WATCH"
    assert rs.score >= WATCH_THRESHOLD
    assert rs.score < DRAIN_THRESHOLD


def test_sbe_high_stays_healthy():
    # SBE alone (even high count) should not hit WATCH threshold
    nv = FakeNvidia(ecc_sbe_volatile=200)
    rs = score(nvidia=nv)
    assert rs.tier == "HEALTHY"
    assert rs.score < WATCH_THRESHOLD


def test_hw_throttle_is_watch():
    nv = FakeNvidia(clock_throttle_reasons=["HwThermalSlowdown"])
    rs = score(nvidia=nv)
    assert rs.tier == "WATCH"
    assert rs.score >= WATCH_THRESHOLD


def test_sw_throttle_stays_healthy():
    nv = FakeNvidia(clock_throttle_reasons=["SwPowerCap"])
    rs = score(nvidia=nv)
    # SW throttle alone (weight 0.10) should not reach WATCH threshold
    assert rs.tier == "HEALTHY"


def test_temp_critical_is_watch():
    nv = FakeNvidia(temperature_gpu=91.0)
    rs = score(nvidia=nv)
    assert rs.tier == "WATCH"
    assert rs.score >= WATCH_THRESHOLD


def test_temp_elevated_stays_healthy():
    nv = FakeNvidia(temperature_gpu=85.0)
    rs = score(nvidia=nv)
    assert rs.tier == "HEALTHY"


def test_hw_throttle_plus_critical_temp_is_drain():
    # Two WATCH signals should combine to DRAIN
    nv = FakeNvidia(
        clock_throttle_reasons=["HwThermalSlowdown"],
        temperature_gpu=91.0,
    )
    rs = score(nvidia=nv)
    assert rs.tier == "DRAIN"


def test_nvidia_smi_error_is_drain():
    nv = FakeNvidia(passed=False, error="nvidia-smi exit 1: no driver")
    rs = score(nvidia=nv)
    assert rs.tier == "DRAIN"


def test_combined_dbe_plus_xid_saturates_at_one():
    nv = FakeNvidia(ecc_dbe_volatile=2)
    xr = FakeXid(drain_xids_found=[95, 79, 48], passed=False)
    rs = score(nvidia=nv, xid=xr)
    assert rs.score <= 1.0
    assert rs.tier == "DRAIN"


def test_recommendations_populated_for_problem_gpus():
    nv = FakeNvidia(ecc_dbe_volatile=1, temperature_gpu=91.0)
    rs = score(nvidia=nv)
    assert len(rs.recommendations) >= 2


@dataclass
class FakeXid:
    available: bool = True
    passed: bool = True
    error: Optional[str] = None
    events: list = field(default_factory=list)
    drain_xids_found: list = field(default_factory=list)
    watch_xids_found: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

def test_xid_drain_is_drain():
    xr = FakeXid(drain_xids_found=[95], passed=False)
    rs = score(xid=xr)
    assert rs.tier == "DRAIN"
    assert any("Xid" in r for r in rs.recommendations)

def test_xid_watch_is_watch():
    xr = FakeXid(watch_xids_found=[43])
    rs = score(xid=xr)
    assert rs.tier == "WATCH"

def test_xid_unavailable_no_penalty():
    xr = FakeXid(available=False)
    rs = score(xid=xr)
    assert rs.tier == "HEALTHY"
    assert rs.score < WATCH_THRESHOLD
    assert "xid_log_unavailable" in rs.signals

def test_xid_unavailable_surfaces_recommendation():
    xr = FakeXid(available=False, error="dmesg failed — try: sudo scanprobe")
    rs = score(xid=xr)
    assert rs.tier == "HEALTHY"
    assert any("Xid scan unavailable" in r for r in rs.recommendations)

def test_xid_clean_no_penalty():
    xr = FakeXid(available=True)
    rs = score(xid=xr)
    assert rs.tier == "HEALTHY"
    assert rs.score == 0.0


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
