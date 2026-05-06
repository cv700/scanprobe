"""
Xid error check: scans dmesg for NVIDIA Xid hardware error codes.

Xid events are NVIDIA's own hardware error taxonomy written directly into
the kernel ring buffer. They're the most direct available signal of GPU
hardware faults — no ML workload required.

Common critical Xids:
  48  — Double-bit ECC error (DBE) — uncorrectable memory error
  63  — Row remapping failure — HBM memory row retired permanently
  74  — NVLink error — inter-GPU fabric fault
  79  — GPU engine hang
  94  — GPU containment error (GPC error, often mercurial core)
  95  — Uncontained error (requires GPU reset)

Requires: root or adm group membership (for dmesg on some systems).
Pure stdlib — no external dependencies.
"""

import subprocess
import re
from dataclasses import dataclass, field
from typing import Optional


# Xid codes considered critical — any of these → DRAIN
DRAIN_XIDS = {48, 63, 74, 79, 94, 95}

# Xid codes worth watching but not draining alone
WATCH_XIDS = {13, 31, 32, 43, 45, 56, 57, 58, 61, 64, 69}

XID_DESCRIPTIONS = {
    13:  "Graphics engine exception",
    31:  "GPU memory page fault",
    32:  "Invalid P2P memory access",
    43:  "GPU stopped processing (long compute)",
    45:  "Preemptive cleanup (application error)",
    48:  "DBE ECC error — uncorrectable memory",
    56:  "Display engine error",
    57:  "Error programming video memory interface",
    58:  "Unstable video memory interface detected",
    61:  "Internal micro-controller breakpoint",
    63:  "Row remapping failure — HBM row retired",
    64:  "Row remapping retired with no spare rows",
    69:  "Graphics engine class error",
    74:  "NVLink error",
    79:  "GPU engine hang",
    92:  "High single-bit ECC error rate",
    94:  "GPU containment error (GPC fault)",
    95:  "Uncontained error — GPU reset required",
}


@dataclass
class XidResult:
    available: bool = True          # False if dmesg is inaccessible
    passed: bool = True
    error: Optional[str] = None
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
            result.error = f"dmesg failed (exit {proc.returncode}) — may need elevated privileges"
            result.passed = True  # not a fault, just unavailable
            return result

        output = proc.stdout

    except FileNotFoundError:
        result.available = False
        result.error = "dmesg not found"
        result.passed = True
        return result
    except subprocess.TimeoutExpired:
        result.available = False
        result.error = "dmesg timed out"
        result.passed = True
        return result
    except Exception as e:
        result.available = False
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

        # Deduplicate repeated identical events
        key = (pci_addr, xid_code)
        count = sum(1 for e in result.events if e["xid"] == xid_code and e["pci"] == pci_addr)

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
