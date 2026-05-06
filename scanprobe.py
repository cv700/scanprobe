#!/usr/bin/env python3
"""
scanprobe - minimal GPU evidence scan.

Stdlib only. Reads nvidia-smi and NVIDIA Xid events from local kernel logs.
"""

import argparse
import csv
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

TIER_PRIORITY = {
    "CLEAR": 0,
    "UNKNOWN": 1,
    "WATCH": 2,
    "DRAIN": 3,
}

DRAIN_RECOVERY_WORDS = ("drain", "reset", "reboot")
NOT_CHECKED_TEXT = (
    "Not checked: silent data corruption, NCCL/fabric health, "
    "application correctness."
)
CLAIM_CONTEXT_TEXT = (
    "No external claim supplied; checking local visible NVIDIA evidence only."
)
MODE_CONTEXT_TEXT = "Mode: read-only; no stress workload run; no fixes attempted."

DISCOVER_GPUS_CMD = ["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"]
QUERY_GPUS_CMD = [
    "nvidia-smi",
    "--query-gpu=" + SMI_FIELDS,
    "--format=csv,noheader,nounits",
]
DMESG_FILTERED_CMD = ["dmesg", "--level=err,warn,crit,alert,emerg"]
DMESG_FULL_CMD = ["dmesg"]
JOURNALCTL_KERNEL_CMD = ["journalctl", "-k", "-b", "--no-pager"]

REDACTION_PATTERNS = [
    (
        re.compile(
            r"^([A-Z][a-z]{2}\s+\d{1,2}\s+\d\d:\d\d:\d\d)"
            r"\s+\S+(\s+kernel:)",
            re.MULTILINE,
        ),
        r"\1 <host>\2",
    ),
    (
        re.compile(
            r"\bGPU-[0-9a-fA-F]{8}"
            r"(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b"
        ),
        "GPU-<redacted>",
    ),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip-address>"),
    (re.compile(r"\b[0-9a-fA-F]{32,64}\b"), "<hex-id>"),
    (re.compile(r"/Users/[^/\s:]+"), "/Users/<user>"),
    (re.compile(r"/home/[^/\s:]+"), "/home/<user>"),
]


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
class GpuDiscovery:
    count: int = 0
    indices: list = field(default_factory=list)
    status: str = "ok"
    error: Optional[str] = None


@dataclass
class RiskScore:
    gpu_index: int
    score: float = 0.0
    tier: str = "CLEAR"
    signals: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)


@dataclass
class NodeReport:
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
    detail = _redact_text((stderr or "").strip())[:200] or "no stderr"
    if "Failed to initialize NVML" in detail:
        return f"nvidia-smi failed: {detail}"
    if "Unable to determine the device handle" in detail:
        return f"nvidia-smi failed: {detail}"
    if "No devices were found" in detail:
        return "nvidia-smi found no GPUs"
    return f"nvidia-smi exit {returncode}: {detail}"


def _smi_error_is_device_lost(error: str) -> bool:
    return "Unable to determine the device handle" in (error or "")


def _run(cmd: list, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _redact_text(value: str) -> str:
    text = value or ""
    for pattern, replacement in REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _xid_154_recovery_action(message: str) -> Optional[str]:
    for action in ("Drain and Reset", "Node Reboot Required", "GPU Reset Required"):
        if action.lower() in message.lower():
            return action

    matches = re.findall(r"\(([^)]+)\)", message)
    for value in reversed(matches):
        if value and value.lower() != "none":
            return value
    return None


def _xid_severity(code: int) -> str:
    if code in DRAIN_XIDS:
        return "DRAIN"
    if code in WATCH_XIDS:
        return "WATCH"
    return "INFO"


def _parse_xid_line(line: str) -> Optional[dict]:
    xid_match = re.search(
        r"NVRM:\s+Xid\s+\((?:PCI:)?([^)]+)\):\s+(\d+)(.*)",
        line,
        re.IGNORECASE,
    )
    if xid_match:
        pci = xid_match.group(1).strip()
        code = int(xid_match.group(2))
        message = _redact_text(xid_match.group(3).strip(" ,"))
        event = {
            "xid": code,
            "pci": pci,
            "description": XID_DESC.get(code, f"Xid {code}"),
            "severity": _xid_severity(code),
            "message": message,
            "raw": _redact_text(line.strip()),
        }
        if code == 154:
            action = _xid_154_recovery_action(message)
            if action:
                event["recovery_action"] = action
                if any(word in action.lower() for word in DRAIN_RECOVERY_WORDS):
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
            "raw": _redact_text(line.strip()),
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
    try:
        parts = [part.strip() for part in next(csv.reader([line], skipinitialspace=True))]
    except (csv.Error, StopIteration):
        parts = []
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


def discover_gpus() -> GpuDiscovery:
    try:
        proc = _run(DISCOVER_GPUS_CMD, timeout=10)
    except FileNotFoundError:
        return GpuDiscovery(status="unavailable", error="nvidia-smi not found")
    except subprocess.TimeoutExpired:
        return GpuDiscovery(status="unavailable", error="nvidia-smi timed out")

    if proc.returncode != 0:
        err = _format_smi_error(proc.returncode, proc.stderr)
        if err == "nvidia-smi found no GPUs":
            return GpuDiscovery(status="none", error=err)
        return GpuDiscovery(status="unavailable", error=err)

    indices = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line:
            indices.append(_parse_int(line, len(indices)))

    if not indices:
        return GpuDiscovery(status="none", error="nvidia-smi returned no GPUs")
    return GpuDiscovery(count=len(indices), indices=indices)


def count_gpus() -> int:
    return discover_gpus().count


def query_gpus(indices: list) -> dict:
    try:
        proc = _run(QUERY_GPUS_CMD, timeout=30)
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
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    for fallback_index, line in enumerate(lines):
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
        proc = _run(DMESG_FILTERED_CMD, timeout=10)
        if proc.returncode != 0 or not proc.stdout.strip():
            proc = _run(DMESG_FULL_CMD, timeout=10)
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
            journal = _run(JOURNALCTL_KERNEL_CMD, timeout=10)
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


def score_gpu(gpu: GpuInfo, gpu_index: int) -> RiskScore:
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
            evidence.append(f"HW throttle active: {', '.join(hw_throttle)}")
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


def _max_tier(tiers: list) -> str:
    return max(tiers or ["CLEAR"], key=lambda tier: TIER_PRIORITY.get(tier, 0))


def build_node_report(scores: list, xid: XidResult) -> NodeReport:
    signals = {}
    evidence = []

    if xid is not None and xid.available:
        if xid.drain_xids_found:
            signals["xid_drain"] = 0.85
            detail = "; ".join(xid.warnings) or ", ".join(
                str(code) for code in xid.drain_xids_found
            )
            evidence.append(
                f"Critical Xid events in {xid.log_source}: "
                + detail
            )
        elif xid.watch_xids_found:
            signals["xid_watch"] = 0.25
            detail = "; ".join(xid.warnings) or ", ".join(
                str(code) for code in xid.watch_xids_found
            )
            evidence.append(
                f"Xid events in {xid.log_source}: "
                + detail
            )
    elif xid is not None and not xid.available:
        signals["xid_log_unavailable"] = 0.0
        evidence.append(f"Xid scan unavailable: {xid.error or 'kernel log access restricted'}")

    score = _aggregate(list(signals.values()))
    if score >= DRAIN_THRESHOLD:
        local_tier = "DRAIN"
    elif score >= WATCH_THRESHOLD:
        local_tier = "WATCH"
    else:
        local_tier = "CLEAR"

    return NodeReport(_max_tier([local_tier, node_tier(scores)]), signals, evidence)


def parse_gpu_list(value: str, available) -> list:
    if isinstance(available, int):
        available_indices = list(range(available))
    else:
        available_indices = sorted(set(int(index) for index in available))

    if value.lower() == "all":
        return available_indices
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
    return _max_tier([score.tier for score in scores])


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


def _print_bullets(items: list) -> None:
    for item in items:
        print(f"  - {item}")


def _gpu_label(gpu: Optional[GpuInfo]) -> tuple:
    name = gpu.name if gpu else "unknown"
    temp = "n/a"
    if gpu and gpu.temperature_gpu is not None:
        temp = f"{gpu.temperature_gpu:.0f}C"
    return name, temp


def print_text(gpus: dict, scores: list, report: NodeReport, elapsed: float):
    tier = report.tier
    print("scanprobe")
    print(CLAIM_CONTEXT_TEXT)
    print(MODE_CONTEXT_TEXT)
    print("")
    print(f"Node: {tier}")
    print("")
    print("Node-level evidence:")
    if report.evidence:
        _print_bullets(report.evidence)
    else:
        print("  - no node-level Xid drain/watch evidence observed")
    print("")
    print("GPU evidence:")
    for score in sorted(scores, key=lambda item: item.gpu_index):
        gpu = gpus.get(score.gpu_index)
        name, temp = _gpu_label(gpu)
        print("")
        print(f"GPU {score.gpu_index}: {score.tier} temp={temp} name={name}")
        if score.evidence:
            _print_bullets(score.evidence)
        else:
            print("  - no local GPU drain/watch evidence observed")
    print("")
    print("Next action:")
    _print_bullets(next_actions(tier))
    print("")
    print(NOT_CHECKED_TEXT)
    print(f"Completed in {elapsed:.1f}s")


def print_json(gpus: dict, scores: list, report: NodeReport, xid: XidResult, elapsed: float):
    out = {
        "version": __version__,
        "elapsed_s": round(elapsed, 2),
        "claim_context": CLAIM_CONTEXT_TEXT,
        "mode": MODE_CONTEXT_TEXT,
        "not_checked": NOT_CHECKED_TEXT,
        "node_tier": report.tier,
        "node_report": asdict(report),
        "risk_scores": [asdict(score) for score in scores],
        "nvidia_smi": {str(index): asdict(gpu) for index, gpu in sorted(gpus.items())},
        "xid": asdict(xid) if xid else None,
        "next_action": next_actions(report.tier),
    }
    print(json.dumps(out, indent=2))


def print_discovery_failure(discovery: GpuDiscovery, elapsed: float, as_json: bool):
    message = discovery.error or "nvidia-smi did not report any GPUs"
    if as_json:
        print(json.dumps({
            "version": __version__,
            "elapsed_s": round(elapsed, 2),
            "claim_context": CLAIM_CONTEXT_TEXT,
            "mode": MODE_CONTEXT_TEXT,
            "not_checked": NOT_CHECKED_TEXT,
            "node_tier": "UNKNOWN",
            "gpu_discovery": asdict(discovery),
            "evidence": [message],
            "next_action": next_actions("UNKNOWN"),
        }, indent=2))
        return

    print("scanprobe")
    print(CLAIM_CONTEXT_TEXT)
    print(MODE_CONTEXT_TEXT)
    print("")
    print("Node: UNKNOWN")
    print("")
    print("Visible evidence:")
    print(f"  - {message}")
    print("")
    print("Next action:")
    _print_bullets(next_actions("UNKNOWN"))
    print("")
    print(NOT_CHECKED_TEXT)
    print(f"Completed in {elapsed:.1f}s")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="scanprobe",
        description="Minimal GPU evidence scan: nvidia-smi + Xid logs.",
    )
    parser.add_argument("--gpus", default="all", help="'all', '0', '0,1,2', or '0-3'")
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args()

    start = time.time()
    discovery = discover_gpus()
    if discovery.count == 0:
        print_discovery_failure(discovery, time.time() - start, args.json)
        return 3

    try:
        indices = parse_gpu_list(args.gpus, discovery.indices)
    except ValueError as exc:
        print(f"Invalid --gpus argument: {exc}")
        return 3

    gpus = query_gpus(indices)
    xid = check_xid()
    scores = [score_gpu(gpus.get(index), index) for index in indices]
    report = build_node_report(scores, xid)
    elapsed = time.time() - start

    if args.json:
        print_json(gpus, scores, report, xid, elapsed)
    else:
        print_text(gpus, scores, report, elapsed)

    tier = report.tier
    if tier == "DRAIN":
        return 2
    if tier == "WATCH":
        return 1
    if tier == "UNKNOWN":
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
