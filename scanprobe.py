#!/usr/bin/env python3
"""
scanprobe - minimal GPU evidence scan.

Stdlib only. Reads nvidia-smi and NVIDIA Xid events from local kernel logs.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

__version__ = "0.1.0"

SMI_FIELDS = ",".join([
    "index",
    "name",
    "ecc.errors.corrected.volatile.total",
    "ecc.errors.uncorrected.volatile.total",
    "ecc.errors.uncorrected.aggregate.total",
    "temperature.gpu",
    "clocks_throttle_reasons.active",
])

THROTTLE_BITS = {
    0x0000000000000001: "GpuIdle",
    0x0000000000000002: "ApplicationsClocksSetting",
    0x0000000000000004: "SwPowerCap",
    0x0000000000000008: "HwSlowdown",
    0x0000000000000010: "SyncBoost",
    0x0000000000000020: "SwThermalSlowdown",
    0x0000000000000040: "HwThermalSlowdown",
    0x0000000000000080: "HwPowerBrakeSlowdown",
    0x0000000000000100: "DisplayClockSetting",
}

DRAIN_XIDS = {48, 64, 74, 79, 95, 140, 143}
WATCH_XIDS = {13, 31, 32, 43, 45, 63, 69, 92, 94, 109, 119, 120}

XID_DESC = {
    13: "Graphics engine exception",
    31: "GPU memory page fault",
    32: "Invalid or corrupted push buffer stream",
    43: "GPU stopped processing (long compute)",
    45: "Preemptive cleanup (application error)",
    48: "DBE ECC error — uncorrectable memory",
    63: "Row remapping event recorded",
    64: "Row remapping failure — recording failed",
    69: "Graphics engine class error",
    74: "NVLink error",
    79: "GPU has fallen off the bus",
    92: "High single-bit ECC error rate",
    94: "Contained ECC or channel error",
    95: "Uncontained error — GPU reset required",
    109: "Context switch timeout",
    119: "GSP RPC timeout",
    120: "GSP error",
    140: "Unrecoverable ECC error escape",
    143: "GPU init error",
    154: "Driver recovery action summary",
}

WATCH_THRESHOLD = 0.20
DRAIN_THRESHOLD = 0.50


@dataclass
class GpuInfo:
    index: int
    name: str = "unknown"
    ecc_sbe_volatile: int = 0
    ecc_dbe_volatile: int = 0
    ecc_dbe_aggregate: int = 0
    temperature_gpu: Optional[float] = None
    clock_throttle_reasons: list = field(default_factory=list)
    passed: bool = True
    error: Optional[str] = None


@dataclass
class XidResult:
    available: bool = True
    passed: bool = True
    error: Optional[str] = None
    log_source: str = "unknown"
    events: list = field(default_factory=list)
    drain_xids_found: list = field(default_factory=list)
    watch_xids_found: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


@dataclass
class RiskScore:
    gpu_index: int
    score: float = 0.0
    tier: str = "CLEAR"
    signals: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)


def _parse_int(value: str, default: int = 0) -> int:
    value = value.strip().replace(",", "")
    if value in ("N/A", "[Not Supported]", "[N/A]", ""):
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _parse_float(value: str) -> Optional[float]:
    value = value.strip().replace(",", "")
    if value in ("N/A", "[Not Supported]", "[N/A]", ""):
        return None
    for unit in (" W", " MiB", " MHz", " %", "C"):
        value = value.replace(unit, "")
    try:
        return float(value.strip())
    except ValueError:
        return None


def _decode_throttle(value: str) -> list:
    try:
        bitmask = int(value.strip(), 16)
    except (TypeError, ValueError):
        return []
    return [name for mask, name in THROTTLE_BITS.items() if bitmask & mask]


def _format_smi_error(returncode: int, stderr: str) -> str:
    detail = (stderr or "").strip()[:200] or "no stderr"
    if "Failed to initialize NVML" in detail:
        return f"nvidia-smi failed: {detail}"
    if "Unable to determine the device handle" in detail:
        return f"nvidia-smi failed: {detail}"
    if "No devices were found" in detail:
        return "nvidia-smi found no GPUs"
    return f"nvidia-smi exit {returncode}: {detail}"


def _smi_error_is_device_lost(error: str) -> bool:
    return "Unable to determine the device handle" in (error or "")


def _xid_154_recovery_action(message: str) -> Optional[str]:
    for action in ("Drain and Reset", "Node Reboot Required", "GPU Reset Required"):
        if action.lower() in message.lower():
            return action

    matches = re.findall(r"\(([^)]+)\)", message)
    for value in reversed(matches):
        if value and value.lower() != "none":
            return value
    return None


def _parse_xid_line(line: str) -> Optional[dict]:
    xid_match = re.search(
        r"NVRM:\s+Xid\s+\((?:PCI:)?([^)]+)\):\s+(\d+)(.*)",
        line,
        re.IGNORECASE,
    )
    if xid_match:
        pci = xid_match.group(1).strip()
        code = int(xid_match.group(2))
        message = xid_match.group(3).strip(" ,")
        severity = "DRAIN" if code in DRAIN_XIDS else "WATCH" if code in WATCH_XIDS else "INFO"
        event = {
            "xid": code,
            "pci": pci,
            "description": XID_DESC.get(code, f"Xid {code}"),
            "severity": severity,
            "message": message,
            "raw": line.strip(),
        }
        if code == 154:
            action = _xid_154_recovery_action(message)
            if action:
                event["recovery_action"] = action
                if any(word in action.lower() for word in ("drain", "reset", "reboot")):
                    event["severity"] = "DRAIN"
        return event

    bus_match = re.search(
        r"NVRM:\s+GPU\s+([0-9a-fA-F:.]+):\s+GPU has fallen off the bus",
        line,
        re.IGNORECASE,
    )
    if bus_match:
        return {
            "xid": 79,
            "pci": bus_match.group(1).strip(),
            "description": XID_DESC[79],
            "severity": "DRAIN",
            "message": "GPU has fallen off the bus",
            "raw": line.strip(),
        }
    return None


def _record_xid_events(result: XidResult, text: str) -> XidResult:
    for line in text.splitlines():
        event = _parse_xid_line(line)
        if not event:
            continue
        code = event["xid"]
        pci = event["pci"]
        result.events.append(event)
        if event["severity"] == "DRAIN" and code not in result.drain_xids_found:
            result.drain_xids_found.append(code)
            result.passed = False
            detail = event.get("recovery_action") or event["description"]
            result.warnings.append(f"Xid {code} ({detail}) on {pci}")
        elif event["severity"] == "WATCH" and code not in result.watch_xids_found:
            result.watch_xids_found.append(code)
            result.warnings.append(f"Xid {code} ({event['description']}) on {pci}")
    return result


def _parse_smi_line(line: str, fallback_index: int) -> GpuInfo:
    parts = [part.strip() for part in line.split(",")]
    gpu = GpuInfo(index=fallback_index)
    if len(parts) < 7:
        gpu.passed = False
        gpu.error = f"unexpected nvidia-smi column count: {len(parts)}"
        return gpu

    gpu.index = _parse_int(parts[0], fallback_index)
    gpu.name = parts[1]
    gpu.ecc_sbe_volatile = _parse_int(parts[2])
    gpu.ecc_dbe_volatile = _parse_int(parts[3])
    gpu.ecc_dbe_aggregate = _parse_int(parts[4])
    gpu.temperature_gpu = _parse_float(parts[5])
    gpu.clock_throttle_reasons = _decode_throttle(parts[6])

    if gpu.ecc_dbe_volatile > 0:
        gpu.passed = False
    return gpu


def count_gpus() -> int:
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return 0
    if proc.returncode != 0:
        return 0
    return len([line for line in proc.stdout.splitlines() if line.strip()])


def query_gpus(indices: list) -> dict:
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=" + SMI_FIELDS, "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return {
            idx: GpuInfo(idx, passed=False, error="nvidia-smi not found")
            for idx in indices
        }
    except subprocess.TimeoutExpired:
        return {
            idx: GpuInfo(idx, passed=False, error="nvidia-smi timed out")
            for idx in indices
        }

    if proc.returncode != 0:
        err = _format_smi_error(proc.returncode, proc.stderr)
        return {idx: GpuInfo(idx, passed=False, error=err) for idx in indices}

    parsed = {}
    for fallback_index, line in enumerate(line for line in proc.stdout.splitlines() if line.strip()):
        gpu = _parse_smi_line(line, fallback_index)
        parsed[gpu.index] = gpu

    results = {}
    for idx in indices:
        results[idx] = parsed.get(
            idx,
            GpuInfo(idx, passed=False, error=f"GPU {idx} not found in nvidia-smi output"),
        )
    return results


def check_xid() -> XidResult:
    result = XidResult()
    try:
        proc = subprocess.run(
            ["dmesg", "--level=err,warn,crit,alert,emerg"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            proc = subprocess.run(["dmesg"], capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        result.available = False
        result.error = "dmesg not found"
        result.log_source = "unavailable-no-dmesg"
        return result
    except subprocess.TimeoutExpired:
        result.available = False
        result.error = "dmesg timed out"
        result.log_source = "unavailable-timeout"
        return result

    if proc.returncode != 0:
        try:
            journal = subprocess.run(
                ["journalctl", "-k", "-b", "--no-pager"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError:
            journal = None
        except subprocess.TimeoutExpired:
            journal = None

        if journal is not None and journal.returncode == 0 and journal.stdout.strip():
            result.log_source = "journalctl-k"
            return _record_xid_events(result, journal.stdout)

        result.available = False
        result.error = "kernel logs unavailable to this process; run on the host or with admin privileges if appropriate"
        result.log_source = "unavailable-restricted"
        return result

    result.log_source = "dmesg-cmd"
    return _record_xid_events(result, proc.stdout)


def _aggregate(weights: list) -> float:
    score = 0.0
    for offset, weight in enumerate(sorted(weights, reverse=True)):
        score += weight * (0.5 ** offset)
    return min(1.0, score)


def score_gpu(gpu: GpuInfo, xid: XidResult, gpu_index: int) -> RiskScore:
    signals = {}
    evidence = []
    unknown = False

    if gpu is None:
        unknown = True
        signals["nvidia_smi_unavailable"] = 0.0
        evidence.append("nvidia-smi did not return this GPU")
    elif gpu.error and not gpu.passed:
        if _smi_error_is_device_lost(gpu.error):
            signals["nvidia_smi_device_lost"] = 0.70
            evidence.append(f"nvidia-smi cannot determine GPU device handle: {gpu.error}")
        else:
            unknown = True
            signals["nvidia_smi_unavailable"] = 0.0
            evidence.append(f"nvidia-smi unavailable: {gpu.error}")
    else:
        if gpu.ecc_dbe_volatile > 0:
            signals["ecc_dbe_volatile"] = min(1.0, 0.70 + gpu.ecc_dbe_volatile * 0.10)
            evidence.append(f"DBE ECC volatile: {gpu.ecc_dbe_volatile}")
        elif gpu.ecc_dbe_aggregate > 0:
            signals["ecc_dbe_aggregate"] = 0.30
            evidence.append(f"DBE ECC aggregate: {gpu.ecc_dbe_aggregate}")

        if gpu.ecc_sbe_volatile > 100:
            signals["ecc_sbe_high"] = 0.15
            evidence.append(f"SBE ECC volatile: {gpu.ecc_sbe_volatile}")
        elif gpu.ecc_sbe_volatile > 10:
            signals["ecc_sbe_elevated"] = 0.05

        hw_throttle = [
            reason for reason in gpu.clock_throttle_reasons
            if "Hw" in reason or "Thermal" in reason
        ]
        sw_throttle = [
            reason for reason in gpu.clock_throttle_reasons
            if reason != "GpuIdle" and reason not in hw_throttle
        ]
        if hw_throttle:
            signals["hw_throttle"] = 0.40
            evidence.append(f"HW thermal throttle active: {', '.join(hw_throttle)}")
        elif sw_throttle:
            signals["sw_throttle"] = 0.10
            evidence.append(f"SW throttle: {', '.join(sw_throttle)}")

        if gpu.temperature_gpu is not None:
            if gpu.temperature_gpu > 88:
                signals["temp_critical"] = 0.35
                evidence.append(f"GPU temperature critical: {gpu.temperature_gpu:.0f}C")
            elif gpu.temperature_gpu > 83:
                signals["temp_elevated"] = 0.12
                evidence.append(f"GPU temperature elevated: {gpu.temperature_gpu:.0f}C")

    if xid is not None and xid.available:
        if xid.drain_xids_found:
            signals["xid_drain"] = 0.85
            evidence.append(
                "Critical Xid events in dmesg: "
                + ", ".join(str(code) for code in xid.drain_xids_found)
            )
        elif xid.watch_xids_found:
            signals["xid_watch"] = 0.25
            evidence.append(
                "Xid events in dmesg: "
                + ", ".join(str(code) for code in xid.watch_xids_found)
            )
    elif xid is not None and not xid.available:
        signals["xid_log_unavailable"] = 0.05
        evidence.append(f"Xid scan unavailable: {xid.error or 'kernel log access restricted'}")

    score = _aggregate(list(signals.values()))
    if score >= DRAIN_THRESHOLD:
        tier = "DRAIN"
    elif score >= WATCH_THRESHOLD:
        tier = "WATCH"
    elif unknown:
        tier = "UNKNOWN"
    else:
        tier = "CLEAR"
    return RiskScore(gpu_index, score, tier, signals, evidence)


def parse_gpu_list(value: str, available: int) -> list:
    if value.lower() == "all":
        return list(range(available))
    indices = []
    for part in value.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            indices.extend(range(int(start), int(end) + 1))
        else:
            indices.append(int(part))
    return sorted(set(indices))


def node_tier(scores: list) -> str:
    tiers = {score.tier for score in scores}
    if "DRAIN" in tiers:
        return "DRAIN"
    if "WATCH" in tiers:
        return "WATCH"
    if "UNKNOWN" in tiers:
        return "UNKNOWN"
    return "CLEAR"


def next_actions(tier: str) -> list:
    if tier == "DRAIN":
        return [
            "Do not launch new work on this node until the listed evidence is resolved.",
            "Attach this output to your provider or administrator support ticket.",
        ]
    if tier == "WATCH":
        return [
            "Inspect the listed evidence before rerunning long or expensive work.",
            "If this followed a NCCL or training failure, correlate with rank, app, and fabric logs.",
        ]
    if tier == "UNKNOWN":
        return [
            "This scan could not observe enough local GPU state from here.",
            "Run on the host if you can, or ask the provider/admin to check host nvidia-smi and kernel logs.",
        ]
    return [
        "No local drain/watch evidence was visible in this scan.",
        "If the job still failed, continue with app, data, NCCL/fabric, or provider-level logs.",
    ]


def print_text(gpus: dict, scores: list, xid: XidResult, elapsed: float):
    tier = node_tier(scores)
    print("scanprobe")
    print(f"Node: {tier}")
    for score in sorted(scores, key=lambda item: item.gpu_index):
        gpu = gpus.get(score.gpu_index)
        name = gpu.name if gpu else "unknown"
        temp = f"{gpu.temperature_gpu:.0f}C" if gpu and gpu.temperature_gpu is not None else "n/a"
        print("")
        print(f"GPU {score.gpu_index}: {score.tier} temp={temp} name={name}")
        print("Visible evidence:")
        if score.evidence:
            for item in score.evidence:
                print(f"  - {item}")
        else:
            print("  - no local drain/watch evidence observed for this GPU")
    print("")
    print("Next action:")
    for action in next_actions(tier):
        print(f"  - {action}")
    print("")
    print("Not checked: silent data corruption, NCCL/fabric health, application correctness.")
    print(f"Completed in {elapsed:.1f}s")


def print_json(gpus: dict, scores: list, xid: XidResult, elapsed: float):
    out = {
        "version": __version__,
        "elapsed_s": round(elapsed, 2),
        "node_tier": node_tier(scores),
        "risk_scores": [asdict(score) for score in scores],
        "nvidia_smi": {str(index): asdict(gpu) for index, gpu in sorted(gpus.items())},
        "xid": asdict(xid) if xid else None,
    }
    print(json.dumps(out, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="scanprobe",
        description="Minimal GPU evidence scan: nvidia-smi + Xid logs.",
    )
    parser.add_argument("--gpus", default="all", help="'all', '0', '0,1,2', or '0-3'")
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args()

    start = time.time()
    available = count_gpus()
    if available == 0:
        if args.json:
            print(json.dumps({"error": "No CUDA GPUs found via nvidia-smi."}))
        else:
            print("No CUDA GPUs found via nvidia-smi.")
        return 3

    try:
        indices = parse_gpu_list(args.gpus, available)
    except ValueError as exc:
        print(f"Invalid --gpus argument: {exc}")
        return 3

    gpus = query_gpus(indices)
    xid = check_xid()
    scores = [score_gpu(gpus.get(index), xid, index) for index in indices]
    elapsed = time.time() - start

    if args.json:
        print_json(gpus, scores, xid, elapsed)
    else:
        print_text(gpus, scores, xid, elapsed)

    tier = node_tier(scores)
    if tier == "DRAIN":
        return 2
    if tier == "WATCH":
        return 1
    if tier == "UNKNOWN":
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
