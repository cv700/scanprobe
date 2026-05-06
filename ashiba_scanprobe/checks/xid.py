"""
Xid error check: scans dmesg for NVIDIA Xid hardware error codes.

Xid events are NVIDIA's own hardware error taxonomy written directly into
the kernel ring buffer. They're the most direct available signal of GPU
hardware faults — no ML workload required.

Common critical Xids:
  48  — Double-bit ECC error (DBE) — uncorrectable memory error
  64  — Row remapping failure — HBM retirement recording failed
  74  — NVLink error — inter-GPU fabric fault
  79  — GPU fallen off the bus
  95  — Uncontained error (requires GPU reset)
  140 — Unrecoverable ECC escape
  143 — GPU init error

Requires: root or adm group membership (for dmesg on some systems).
Pure stdlib — no external dependencies.

Source: NVIDIA "Xid Errors" and "GPU Debug Guidelines" (2026) — per-code
operator actions distinguish restart-app / reset-GPU / drain-class events.
"""

import subprocess
import re
from dataclasses import dataclass, field
from typing import Optional


# Xid codes considered critical — any of these → DRAIN
DRAIN_XIDS = {48, 64, 74, 79, 95, 140, 143}

# Xid codes worth watching but not draining alone
# 63 = row-remap success; 92 = high SBE rate; 94 = contained ECC.
WATCH_XIDS = {13, 31, 32, 43, 45, 63, 69, 92, 94, 109, 119, 120}

XID_DESCRIPTIONS = {
    13:  "Graphics engine exception",
    31:  "GPU memory page fault",
    32:  "Invalid or corrupted push buffer stream",
    43:  "GPU stopped processing (long compute)",
    45:  "Preemptive cleanup (application error)",
    48:  "DBE ECC error — uncorrectable memory",
    63:  "Row remapping event recorded",
    64:  "Row remapping failure — recording failed",
    69:  "Graphics engine class error",
    74:  "NVLink error",
    79:  "GPU has fallen off the bus",
    92:  "High single-bit ECC error rate",
    94:  "Contained ECC or channel error",
    95:  "Uncontained error — GPU reset required",
    109: "Context switch timeout",
    119: "GSP RPC timeout",
    120: "GSP error",
    140: "Unrecoverable ECC error escape",
    143: "GPU init error",
}


@dataclass
class XidResult:
    available: bool = True          # False if dmesg is inaccessible
    passed: bool = True
    error: Optional[str] = None
    log_source: str = "unknown"
    events: list = field(default_factory=list)   # [{xid, gpu, message, raw}]
    drain_xids_found: list = field(default_factory=list)
    watch_xids_found: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def check_xid(since_boot: bool = True) -> XidResult:
    """
    Scan dmesg for NVIDIA Xid events.
    since_boot=True reads all events since last boot (default).
    Pure stdlib — no external dependencies.
    """
    result = XidResult()

    try:
        proc = subprocess.run(
            ["dmesg", "--level=err,warn,crit,alert,emerg"],
            capture_output=True, text=True, timeout=10
        )
        # Some systems require sudo for dmesg; try plain dmesg as fallback
        if proc.returncode != 0 or not proc.stdout.strip():
            proc = subprocess.run(
                ["dmesg"],
                capture_output=True, text=True, timeout=10
            )
        if proc.returncode != 0:
            result.available = False
            result.log_source = "unavailable-restricted"
            result.error = (
                f"dmesg failed (exit {proc.returncode}) — kernel log unavailable; "
                "try: sudo scanprobe"
            )
            result.passed = True  # not a fault, just unavailable
            return result

        output = proc.stdout
        result.log_source = "dmesg-cmd"

    except FileNotFoundError:
        result.available = False
        result.log_source = "unavailable-no-dmesg"
        result.error = "dmesg not found"
        result.passed = True
        return result
    except subprocess.TimeoutExpired:
        result.available = False
        result.log_source = "unavailable-timeout"
        result.error = "dmesg timed out"
        result.passed = True
        return result
    except Exception as e:
        result.available = False
        result.log_source = "unavailable-error"
        result.error = str(e)
        result.passed = True
        return result

    # Parse Xid lines:  NVRM: Xid (PCI:0000:XX:XX): YY, ...
    # Also catches:      kernel: NVRM: Xid ...
    xid_pattern = re.compile(
        r"NVRM:\s+Xid\s+\(PCI:([^)]+)\):\s+(\d+)(.*)",
        re.IGNORECASE
    )

    seen = set()
    for line in output.splitlines():
        m = xid_pattern.search(line)
        if not m:
            continue
        pci_addr = m.group(1).strip()
        xid_code = int(m.group(2))
        detail = m.group(3).strip()

        result.events.append({
            "xid": xid_code,
            "pci": pci_addr,
            "description": XID_DESCRIPTIONS.get(xid_code, f"Xid {xid_code}"),
            "detail": detail[:120],
            "severity": "DRAIN" if xid_code in DRAIN_XIDS else
                        "WATCH" if xid_code in WATCH_XIDS else "INFO",
        })

        if xid_code in DRAIN_XIDS and xid_code not in result.drain_xids_found:
            result.drain_xids_found.append(xid_code)
            result.passed = False
            result.warnings.append(
                f"Xid {xid_code} ({XID_DESCRIPTIONS.get(xid_code, '?')}) on {pci_addr}"
            )
        elif xid_code in WATCH_XIDS and xid_code not in result.watch_xids_found:
            result.watch_xids_found.append(xid_code)
            result.warnings.append(
                f"Xid {xid_code} ({XID_DESCRIPTIONS.get(xid_code, '?')}) on {pci_addr}"
            )

    return result
