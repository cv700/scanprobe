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
# We don't import the check modules (would pull in torch etc.),
# just match the fields that scoring.py reads.

@dataclass
class FakeNvidia:
    ecc_dbe_volatile: int = 0
    ecc_dbe_aggregate: int = 0
    ecc_sbe_volatile: int = 0
    temperature_gpu: Optional[float] = None
    clock_throttle_reasons: list = field(default_factory=list)
    passed: bool = True
    error: Optional[str] = None

@dataclass
class FakeDcgm:
    available: bool = True
    passed: bool = True
    failed_tests: list = field(default_factory=list)
    level: int = 1

@dataclass
class FakeMatmul:
    error: Optional[str] = None
    num_shapes_anomalous: int = 0
    num_shapes_tested: int = 18
    max_relative_l2: float = 0.0

@dataclass
class FakeCollective:
    error: Optional[str] = None
    outlier_ranks: list = field(default_factory=list)
    cluster_median_ms: float = 50.0
    rank_p50_ms: dict = field(default_factory=dict)


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

def score(nvidia=None, dcgm=None, matmul=None, collective=None, xid=None, gpu_index=0):
    return compute_risk_score(nvidia, dcgm, matmul, collective,
                              xid_result=xid, gpu_index=gpu_index)


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


def test_dcgm_failure_is_drain():
    dc = FakeDcgm(passed=False, failed_tests=["Memory Bandwidth FAIL"])
    rs = score(dcgm=dc)
    assert rs.tier == "DRAIN"
    assert any("DCGM" in r for r in rs.recommendations)


def test_dcgm_unavailable_no_penalty():
    dc = FakeDcgm(available=False, passed=False)
    rs = score(dcgm=dc)
    assert rs.tier == "HEALTHY"
    assert rs.score == 0.0


def test_dcgm_pass_no_penalty():
    dc = FakeDcgm(available=True, passed=True)
    rs = score(dcgm=dc)
    assert rs.tier == "HEALTHY"


def test_matmul_majority_anomalous_is_watch():
    mm = FakeMatmul(num_shapes_anomalous=10, num_shapes_tested=18, max_relative_l2=0.05)
    rs = score(matmul=mm)
    assert rs.tier == "WATCH"


def test_matmul_all_anomalous_is_watch_approaching_drain():
    mm = FakeMatmul(num_shapes_anomalous=18, num_shapes_tested=18, max_relative_l2=0.2)
    rs = score(matmul=mm)
    assert rs.tier in ("WATCH", "DRAIN")


def test_matmul_clean_no_penalty():
    mm = FakeMatmul(num_shapes_anomalous=0, num_shapes_tested=18)
    rs = score(matmul=mm)
    assert rs.tier == "HEALTHY"
    assert rs.score == 0.0


def test_collective_outlier_3sigma_is_watch():
    cr = FakeCollective(
        outlier_ranks=[{"rank": 0, "p50_ms": 250.0, "sigma_above_median": 3.5}],
        cluster_median_ms=50.0,
    )
    cr.rank_p50_ms = {0: 250.0}
    rs = score(collective=cr, gpu_index=0)
    assert rs.tier == "WATCH"


def test_collective_outlier_only_flags_affected_rank():
    cr = FakeCollective(
        outlier_ranks=[{"rank": 0, "p50_ms": 250.0, "sigma_above_median": 3.5}],
        cluster_median_ms=50.0,
    )
    cr.rank_p50_ms = {0: 250.0, 1: 48.0}
    rs_bad = score(collective=cr, gpu_index=0)
    rs_good = score(collective=cr, gpu_index=1)
    assert rs_bad.tier == "WATCH"
    assert rs_good.tier == "HEALTHY"


def test_nvidia_smi_error_is_drain():
    nv = FakeNvidia(passed=False, error="nvidia-smi exit 1: no driver")
    rs = score(nvidia=nv)
    assert rs.tier == "DRAIN"


def test_combined_dbe_plus_dcgm_saturates_at_one():
    nv = FakeNvidia(ecc_dbe_volatile=2)
    dc = FakeDcgm(passed=False, failed_tests=["test1", "test2", "test3"])
    rs = score(nvidia=nv, dcgm=dc)
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
    events: list = field(default_factory=list)
    drain_xids_found: list = field(default_factory=list)
    watch_xids_found: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

def test_xid_drain_is_drain():
    xr = FakeXid(drain_xids_found=[94], passed=False)
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
    assert rs.score == 0.0

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
