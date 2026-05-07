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
SMI_FIELD_NAMES = SMI_FIELDS.split(",")
EXPECTED_SMI_COLUMNS = len(SMI_FIELD_NAMES)
UNSUPPORTED_SMI_VALUES = {"N/A", "[N/A]", "[Not Supported]", ""}

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

DRAIN_XIDS = {
    46, 48, 62, 64, 74, 79, 95, 109, 110, 119, 120, 136, 140, 143, 155, 156,
    158,
}
WATCH_XIDS = {
    13, 31, 32, 43, 45, 63, 69, 92, 94, 137, 157, 160, 161,
}

XID_DESC = {
    13: "Graphics engine exception",
    31: "GPU memory page fault",
    32: "Invalid or corrupted push buffer stream",
    43: "GPU stopped processing (long compute)",
    45: "Preemptive cleanup (application error)",
    46: "GPU stopped processing — reset required",
    48: "DBE ECC error — uncorrectable memory",
    62: "Internal micro-controller halt",
    63: "Row remapping event recorded",
    64: "Row remapping failure — recording failed",
    69: "Graphics engine class error",
    74: "NVLink error",
    79: "GPU has fallen off the bus",
    92: "High single-bit ECC error rate",
    94: "Contained ECC or channel error",
    95: "Uncontained error — GPU reset required",
    109: "Context switch timeout",
    110: "Security fault error",
    119: "GSP RPC timeout",
    120: "GSP error",
    136: "Link training failed",
    137: "NVLink privilege error",
    140: "Unrecoverable ECC error escape",
    143: "GPU init error",
    154: "Driver recovery action summary",
    155: "NVLink software-defined error",
    156: "Resource retirement event",
    157: "Resource retirement failure",
    158: "GPU fatal timeout",
    160: "Channel retirement event",
    161: "Channel retirement failure",
}

TIER_PRIORITY = {
    "CLEAR": 0,
    "UNKNOWN": 1,
    "WATCH": 2,
    "DRAIN": 3,
}

DRAIN_RECOVERY_ACTIONS = (
    "Drain and Reset",
    "Node Reboot Required",
    "GPU Reset Required",
)
DRAIN_RECOVERY_ACTIONS_LOWER = {action.lower() for action in DRAIN_RECOVERY_ACTIONS}
NOT_CHECKED_TEXT = (
    "Not checked: silent data corruption, NCCL/fabric health, "
    "application correctness."
)
CLAIM_CONTEXT_TEXT = (
    "No external claim supplied; checking local visible NVIDIA evidence only."
)
MODE_CONTEXT_TEXT = "Mode: read-only; no stress workload run; no fixes attempted."
RECENCY_CONTEXT_TEXT = (
    "Kernel-log scope: readable current-boot logs; event recency not interpreted."
)
AUTOMATION_CONTEXT = {
    "kind": "advisory",
    "authority": "operator",
    "automatic_remediation": False,
    "message": "Use as local evidence for a human/operator decision, not as an automatic drain command.",
}

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
    primary_issue: str = "none visible in this local scan"
    visibility: list = field(default_factory=list)
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


def _is_unsupported_smi_value(value: str) -> bool:
    return value.strip() in UNSUPPORTED_SMI_VALUES


def _format_smi_error(returncode: int, stderr: str, stdout: str = "") -> str:
    combined = "\n".join(
        part for part in (stderr or "", stdout or "") if part
    ).strip()
    detail = _redact_text(combined) or "no output"
    display = detail[:200]
    if "Unable to determine the device handle" in detail:
        if "Unable to determine the device handle" in display:
            return f"nvidia-smi failed: {display}"
        return f"nvidia-smi failed: Unable to determine the device handle: {display}"
    if "No devices were found" in detail:
        return "nvidia-smi found no GPUs"
    if "Failed to initialize NVML" in detail:
        if "Driver/library version mismatch" in detail and (
            "Driver/library version mismatch" not in display
        ):
            return (
                "nvidia-smi failed: Failed to initialize NVML: "
                f"Driver/library version mismatch: {display}"
            )
        if "Failed to initialize NVML" in display:
            return f"nvidia-smi failed: {display}"
        return f"nvidia-smi failed: Failed to initialize NVML: {display}"
    if "couldn't communicate with the NVIDIA driver" in detail:
        if "couldn't communicate with the NVIDIA driver" in display:
            return f"nvidia-smi failed: {display}"
        return (
            "nvidia-smi failed: couldn't communicate with the NVIDIA driver: "
            f"{display}"
        )
    return f"nvidia-smi exit {returncode}: {display}"


def _smi_error_is_device_lost(error: str) -> bool:
    return "Unable to determine the device handle" in (error or "")


def _smi_error_is_driver_library_mismatch(error: str) -> bool:
    return "Driver/library version mismatch" in (error or "")


def _smi_error_is_driver_unreachable(error: str) -> bool:
    return "couldn't communicate with the NVIDIA driver" in (error or "")


def _run(cmd: list, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _redact_text(value: str) -> str:
    text = value or ""
    for pattern, replacement in REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _xid_154_recovery_action(message: str) -> Optional[str]:
    for action in DRAIN_RECOVERY_ACTIONS:
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
        r"NVRM:\s+Xid\s+\((?:PCI:)?([0-9a-fA-F:.]+)\):\s+(\d+)(.*)",
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
                if action.lower() in DRAIN_RECOVERY_ACTIONS_LOWER:
                    event["severity"] = "DRAIN"
        return event

    bus_match = re.search(
        r"NVRM:\s+GPU\s+([0-9a-fA-F:.]+):\s+GPU has fallen off the bus",
        line,
        re.IGNORECASE,
    )
    if not bus_match:
        bus_match = re.search(
            r"NVRM:\s+GPU\s+at\s+([0-9a-fA-F:.]+)\s+has fallen off the bus",
            line,
            re.IGNORECASE,
        )
    if bus_match:
        return {
            "xid": 79,
            "pci": bus_match.group(1).strip(),
            "description": XID_DESC[79],
            "severity": _xid_severity(79),
            "message": "GPU has fallen off the bus",
            "raw": _redact_text(line.strip()),
        }
    return None


def _record_xid_events(result: XidResult, text: str) -> XidResult:
    drain_pairs = {
        (event.get("xid"), event.get("pci"))
        for event in result.events
        if event.get("severity") == "DRAIN"
    }
    watch_pairs = {
        (event.get("xid"), event.get("pci"))
        for event in result.events
        if event.get("severity") == "WATCH"
    }
    for line in text.splitlines():
        event = _parse_xid_line(line)
        if not event:
            continue
        code = event["xid"]
        pci = event["pci"]
        pair = (code, pci)
        result.events.append(event)
        if event["severity"] == "DRAIN":
            if code not in result.drain_xids_found:
                result.drain_xids_found.append(code)
            result.passed = False
            if pair not in drain_pairs:
                detail = event.get("recovery_action") or event["description"]
                result.warnings.append(f"Xid {code} ({detail}) on {pci}")
                drain_pairs.add(pair)
        elif event["severity"] == "WATCH":
            if code not in result.watch_xids_found:
                result.watch_xids_found.append(code)
            if pair not in watch_pairs:
                result.warnings.append(f"Xid {code} ({event['description']}) on {pci}")
                watch_pairs.add(pair)
    return result


def _parse_smi_line(line: str, fallback_index: int) -> GpuInfo:
    try:
        parts = [part.strip() for part in next(csv.reader([line], skipinitialspace=True))]
    except (csv.Error, StopIteration):
        parts = []
    gpu = GpuInfo(index=fallback_index)
    if parts:
        gpu.index = _parse_int(parts[0], fallback_index)
    if len(parts) > 1:
        gpu.name = parts[1]
    if len(parts) != EXPECTED_SMI_COLUMNS:
        gpu.passed = False
        gpu.error = f"unexpected nvidia-smi column count: {len(parts)}"
        return gpu

    unsupported = [
        field
        for field, value in zip(SMI_FIELD_NAMES[2:], parts[2:])
        if _is_unsupported_smi_value(value)
    ]
    if unsupported:
        gpu.passed = False
        gpu.error = "unsupported nvidia-smi fields: " + ", ".join(unsupported)
        return gpu

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
        err = _format_smi_error(proc.returncode, proc.stderr, proc.stdout)
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
        err = _format_smi_error(proc.returncode, proc.stderr, proc.stdout)
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


def _try_journalctl_xids(result: XidResult) -> Optional[XidResult]:
    try:
        journal = _run(JOURNALCTL_KERNEL_CMD, timeout=10)
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return None

    if journal.returncode == 0 and journal.stdout.strip():
        result.log_source = "journalctl-k"
        return _record_xid_events(result, journal.stdout)
    return None


def check_xid() -> XidResult:
    result = XidResult()
    used_filtered = True
    try:
        proc = _run(DMESG_FILTERED_CMD, timeout=10)
        if proc.returncode != 0 or not proc.stdout.strip():
            proc = _run(DMESG_FULL_CMD, timeout=10)
            used_filtered = False
    except FileNotFoundError:
        journal_result = _try_journalctl_xids(result)
        if journal_result is not None:
            return journal_result
        result.available = False
        result.error = "dmesg not found"
        result.log_source = "unavailable-no-dmesg"
        return result
    except subprocess.TimeoutExpired:
        journal_result = _try_journalctl_xids(result)
        if journal_result is not None:
            return journal_result
        result.available = False
        result.error = "dmesg timed out"
        result.log_source = "unavailable-timeout"
        return result

    if proc.returncode != 0:
        journal_result = _try_journalctl_xids(result)
        if journal_result is not None:
            return journal_result

        result.available = False
        result.error = "kernel logs unavailable to this process; run on the host or with admin privileges if appropriate"
        result.log_source = "unavailable-restricted"
        return result

    if not proc.stdout.strip():
        journal_result = _try_journalctl_xids(result)
        if journal_result is not None:
            return journal_result

        result.available = False
        result.error = "kernel logs unavailable to this process; no current-boot log text was visible"
        result.log_source = "unavailable-empty"
        return result

    result.log_source = "dmesg-cmd"
    result = _record_xid_events(result, proc.stdout)
    if result.events or not used_filtered:
        return result

    try:
        full = _run(DMESG_FULL_CMD, timeout=10)
    except FileNotFoundError:
        return result
    except subprocess.TimeoutExpired:
        return result

    if full.returncode == 0 and full.stdout.strip():
        full_result = XidResult(log_source="dmesg-cmd")
        return _record_xid_events(full_result, full.stdout)

    if full.returncode != 0:
        journal_result = _try_journalctl_xids(XidResult())
        if journal_result is not None:
            return journal_result
    return result


def _aggregate(weights: list) -> float:
    score = 0.0
    for offset, weight in enumerate(sorted(weights, reverse=True)):
        score += weight * (0.5 ** offset)
    return min(1.0, score)


def _node_nvidia_smi_error(gpus: dict) -> Optional[str]:
    errors = sorted({
        gpu.error
        for gpu in gpus.values()
        if gpu is not None and gpu.error and _smi_error_is_device_lost(gpu.error)
    })
    return errors[0] if errors else None


def _gpu_tier(signals: dict, unknown: bool) -> str:
    if "ecc_dbe_volatile" in signals or "thermal_hw_throttle_combo" in signals:
        return "DRAIN"

    watch_signals = {
        "ecc_dbe_aggregate",
        "ecc_sbe_high",
        "hw_throttle",
        "temp_critical",
        "temp_elevated",
    }
    if any(signal in signals for signal in watch_signals):
        return "WATCH"

    if unknown:
        return "UNKNOWN"
    return "CLEAR"


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
            unknown = True
            signals["nvidia_smi_unavailable"] = 0.0
            evidence.append(
                "nvidia-smi reported a device handle error; "
                "node-level evidence owns this signal"
            )
        elif _smi_error_is_driver_library_mismatch(gpu.error):
            unknown = True
            signals["nvidia_smi_driver_library_mismatch"] = 0.0
            evidence.append(f"nvidia-smi unavailable: {gpu.error}")
        else:
            unknown = True
            signals["nvidia_smi_unavailable"] = 0.0
            evidence.append(f"nvidia-smi unavailable: {gpu.error}")
    else:
        if gpu.ecc_dbe_volatile > 0:
            signals["ecc_dbe_volatile"] = min(1.0, 0.70 + gpu.ecc_dbe_volatile * 0.10)
            evidence.append(f"nvidia-smi: DBE ECC volatile: {gpu.ecc_dbe_volatile}")
        elif gpu.ecc_dbe_aggregate > 0:
            signals["ecc_dbe_aggregate"] = 0.30
            evidence.append(f"nvidia-smi: DBE ECC aggregate: {gpu.ecc_dbe_aggregate}")

        if gpu.ecc_sbe_volatile > 100:
            signals["ecc_sbe_high"] = 0.15
            evidence.append(f"nvidia-smi: SBE ECC volatile: {gpu.ecc_sbe_volatile}")
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
            evidence.append(f"nvidia-smi: HW throttle active: {', '.join(hw_throttle)}")
        elif sw_throttle:
            signals["sw_throttle"] = 0.10
            evidence.append(f"nvidia-smi: SW throttle: {', '.join(sw_throttle)}")

        if gpu.temperature_gpu is not None:
            if gpu.temperature_gpu > 88:
                signals["temp_critical"] = 0.35
                evidence.append(f"nvidia-smi: GPU temperature critical: {gpu.temperature_gpu:.0f}C")
            elif gpu.temperature_gpu > 83:
                signals["temp_elevated"] = 0.12
                evidence.append(f"nvidia-smi: GPU temperature elevated: {gpu.temperature_gpu:.0f}C")

        if "hw_throttle" in signals and "temp_critical" in signals:
            signals["thermal_hw_throttle_combo"] = 0.50
            evidence.append(
                "nvidia-smi: critical temperature with HW throttle: explicit drain combo"
            )

    score = _aggregate(list(signals.values()))
    tier = _gpu_tier(signals, unknown)
    return RiskScore(gpu_index, score, tier, signals, evidence)


def _max_tier(tiers: list) -> str:
    return max(tiers or ["CLEAR"], key=lambda tier: TIER_PRIORITY.get(tier, 0))


def _format_xid_event(event: dict) -> str:
    code = event["xid"]
    detail = event.get("recovery_action") or event.get("description") or f"Xid {code}"
    pci = event.get("pci") or "unknown PCI"
    return f"Xid {code} ({detail}) on {pci}"


def _xid_details(xid: XidResult, severity: str) -> list:
    codes = xid.drain_xids_found if severity == "DRAIN" else xid.watch_xids_found
    details = []
    seen = set()

    for event in xid.events:
        if event.get("severity") != severity:
            continue
        detail = _format_xid_event(event)
        if detail not in seen:
            details.append(detail)
            seen.add(detail)

    if details:
        return details

    for warning in xid.warnings:
        if any(warning.startswith(f"Xid {code} ") for code in codes):
            details.append(warning)

    if details:
        return details
    return [str(code) for code in codes]


def _node_tier_from_signals(signals: dict, xid: Optional[XidResult]) -> str:
    if "nvidia_smi_device_lost" in signals or "xid_drain" in signals:
        return "DRAIN"
    if "xid_watch" in signals:
        return "WATCH"
    if (
        "gpu_discovery_unavailable" in signals
        or "nvidia_smi_driver_library_mismatch" in signals
        or "nvidia_smi_driver_unreachable" in signals
    ):
        return "UNKNOWN"
    if xid is None or not xid.available:
        return "UNKNOWN"
    return "CLEAR"


def _gpu_count_label(count: int) -> str:
    if count == 1:
        return "1 selected GPU"
    return f"{count} selected GPUs"


def _score_has_complete_smi(score: RiskScore) -> bool:
    return not any(signal.startswith("nvidia_smi_") for signal in score.signals)


def _visibility_summary(scores: list, xid: Optional[XidResult]) -> list:
    visibility = []

    if scores:
        visible_count = sum(1 for score in scores if _score_has_complete_smi(score))
        incomplete_count = len(scores) - visible_count
        if visible_count:
            visibility.append(
                "nvidia-smi GPU query visible on "
                + _gpu_count_label(visible_count)
            )
        if incomplete_count:
            visibility.append(
                "nvidia-smi GPU query incomplete for "
                + _gpu_count_label(incomplete_count)
            )
        if visible_count == len(scores) and all(score.tier == "CLEAR" for score in scores):
            visibility.append("no local GPU drain/watch evidence visible")
    else:
        visibility.append("no selected GPUs scanned")

    visibility.extend(_xid_visibility(xid))

    return visibility


def _xid_visibility(xid: Optional[XidResult]) -> list:
    if xid is None:
        return ["Xid scan not run"]
    if xid.available:
        source = f" via {xid.log_source}" if xid.log_source != "unknown" else ""
        return ["Xid scan available" + source]
    return [
        "Xid scan unavailable: "
        + (xid.error or "kernel log access restricted")
    ]


def _gpu_primary_issue(score: RiskScore) -> Optional[str]:
    signals = score.signals
    index = score.gpu_index
    if "ecc_dbe_volatile" in signals:
        return f"GPU {index} has volatile DBE ECC evidence"
    if "thermal_hw_throttle_combo" in signals:
        return f"GPU {index} is critically hot and hardware throttle is active"
    if "hw_throttle" in signals:
        return f"GPU {index} reports hardware throttle"
    if "temp_critical" in signals:
        return f"GPU {index} temperature is critical"
    if "temp_elevated" in signals:
        return f"GPU {index} temperature is elevated"
    if "ecc_dbe_aggregate" in signals:
        return f"GPU {index} has aggregate DBE ECC history"
    if "ecc_sbe_high" in signals:
        return f"GPU {index} has high volatile SBE ECC count"
    if "nvidia_smi_driver_library_mismatch" in signals:
        return "NVIDIA driver/library mismatch prevents local GPU state"
    if "nvidia_smi_unavailable" in signals:
        return f"nvidia-smi could not provide complete GPU {index} state"
    return None


def _primary_issue(tier: str, signals: dict, scores: list) -> str:
    if "nvidia_smi_device_lost" in signals:
        return "nvidia-smi cannot determine a GPU device handle"
    if "xid_drain" in signals:
        return "drain-class NVIDIA Xid evidence is visible in current-boot kernel logs"

    for score in sorted(scores, key=lambda item: item.gpu_index):
        if score.tier == "DRAIN":
            issue = _gpu_primary_issue(score)
            if issue:
                return issue

    if "xid_watch" in signals:
        return "watch-class NVIDIA Xid evidence is visible in current-boot kernel logs"

    for score in sorted(scores, key=lambda item: item.gpu_index):
        if score.tier == "WATCH":
            issue = _gpu_primary_issue(score)
            if issue:
                return issue

    if "gpu_discovery_unavailable" in signals:
        return "this shell cannot see local NVIDIA GPU state"
    if "nvidia_smi_driver_library_mismatch" in signals:
        return "NVIDIA driver/library mismatch prevents local GPU state"
    if "nvidia_smi_driver_unreachable" in signals:
        return "nvidia-smi cannot communicate with the NVIDIA driver"
    if "cli_error" in signals:
        return "invalid command-line input prevented the scan"
    if "xid_log_unavailable" in signals:
        return "this shell cannot see current-boot NVIDIA kernel logs"

    for score in sorted(scores, key=lambda item: item.gpu_index):
        if score.tier == "UNKNOWN":
            issue = _gpu_primary_issue(score)
            if issue:
                return issue

    if tier == "UNKNOWN":
        return "this shell cannot see enough local GPU or kernel-log state"
    return "none visible in this local scan"


def build_node_report(
    scores: list,
    xid: Optional[XidResult],
    nvidia_smi_error: Optional[str] = None,
) -> NodeReport:
    signals = {}
    evidence = []

    if nvidia_smi_error:
        signals["nvidia_smi_device_lost"] = 0.70
        evidence.append(
            "nvidia-smi could not query GPU state: "
            + nvidia_smi_error
        )

    _record_xid_report(signals, evidence, xid)

    local_tier = _node_tier_from_signals(signals, xid)
    tier = _max_tier([local_tier, node_tier(scores)])

    return NodeReport(
        tier=tier,
        primary_issue=_primary_issue(tier, signals, scores),
        visibility=_visibility_summary(scores, xid),
        signals=signals,
        evidence=evidence,
    )


def _record_xid_report(signals: dict, evidence: list, xid: Optional[XidResult]) -> None:
    if xid is not None and xid.available:
        if xid.drain_xids_found:
            signals["xid_drain"] = 0.85
            detail = "; ".join(_xid_details(xid, "DRAIN"))
            evidence.append(
                f"Critical Xid events in {xid.log_source}: "
                + detail
            )
        if xid.watch_xids_found:
            signals["xid_watch"] = 0.25
            detail = "; ".join(_xid_details(xid, "WATCH"))
            evidence.append(
                f"Xid events in {xid.log_source}: "
                + detail
            )
    elif xid is None:
        signals["xid_log_unavailable"] = 0.0
        evidence.append("Xid scan unavailable: kernel log scan was not run")
    elif xid is not None and not xid.available:
        signals["xid_log_unavailable"] = 0.0
        evidence.append(
            f"Xid scan unavailable: {xid.error or 'kernel log access restricted'}"
        )


def parse_gpu_list(value: str, available) -> list:
    if isinstance(available, int):
        available_indices = list(range(available))
    else:
        available_indices = sorted(set(int(index) for index in available))

    if value is None or not value.strip():
        raise ValueError("empty GPU selection")
    if value.lower() == "all":
        return available_indices
    indices = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            raise ValueError("empty GPU selection")
        if "-" in part:
            start, end = part.split("-", 1)
            if not start or not end:
                raise ValueError(f"invalid GPU range: {part}")
            start_index = int(start)
            end_index = int(end)
            if end_index < start_index:
                raise ValueError(f"reversed GPU range: {part}")
            indices.extend(range(start_index, end_index + 1))
        else:
            indices.append(int(part))
    selected = sorted(set(indices))
    if not selected:
        raise ValueError("empty GPU selection")
    return selected


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


def _node_tier_label(tier: str) -> str:
    if tier == "CLEAR":
        return "CLEAR (no visible local drain/watch evidence)"
    if tier == "UNKNOWN":
        return "UNKNOWN (not enough local evidence)"
    return tier


def print_text(gpus: dict, scores: list, report: NodeReport, elapsed: float):
    tier = report.tier
    print("scanprobe")
    print(CLAIM_CONTEXT_TEXT)
    print(MODE_CONTEXT_TEXT)
    print(RECENCY_CONTEXT_TEXT)
    print("")
    print(f"Node: {_node_tier_label(tier)}")
    print(f"Primary issue: {report.primary_issue}.")
    print("")
    print("Visibility:")
    _print_bullets(report.visibility)
    print("")
    print("Node-level evidence:")
    if report.evidence:
        _print_bullets(report.evidence)
    else:
        print("  - no node-level drain/watch evidence observed")
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
            print("  - nvidia-smi: no local GPU drain/watch evidence observed")
    print("")
    print("Next action:")
    _print_bullets(next_actions(tier))
    print("")
    print(NOT_CHECKED_TEXT)
    print(f"Completed in {elapsed:.1f}s")


def _print_simple_text_report(report: NodeReport, evidence: list, elapsed: float):
    print("scanprobe")
    print(CLAIM_CONTEXT_TEXT)
    print(MODE_CONTEXT_TEXT)
    print(RECENCY_CONTEXT_TEXT)
    print("")
    print(f"Node: {_node_tier_label(report.tier)}")
    print(f"Primary issue: {report.primary_issue}.")
    if report.visibility:
        print("")
        print("Visibility:")
        _print_bullets(report.visibility)
    print("")
    print("Visible evidence:")
    _print_bullets(evidence)
    print("")
    print("Next action:")
    _print_bullets(next_actions(report.tier))
    print("")
    print(NOT_CHECKED_TEXT)
    print(f"Completed in {elapsed:.1f}s")


def _base_json_payload(report: NodeReport, elapsed: float) -> dict:
    return {
        "version": __version__,
        "elapsed_s": round(elapsed, 2),
        "claim_context": CLAIM_CONTEXT_TEXT,
        "mode": MODE_CONTEXT_TEXT,
        "kernel_log_scope": RECENCY_CONTEXT_TEXT,
        "automation": AUTOMATION_CONTEXT,
        "not_checked": NOT_CHECKED_TEXT,
        "node_tier": report.tier,
        "node_report": asdict(report),
        "next_action": next_actions(report.tier),
    }


def print_json(gpus: dict, scores: list, report: NodeReport, xid: XidResult, elapsed: float):
    out = _base_json_payload(report, elapsed)
    out.update({
        "risk_scores": [asdict(score) for score in scores],
        "nvidia_smi": {str(index): asdict(gpu) for index, gpu in sorted(gpus.items())},
        "xid": asdict(xid) if xid else None,
    })
    print(json.dumps(out, indent=2))


def _discovery_failure_report(
    discovery: GpuDiscovery,
    xid: Optional[XidResult] = None,
) -> NodeReport:
    message = discovery.error or "nvidia-smi did not report any GPUs"
    signals = {}
    evidence = [message]

    if _smi_error_is_device_lost(message):
        signals["nvidia_smi_device_lost"] = 0.70
        visibility = ["nvidia-smi GPU discovery could not complete"]
    elif _smi_error_is_driver_library_mismatch(message):
        signals["nvidia_smi_driver_library_mismatch"] = 0.0
        visibility = ["nvidia-smi GPU discovery unavailable: " + message]
    elif _smi_error_is_driver_unreachable(message):
        signals["nvidia_smi_driver_unreachable"] = 0.0
        visibility = ["nvidia-smi GPU discovery unavailable: " + message]
    elif discovery.status == "none":
        signals["gpu_discovery_unavailable"] = 0.0
        visibility = ["nvidia-smi GPU discovery found no visible GPUs"]
    else:
        signals["gpu_discovery_unavailable"] = 0.0
        visibility = ["nvidia-smi GPU discovery unavailable: " + message]

    if xid is not None:
        _record_xid_report(signals, evidence, xid)
    visibility.extend(_xid_visibility(xid))
    tier = _node_tier_from_signals(signals, xid)

    if _smi_error_is_device_lost(message):
        return NodeReport(
            tier=tier,
            primary_issue="nvidia-smi cannot determine a GPU device handle",
            visibility=visibility,
            signals=signals,
            evidence=evidence,
        )

    return NodeReport(
        tier=tier,
        primary_issue=_primary_issue(tier, signals, []),
        visibility=visibility,
        signals=signals,
        evidence=evidence,
    )


def print_discovery_failure(
    discovery: GpuDiscovery,
    elapsed: float,
    as_json: bool,
    xid: Optional[XidResult] = None,
):
    message = discovery.error or "nvidia-smi did not report any GPUs"
    report = _discovery_failure_report(discovery, xid)
    if as_json:
        out = _base_json_payload(report, elapsed)
        out.update({
            "risk_scores": [],
            "nvidia_smi": {},
            "xid": asdict(xid) if xid else None,
            "gpu_discovery": asdict(discovery),
            "evidence": report.evidence,
        })
        print(json.dumps(out, indent=2))
        return

    _print_simple_text_report(report, report.evidence, elapsed)


def _should_scan_xid_after_discovery_failure(discovery: GpuDiscovery) -> bool:
    return discovery.error != "nvidia-smi not found"


def print_cli_error(message: str, elapsed: float, as_json: bool):
    report = NodeReport(
        tier="UNKNOWN",
        primary_issue="invalid command-line input prevented the scan",
        visibility=["scan stopped before GPU query and Xid scan"],
        signals={"cli_error": 0.0},
        evidence=[message],
    )
    if as_json:
        out = _base_json_payload(report, elapsed)
        out.update({
            "risk_scores": [],
            "nvidia_smi": {},
            "xid": None,
            "evidence": [message],
        })
        print(json.dumps(out, indent=2))
        return

    _print_simple_text_report(report, [message], elapsed)


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
        xid = None
        if _should_scan_xid_after_discovery_failure(discovery):
            xid = check_xid()
        report = _discovery_failure_report(discovery, xid)
        print_discovery_failure(discovery, time.time() - start, args.json, xid=xid)
        if report.tier == "DRAIN":
            return 2
        if report.tier == "WATCH":
            return 1
        if report.tier == "CLEAR":
            return 0
        if report.tier == "UNKNOWN":
            return 3
        return 3

    try:
        indices = parse_gpu_list(args.gpus, discovery.indices)
    except ValueError as exc:
        print_cli_error(f"Invalid --gpus argument: {exc}", time.time() - start, args.json)
        return 3

    gpus = query_gpus(indices)
    xid = check_xid()
    scores = [score_gpu(gpus.get(index), index) for index in indices]
    report = build_node_report(scores, xid, nvidia_smi_error=_node_nvidia_smi_error(gpus))
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
