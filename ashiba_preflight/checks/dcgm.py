"""
DCGM diagnostic check via dcgmi.
Runs node-level diagnostics at levels 1-3 depending on tier.

Note on scope: dcgmi diag runs across all GPUs on the node by default.
Results are node-level; the scoring layer propagates any failure to all
checked GPUs equally. This is correct behavior — DCGM cannot isolate
failures to individual GPUs in levels 1-2.

Gracefully degrades if dcgmi is not installed.
"""

import subprocess
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DcgmResult:
    level: int = 1
    available: bool = False
    passed: bool = True
    error: Optional[str] = None
    warnings: list = field(default_factory=list)
    failed_tests: list = field(default_factory=list)
    raw_output: str = ""


LEVEL_DESCRIPTIONS = {
    1: "quick (~30s)",
    2: "medium (~3min)",
    3: "extended (~20min)",
}

# Timeout per level (seconds), with headroom above the stated duration
LEVEL_TIMEOUTS = {1: 120, 2: 360, 3: 1800}


def check_dcgm(level: int = 1) -> DcgmResult:
    """
    Run dcgmi diag on all node GPUs at the specified level.
    Level 1 = quick health check, 2 = medium, 3 = extended (thorough).
    Returns a node-level DcgmResult.
    """
    result = DcgmResult(level=level)

    # Probe availability first
    try:
        probe = subprocess.run(
            ["dcgmi", "diag", "--help"],
            capture_output=True, text=True, timeout=5
        )
        result.available = probe.returncode == 0 or "usage" in probe.stdout.lower()
    except FileNotFoundError:
        result.available = False
        result.error = "dcgmi not found — install DCGM: https://developer.nvidia.com/dcgm"
        result.passed = True  # absence of dcgm is not itself a GPU fault
        return result
    except Exception as e:
        result.available = False
        result.error = f"dcgmi unavailable: {e}"
        result.passed = True
        return result

    timeout = LEVEL_TIMEOUTS.get(level, 120)
    try:
        proc = subprocess.run(
            ["dcgmi", "diag", "-r", str(level)],
            capture_output=True, text=True, timeout=timeout
        )
        result.raw_output = proc.stdout + proc.stderr

        if proc.returncode != 0:
            result.passed = False

        # Extract explicit FAIL lines
        failed = [
            line.strip()
            for line in result.raw_output.splitlines()
            if re.search(r"\bfail\b", line, re.IGNORECASE)
        ]
        result.failed_tests = failed

        if failed:
            result.passed = False
            result.warnings = failed[:5]  # surface up to 5 for display
        elif "pass" in result.raw_output.lower() and proc.returncode == 0:
            result.passed = True

    except subprocess.TimeoutExpired:
        result.error = f"dcgmi diag -r{level} timed out after {timeout}s"
        result.passed = False
    except Exception as e:
        result.error = str(e)
        result.passed = False

    return result
