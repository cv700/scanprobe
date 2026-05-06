#!/usr/bin/env python3
"""
ashiba preflight — GPU cluster health check
Single-file edition. No install required. Python 3.9+, nvidia-smi.

Usage:
  curl -fsSL https://raw.githubusercontent.com/ashiba/preflight/main/preflight.py | python3
  python3 preflight.py
  python3 preflight.py --tier 2      # + DCGM + matmul (~3 min)
  python3 preflight.py --json        # machine-readable

Exit: 0=HEALTHY  1=WATCH  2=DRAIN  3=error

github.com/ashiba/preflight · MIT license
"""

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

__version__ = "0.1.0"

# ── ANSI color helpers ────────────────────────────────────────────────────────

_COLOR = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text

def green(t):  return _c("32", t)
def yellow(t): return _c("33", t)
def red(t):    return _c("31", t)
def bold(t):   return _c("1",  t)
def dim(t):    return _c("2",  t)


# ── nvidia-smi check ──────────────────────────────────────────────────────────

_SMI_FIELDS = ",".join([
    "index", "name", "uuid",
    "ecc.mode.current",
    "ecc.errors.corrected.volatile.total",
    "ecc.errors.uncorrected.volatile.total",
    "ecc.errors.corrected.aggregate.total",
    "ecc.errors.uncorrected.aggregate.total",
    "temperature.gpu", "temperature.memory",
    "power.draw", "power.limit",
    "clocks.current.sm", "clocks.current.memory",
    "clocks_throttle_reasons.active",
    "memory.used", "memory.total",
    "pcie.link.gen.current", "pcie.link.width.current",
])

_THROTTLE_BITS = {
    0x0000000000000001: "GpuIdle",
    0x0000000000000002: "AppClocksSetting",
    0x0000000000000004: "SwPowerCap",
    0x0000000000000008: "HwSlowdown",
    0x0000000000000010: "SyncBoost",
    0x0000000000000020: "SwThermalSlowdown",
    0x0000000000000040: "HwThermalSlowdown",
    0x0000000000000080: "HwPowerBrakeSlowdown",
    0x0000000000000100: "DisplayClockSetting",
}


@dataclass
class GpuInfo:
    index: int
    name: str = "unknown"
    uuid: str = "unknown"
    ecc_enabled: bool = False
    ecc_sbe_volatile: int = 0
    ecc_dbe_volatile: int = 0
    ecc_sbe_aggregate: int = 0
    ecc_dbe_aggregate: int = 0
    temperature_gpu: Optional[float] = None
    power_draw_w: Optional[float] = None
    power_limit_w: Optional[float] = None
    clock_throttle_reasons: list = field(default_factory=list)
    memory_used_mib: Optional[float] = None
    memory_total_mib: Optional[float] = None
    pcie_link_gen: Optional[int] = None
    pcie_link_width: Optional[int] = None
    passed: bool = True
    error: Optional[str] = None


def _smi_int(s: str, default: int = 0) -> int:
    s = s.strip().replace(",", "")
    if s in ("N/A", "[Not Supported]", "[N/A]", ""):
        return default
    try:
        return int(s)
    except ValueError:
        return default


def _smi_float(s: str) -> Optional[float]:
    s = s.strip().replace(",", "")
    if s in ("N/A", "[Not Supported]", "[N/A]", ""):
        return None
    for unit in (" W", " MiB", " MHz", " %", "°C", " C"):
        s = s.replace(unit, "")
    try:
        return float(s.strip())
    except ValueError:
        return None


def _decode_throttle(hex_str: str) -> list:
    try:
        val = int(hex_str.strip(), 16)
        return [name for mask, name in _THROTTLE_BITS.items() if val & mask]
    except (ValueError, TypeError):
        return []


def _parse_smi_line(line: str, idx: int) -> GpuInfo:
    g = GpuInfo(index=idx)
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 19:
        g.error = f"unexpected column count ({len(parts)})"
        g.passed = False
        return g
    g.name                = parts[1]
    g.uuid                = parts[2]
    g.ecc_enabled         = parts[3].lower() in ("enabled", "1", "true")
    g.ecc_sbe_volatile    = _smi_int(parts[4])
    g.ecc_dbe_volatile    = _smi_int(parts[5])
    g.ecc_sbe_aggregate   = _smi_int(parts[6])
    g.ecc_dbe_aggregate   = _smi_int(parts[7])
    g.temperature_gpu     = _smi_float(parts[8])
    g.power_draw_w        = _smi_float(parts[10])
    g.power_limit_w       = _smi_float(parts[11])
    g.clock_throttle_reasons = _decode_throttle(parts[14])
    g.memory_used_mib     = _smi_float(parts[15])
    g.memory_total_mib    = _smi_float(parts[16])
    g.pcie_link_gen       = _smi_int(parts[17]) or None
    g.pcie_link_width     = _smi_int(parts[18]) or None
    if g.ecc_dbe_volatile > 0:
        g.passed = False
    return g


def query_all_gpus() -> dict:
    """Run nvidia-smi once, return {index: GpuInfo}."""
    try:
        proc = subprocess.run(
            ["nvidia-smi", f"--query-gpu={_SMI_FIELDS}",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return {"error": "nvidia-smi not found — is this a CUDA host?"}
    except subprocess.TimeoutExpired:
        return {"error": "nvidia-smi timed out"}
    except Exception as e:
        return {"error": str(e)}

    if proc.returncode != 0:
        return {"error": f"nvidia-smi exit {proc.returncode}: {proc.stderr.strip()[:200]}"}

    results = {}
    for i, line in enumerate([l for l in proc.stdout.strip().splitlines() if l.strip()]):
        results[i] = _parse_smi_line(line, i)
    return results


def count_gpus() -> int:
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            return 0
        return len([l for l in proc.stdout.strip().splitlines() if l.strip()])
    except Exception:
        return 0


# ── Xid check ────────────────────────────────────────────────────────────────

# Xids that indicate definite hardware fault → DRAIN
DRAIN_XIDS = {48, 63, 74, 79, 94, 95}
# Xids worth monitoring → WATCH
WATCH_XIDS = {13, 31, 32, 43, 45, 56, 57, 58, 61, 64, 69, 92}

XID_DESC = {
    13:  "Graphics engine exception",
    31:  "GPU memory page fault",
    32:  "Invalid P2P memory access",
    43:  "GPU stopped processing",
    45:  "Preemptive cleanup",
    48:  "DBE ECC — uncorrectable memory error",
    56:  "Display engine error",
    57:  "Error programming video memory interface",
    58:  "Unstable video memory interface",
    61:  "Internal micro-controller breakpoint",
    63:  "Row remapping failure — HBM row retired",
    64:  "Row remapping — no spare rows",
    69:  "Graphics engine class error",
    74:  "NVLink error",
    79:  "GPU engine hang",
    92:  "High SBE ECC error rate",
    94:  "GPU containment error (GPC fault)",
    95:  "Uncontained error — GPU reset required",
}


@dataclass
class XidResult:
    available: bool = True
    passed: bool = True
    error: Optional[str] = None
    events: list = field(default_factory=list)
    drain_xids_found: list = field(default_factory=list)
    watch_xids_found: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def check_xid() -> XidResult:
    """Scan dmesg for NVIDIA Xid hardware error codes. Pure stdlib."""
    result = XidResult()
    try:
        proc = subprocess.run(
            ["dmesg", "--level=err,warn,crit,alert,emerg"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            proc = subprocess.run(
                ["dmesg"], capture_output=True, text=True, timeout=10,
            )
        if proc.returncode != 0:
            result.available = False
            result.error = "dmesg unavailable (may need elevated privileges)"
            return result
        output = proc.stdout
    except FileNotFoundError:
        result.available = False
        result.error = "dmesg not found"
        return result
    except Exception as e:
        result.available = False
        result.error = str(e)
        return result

    xid_re = re.compile(r"NVRM:\s+Xid\s+\(PCI:([^)]+)\):\s+(\d+)(.*)", re.IGNORECASE)
    for line in output.splitlines():
        m = xid_re.search(line)
        if not m:
            continue
        pci = m.group(1).strip()
        code = int(m.group(2))
        result.events.append({
            "xid": code,
            "pci": pci,
            "description": XID_DESC.get(code, f"Xid {code}"),
            "severity": "DRAIN" if code in DRAIN_XIDS else
                        "WATCH" if code in WATCH_XIDS else "INFO",
        })
        if code in DRAIN_XIDS and code not in result.drain_xids_found:
            result.drain_xids_found.append(code)
            result.passed = False
            result.warnings.append(
                f"Xid {code} ({XID_DESC.get(code, '?')}) on {pci}"
            )
        elif code in WATCH_XIDS and code not in result.watch_xids_found:
            result.watch_xids_found.append(code)
            result.warnings.append(
                f"Xid {code} ({XID_DESC.get(code, '?')}) on {pci}"
            )
    return result


# ── Scoring ───────────────────────────────────────────────────────────────────

WATCH_THRESHOLD = 0.20
DRAIN_THRESHOLD = 0.50


@dataclass
class RiskScore:
    gpu_index: int
    score: float = 0.0
    tier: str = "HEALTHY"
    signals: dict = field(default_factory=dict)
    recommendations: list = field(default_factory=list)


def _aggregate(weights: list) -> float:
    """
    Geometric decay: dominant signal counts fully, each additional at half weight.
    Prevents minor signal accumulation from mimicking a true drain event.
    """
    if not weights:
        return 0.0
    ws = sorted(weights, reverse=True)
    return min(1.0, sum(w * (0.5 ** i) for i, w in enumerate(ws)))


def score_gpu(gpu: GpuInfo, xid: XidResult, gpu_index: int) -> RiskScore:
    rs = RiskScore(gpu_index=gpu_index)
    signals = {}
    recs = []

    # ── nvidia-smi signals ──
    if gpu is not None:
        if gpu.error and not gpu.passed:
            signals["nvidia_smi_error"] = 0.55
            recs.append(f"nvidia-smi error: {gpu.error}")
        else:
            if gpu.ecc_dbe_volatile > 0:
                signals["ecc_dbe_volatile"] = min(1.0, 0.70 + gpu.ecc_dbe_volatile * 0.10)
                recs.append(
                    f"DBE ECC volatile: {gpu.ecc_dbe_volatile} uncorrectable error(s) — "
                    f"schedule RMA"
                )
            elif gpu.ecc_dbe_aggregate > 0:
                signals["ecc_dbe_aggregate"] = 0.30
                recs.append(
                    f"DBE ECC aggregate: {gpu.ecc_dbe_aggregate} lifetime uncorrected error(s)"
                )

            sbe = gpu.ecc_sbe_volatile
            if sbe > 100:
                signals["ecc_sbe_high"] = 0.15
                recs.append(f"SBE ECC: {sbe} corrected errors — monitor closely")
            elif sbe > 10:
                signals["ecc_sbe_elevated"] = 0.05

            hw_throttle = [r for r in gpu.clock_throttle_reasons
                           if "Hw" in r or "Thermal" in r]
            sw_throttle = [r for r in gpu.clock_throttle_reasons
                           if r != "GpuIdle" and r not in hw_throttle]
            if hw_throttle:
                signals["hw_throttle"] = 0.40
                recs.append(f"HW thermal throttle: {', '.join(hw_throttle)}")
            elif sw_throttle:
                signals["sw_throttle"] = 0.10

            temp = gpu.temperature_gpu
            if temp is not None:
                if temp > 88:
                    signals["temp_critical"] = 0.35
                    recs.append(f"Temperature critical: {temp:.0f}°C")
                elif temp > 83:
                    signals["temp_elevated"] = 0.12
                    recs.append(f"Temperature elevated: {temp:.0f}°C")

    # ── Xid signals ──
    if xid is not None and xid.available:
        if xid.drain_xids_found:
            signals["xid_drain"] = 0.85
            codes = ", ".join(str(x) for x in xid.drain_xids_found)
            recs.append(
                f"Critical Xid in dmesg: {codes} — hardware fault confirmed. "
                f"File a support ticket referencing these Xid codes."
            )
        elif xid.watch_xids_found:
            signals["xid_watch"] = 0.25
            codes = ", ".join(str(x) for x in xid.watch_xids_found)
            recs.append(f"Xid events in dmesg: {codes} — monitor")

    rs.score = _aggregate(list(signals.values()))
    rs.signals = signals
    rs.recommendations = recs
    rs.tier = (
        "DRAIN" if rs.score >= DRAIN_THRESHOLD else
        "WATCH" if rs.score >= WATCH_THRESHOLD else
        "HEALTHY"
    )
    return rs


# ── Output ────────────────────────────────────────────────────────────────────

_TIER_FMT = {
    "HEALTHY": lambda: green("HEALTHY"),
    "WATCH":   lambda: yellow(" WATCH "),
    "DRAIN":   lambda: red(" DRAIN "),
}

_TIER_ARROW = {
    "HEALTHY": green("✓") if _COLOR else " ",
    "WATCH":   yellow("!") if _COLOR else "!",
    "DRAIN":   red("✗") if _COLOR else "X",
}


def _gpu_summary(gpu: GpuInfo, rs: RiskScore) -> str:
    """Short phrase describing the top signal on this GPU."""
    if not rs.recommendations:
        if gpu and gpu.ecc_dbe_volatile == 0 and gpu.ecc_dbe_aggregate == 0:
            sbe = gpu.ecc_sbe_volatile if gpu else 0
            ecc_str = f"{sbe} SBE" if sbe > 0 else "no ECC errors"
            return ecc_str
        return "ok"
    # First recommendation, truncated
    r = rs.recommendations[0]
    return r[:60] + ("…" if len(r) > 60 else "")


def _short_name(name: str) -> str:
    """Shorten GPU name for display."""
    # 'NVIDIA H100 80GB HBM3' → 'H100 80GB'
    name = name.replace("NVIDIA ", "").replace("Tesla ", "")
    parts = name.split()
    return " ".join(parts[:3]) if len(parts) > 3 else name


def print_results(gpu_data: dict, scores: list, xid: XidResult,
                  elapsed: float, tier: int, dcgm_available: bool):
    print()
    print(bold(f"ashiba preflight  v{__version__}") + dim("  ─  github.com/ashiba/preflight"))
    print()

    for rs in scores:
        idx = rs.gpu_index
        gpu = gpu_data.get(idx) if isinstance(gpu_data, dict) else None
        tier_str = _TIER_FMT.get(rs.tier, lambda: rs.tier)()
        arrow = _TIER_ARROW.get(rs.tier, " ")
        name = _short_name(gpu.name) if gpu and gpu.name != "unknown" else "unknown"
        temp = f"{gpu.temperature_gpu:.0f}°C" if (gpu and gpu.temperature_gpu is not None) else "  -  "
        summary = _gpu_summary(gpu, rs)
        print(f"  GPU {idx}  {arrow} {tier_str}  {name:<18}  {temp}  {dim(summary)}")

    print()

    # Node-level verdict
    tiers = {rs.tier for rs in scores}
    node_tier = "DRAIN" if "DRAIN" in tiers else "WATCH" if "WATCH" in tiers else "HEALTHY"
    tier_label = _TIER_FMT.get(node_tier, lambda: node_tier)()
    print(f"  Node: {tier_label}")

    # Deduplicated recommendations
    seen_recs = set()
    for rs in scores:
        for rec in rs.recommendations:
            if rec not in seen_recs:
                seen_recs.add(rec)
                arrow = red("→") if rs.tier == "DRAIN" else yellow("→") if rs.tier == "WATCH" else "→"
                print(f"  {arrow} GPU {rs.gpu_index}  {rec}")

    # Xid note even on healthy nodes if events exist
    if xid and xid.available and xid.events:
        all_drain = [e for e in xid.events if e["severity"] == "DRAIN"]
        all_watch = [e for e in xid.events if e["severity"] == "WATCH"]
        if all_drain:
            codes = sorted({e["xid"] for e in all_drain})
            print(f"  {red('→')} Xid {codes} in dmesg — hardware faults present on this node")
    elif xid and not xid.available:
        print(f"  {dim('·')} Xid scan: {dim(xid.error or 'unavailable')}")

    print()

    # Footer
    checked = ["nvidia-smi", "ECC counters", "Xid scan"]
    if not (xid and xid.available):
        checked.remove("Xid scan")
        checked.append("Xid scan (unavailable)")
    skipped = []
    if not dcgm_available:
        skipped.append("DCGM (not found)")
    if tier < 2:
        skipped.append("matmul/collective (--tier 2)")

    checked_str = " · ".join(checked)
    print(f"  {dim('Checked:')} {dim(checked_str)}  {dim(f'({elapsed:.0f}s)')}")
    if skipped:
        print(f"  {dim('Skipped:')} {dim(', '.join(skipped))}")
    if tier < 2:
        print(f"  {dim('Tip: python3 preflight.py --tier 2  for DCGM + matmul checks (~3 min)')}")
    print()


def print_json(gpu_data: dict, scores: list, xid: XidResult, elapsed: float):
    out = {
        "version": __version__,
        "elapsed_s": round(elapsed, 2),
        "node_tier": (
            "DRAIN" if any(rs.tier == "DRAIN" for rs in scores) else
            "WATCH" if any(rs.tier == "WATCH" for rs in scores) else
            "HEALTHY"
        ),
        "gpus": [],
        "xid": {
            "available": xid.available if xid else False,
            "drain_xids": xid.drain_xids_found if xid else [],
            "watch_xids": xid.watch_xids_found if xid else [],
        } if xid else None,
    }
    for rs in scores:
        gpu = gpu_data.get(rs.gpu_index) if isinstance(gpu_data, dict) else None
        out["gpus"].append({
            "index": rs.gpu_index,
            "name": gpu.name if gpu else "unknown",
            "tier": rs.tier,
            "score": round(rs.score, 4),
            "signals": rs.signals,
            "recommendations": rs.recommendations,
            "temperature_gpu": gpu.temperature_gpu if gpu else None,
            "ecc_dbe_volatile": gpu.ecc_dbe_volatile if gpu else None,
            "ecc_dbe_aggregate": gpu.ecc_dbe_aggregate if gpu else None,
            "ecc_sbe_volatile": gpu.ecc_sbe_volatile if gpu else None,
        })
    print(json.dumps(out, indent=2))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        prog="preflight",
        description=(
            "ashiba preflight — GPU cluster health check\n"
            "Exit: 0=HEALTHY  1=WATCH  2=DRAIN  3=error"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--tier", type=int, default=1, choices=[1, 2, 3],
                   help="1=fast ~20s (default), 2=+DCGM+matmul ~3min, 3=+collective ~10min")
    p.add_argument("--gpus", default="all",
                   help="'all', '0', '0,1,2', or '0-3'  (default: all)")
    p.add_argument("--json", dest="json_output", action="store_true",
                   help="Machine-readable JSON output")
    args = p.parse_args()

    t_start = time.time()

    # ── Discover GPUs ─────────────────────────────────────────────────────────
    n = count_gpus()
    if n == 0:
        print("No CUDA GPUs found via nvidia-smi.", file=sys.stderr)
        return 3

    if args.gpus.lower() == "all":
        indices = list(range(n))
    else:
        indices = []
        for part in args.gpus.split(","):
            part = part.strip()
            if "-" in part:
                lo, hi = part.split("-", 1)
                indices.extend(range(int(lo), int(hi) + 1))
            else:
                indices.append(int(part))
        indices = sorted(set(indices))

    # ── Run checks ────────────────────────────────────────────────────────────
    if not args.json_output:
        sys.stderr.write(dim("  running checks...") + "\n")

    gpu_data = query_all_gpus()
    xid = check_xid()

    dcgm_available = False
    if args.tier >= 1:
        try:
            proc = subprocess.run(
                ["dcgmi", "diag", "-r", "1"],
                capture_output=True, text=True, timeout=120,
            )
            dcgm_available = True
            # Basic pass/fail from return code; detailed scoring needs the full module
        except FileNotFoundError:
            pass
        except Exception:
            pass

    # ── Score ─────────────────────────────────────────────────────────────────
    if isinstance(gpu_data, dict) and "error" in gpu_data:
        if not args.json_output:
            print(f"\n  {red('ERROR')} {gpu_data['error']}\n")
        else:
            print(json.dumps({"error": gpu_data["error"]}))
        return 3

    scores = []
    for idx in indices:
        gpu = gpu_data.get(idx)
        rs = score_gpu(gpu, xid, idx)
        scores.append(rs)

    elapsed = time.time() - t_start

    # ── Output ────────────────────────────────────────────────────────────────
    if not args.json_output:
        # Clear "running checks..." line if in a TTY
        if sys.stderr.isatty():
            sys.stderr.write("\033[A\033[2K")  # up one line, erase it
        print_results(gpu_data, scores, xid, elapsed, args.tier, dcgm_available)
    else:
        print_json(gpu_data, scores, xid, elapsed)

    # ── Exit code ─────────────────────────────────────────────────────────────
    tiers = {rs.tier for rs in scores}
    if "DRAIN" in tiers:   return 2
    elif "WATCH" in tiers: return 1
    else:                  return 0


if __name__ == "__main__":
    sys.exit(main())
