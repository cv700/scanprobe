"""
Risk score aggregation.

Combines the current validated scan signals into a per-GPU risk score [0.0, 1.0]
and a HEALTHY / WATCH / DRAIN tier.

Scoring model
─────────────
Signals are assigned a weight in [0, 1] reflecting their severity:

  Drain-class  (weight ≥ 0.50): any single one of these → DRAIN
    - DBE ECC volatile errors:   0.90   uncorrectable, near-certain fault
    - nvidia-smi outright error: 0.55   can't even query the GPU
    - Drain-class Xid event:     0.85   kernel log reports hardware fault

  Watch-class  (weight 0.20–0.45): one → WATCH; two together can push to DRAIN
    - HW thermal throttle:       0.40
    - Temperature > 88°C:        0.35
    - DBE ECC aggregate > 0:     0.30   lifetime errors, not just current session
    - Watch-class Xid event:     0.25

  Monitor-class (weight 0.05–0.15): don't change tier alone
    - Temperature 83–88°C:       0.12
    - SBE ECC > 100:             0.15
    - SBE ECC 10–100:            0.05
    - SW throttle active:        0.10
    - Xid log unavailable:       0.05

Aggregation: geometric decay
    score = w[0] + w[1]*0.5 + w[2]*0.25 + ...  (weights sorted descending)
    score = min(1.0, score)

This means:
  - The strongest signal dominates
  - Each additional signal contributes half as much as the previous
  - Two WATCH signals (0.35 + 0.30*0.5 = 0.50) can reach DRAIN
  - Monitor signals alone stay well below WATCH threshold
"""

from dataclasses import dataclass, field

# No external dependencies — pure stdlib math only


@dataclass
class RiskScore:
    gpu_index: int
    score: float = 0.0
    tier: str = "HEALTHY"           # HEALTHY / WATCH / DRAIN
    signals: dict = field(default_factory=dict)    # name -> weight
    recommendations: list = field(default_factory=list)


WATCH_THRESHOLD = 0.20
DRAIN_THRESHOLD = 0.50


def _aggregate(weights: list) -> float:
    """
    Geometric decay aggregation: dominant signal + each additional at half weight.
    Prevents a pile of minor signals from mimicking a true drain event.
    """
    if not weights:
        return 0.0
    weights = sorted(weights, reverse=True)
    score = 0.0
    for i, w in enumerate(weights):
        score += w * (0.5 ** i)
    return min(1.0, score)


def compute_risk_score(
    nvidia_result=None,
    xid_result=None,
    gpu_index: int = 0,
) -> RiskScore:
    """
    Aggregate check results into a risk score for a single GPU.
    Missing checks do not penalize the score.
    """
    rs = RiskScore(gpu_index=gpu_index)
    signals = {}
    recs = []

    # ── nvidia-smi signals ──────────────────────────────────────────────────
    if nvidia_result is not None:

        if nvidia_result.error and not nvidia_result.passed:
            signals["nvidia_smi_error"] = 0.55
            recs.append(f"nvidia-smi error: {nvidia_result.error}")

        else:
            # Double-bit ECC: uncorrectable, near-certain hardware fault
            dbe_volatile = nvidia_result.ecc_dbe_volatile
            dbe_aggregate = nvidia_result.ecc_dbe_aggregate

            if dbe_volatile > 0:
                # Volatile = since last driver reload; most alarming
                signals["ecc_dbe_volatile"] = min(1.0, 0.70 + dbe_volatile * 0.10)
                recs.append(f"DBE ECC volatile: {dbe_volatile} uncorrectable error(s) — schedule RMA")
            elif dbe_aggregate > 0:
                # Aggregate = lifetime count; GPU is still running but has history
                signals["ecc_dbe_aggregate"] = 0.30
                recs.append(f"DBE ECC aggregate: {dbe_aggregate} lifetime uncorrected error(s)")

            # Single-bit ECC: corrected by hardware but elevated count = degradation
            sbe = nvidia_result.ecc_sbe_volatile
            if sbe > 100:
                signals["ecc_sbe_high"] = 0.15
                recs.append(f"SBE ECC volatile: {sbe} corrected errors — monitor closely")
            elif sbe > 10:
                signals["ecc_sbe_elevated"] = 0.05

            # Clock throttle
            hw_throttle = [r for r in nvidia_result.clock_throttle_reasons
                           if "Hw" in r or "Thermal" in r]
            sw_throttle = [r for r in nvidia_result.clock_throttle_reasons
                           if r not in ("GpuIdle",) and r not in hw_throttle]

            if hw_throttle:
                signals["hw_throttle"] = 0.40
                recs.append(f"HW thermal throttle active: {', '.join(hw_throttle)}")
            elif sw_throttle:
                signals["sw_throttle"] = 0.10
                recs.append(f"SW throttle: {', '.join(sw_throttle)}")

            # Temperature
            temp = nvidia_result.temperature_gpu
            if temp is not None:
                if temp > 88:
                    signals["temp_critical"] = 0.35
                    recs.append(f"GPU temperature critical: {temp:.0f}°C (limit ~85°C)")
                elif temp > 83:
                    signals["temp_elevated"] = 0.12
                    recs.append(f"GPU temperature elevated: {temp:.0f}°C")

    # ── Xid signals (kernel ring buffer hardware errors) ────────────────────
    if xid_result is not None and xid_result.available:
        if xid_result.drain_xids_found:
            # Drain-class Xids: DBE ECC (48), row-remap failure (64),
            # NVLink (74), fallen-off-bus (79), uncontained ECC (95),
            # unrecoverable ECC escape (140), GPU init error (143).
            signals["xid_drain"] = 0.85
            codes = ", ".join(str(x) for x in xid_result.drain_xids_found)
            recs.append(f"Critical Xid events in dmesg: {codes} — hardware fault confirmed")
        elif xid_result.watch_xids_found:
            signals["xid_watch"] = 0.25
            codes = ", ".join(str(x) for x in xid_result.watch_xids_found)
            recs.append(f"Xid events in dmesg: {codes} — monitor")
    elif xid_result is not None and not xid_result.available:
        signals["xid_log_unavailable"] = 0.05
        if xid_result.error:
            recs.append(f"Xid scan unavailable: {xid_result.error}")
        else:
            recs.append("Xid scan unavailable — kernel log access restricted")

    # ── Aggregate ────────────────────────────────────────────────────────────
    rs.score = _aggregate(list(signals.values()))
    rs.signals = signals
    rs.recommendations = recs

    if rs.score >= DRAIN_THRESHOLD:
        rs.tier = "DRAIN"
    elif rs.score >= WATCH_THRESHOLD:
        rs.tier = "WATCH"
    else:
        rs.tier = "HEALTHY"

    return rs
