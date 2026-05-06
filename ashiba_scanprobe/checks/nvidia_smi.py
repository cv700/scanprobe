"""
nvidia-smi check: ECC errors, power, temperature, clock throttle reasons.
Queries all GPUs in a single subprocess call for efficiency.
"""

import subprocess
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NvidiaSmiResult:
    gpu_index: int
    name: str = "unknown"
    uuid: str = "unknown"
    # ECC
    ecc_enabled: bool = False
    ecc_sbe_volatile: int = 0       # single-bit errors since last driver reload
    ecc_dbe_volatile: int = 0       # double-bit errors since last driver reload
    ecc_sbe_aggregate: int = 0      # single-bit errors lifetime
    ecc_dbe_aggregate: int = 0      # double-bit errors lifetime
    # Thermals
    temperature_gpu: Optional[float] = None
    temperature_memory: Optional[float] = None
    power_draw_w: Optional[float] = None
    power_limit_w: Optional[float] = None
    # Clocks
    clock_sm_mhz: Optional[float] = None
    clock_mem_mhz: Optional[float] = None
    clock_throttle_reasons: list = field(default_factory=list)
    # Memory
    memory_used_mib: Optional[float] = None
    memory_total_mib: Optional[float] = None
    # PCIe
    pcie_link_gen: Optional[int] = None
    pcie_link_width: Optional[int] = None
    # Status
    passed: bool = True
    error: Optional[str] = None
    warnings: list = field(default_factory=list)


# nvidia-smi query fields, in order — must match _parse_line() index assumptions
_FIELDS = ",".join([
    "index",
    "name",
    "uuid",
    "ecc.mode.current",
    "ecc.errors.corrected.volatile.total",
    "ecc.errors.uncorrected.volatile.total",
    "ecc.errors.corrected.aggregate.total",
    "ecc.errors.uncorrected.aggregate.total",
    "temperature.gpu",
    "temperature.memory",
    "power.draw",
    "power.limit",
    "clocks.current.sm",
    "clocks.current.memory",
    "clocks_throttle_reasons.active",
    "memory.used",
    "memory.total",
    "pcie.link.gen.current",
    "pcie.link.width.current",
])


def _parse_int(s: str, default: int = 0) -> int:
    s = s.strip().replace(",", "")
    if s in ("N/A", "[Not Supported]", "[N/A]", ""):
        return default
    try:
        return int(s)
    except ValueError:
        return default


def _parse_float(s: str) -> Optional[float]:
    s = s.strip().replace(",", "")
    if s in ("N/A", "[Not Supported]", "[N/A]", ""):
        return None
    # Strip unit suffixes that may appear even with nounits (defensive)
    for unit in (" W", " MiB", " MHz", " %", "°C", " C"):
        s = s.replace(unit, "")
    try:
        return float(s.strip())
    except ValueError:
        return None


def _decode_throttle_reasons(hex_str: str) -> list:
    """Decode NVML clock throttle bitmask to human-readable reason strings."""
    REASONS = {
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
    try:
        val = int(hex_str.strip(), 16)
        return [name for mask, name in REASONS.items() if val & mask]
    except (ValueError, TypeError):
        return []


def _parse_line(line: str, gpu_index: int) -> NvidiaSmiResult:
    """Parse one CSV line from nvidia-smi into a NvidiaSmiResult."""
    result = NvidiaSmiResult(gpu_index=gpu_index)
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 19:
        result.error = f"unexpected nvidia-smi column count: {len(parts)}"
        result.passed = False
        return result

    result.name               = parts[1]
    result.uuid               = parts[2]
    result.ecc_enabled        = parts[3].lower() in ("enabled", "1", "true")
    result.ecc_sbe_volatile   = _parse_int(parts[4])
    result.ecc_dbe_volatile   = _parse_int(parts[5])
    result.ecc_sbe_aggregate  = _parse_int(parts[6])
    result.ecc_dbe_aggregate  = _parse_int(parts[7])
    result.temperature_gpu    = _parse_float(parts[8])
    result.temperature_memory = _parse_float(parts[9])
    result.power_draw_w       = _parse_float(parts[10])
    result.power_limit_w      = _parse_float(parts[11])
    result.clock_sm_mhz       = _parse_float(parts[12])
    result.clock_mem_mhz      = _parse_float(parts[13])
    result.clock_throttle_reasons = _decode_throttle_reasons(parts[14])
    result.memory_used_mib    = _parse_float(parts[15])
    result.memory_total_mib   = _parse_float(parts[16])
    result.pcie_link_gen      = _parse_int(parts[17]) or None
    result.pcie_link_width    = _parse_int(parts[18]) or None

    # Derive warnings and pass/fail
    if result.ecc_dbe_volatile > 0:
        result.passed = False
        result.warnings.append(
            f"DBE volatile ECC errors: {result.ecc_dbe_volatile} — uncorrectable, high severity"
        )
    if result.ecc_dbe_aggregate > 0:
        result.warnings.append(
            f"DBE aggregate ECC errors: {result.ecc_dbe_aggregate} — lifetime uncorrected"
        )
    if result.ecc_sbe_volatile > 10:
        result.warnings.append(
            f"High SBE volatile ECC: {result.ecc_sbe_volatile} — corrected, monitor"
        )
    if result.temperature_gpu is not None and result.temperature_gpu > 83:
        result.warnings.append(
            f"GPU temperature {result.temperature_gpu:.0f}°C (limit ~85°C)"
        )
    active_throttle = [r for r in result.clock_throttle_reasons if r != "GpuIdle"]
    if active_throttle:
        result.warnings.append(f"Clock throttle: {', '.join(active_throttle)}")
    if result.power_draw_w is not None and result.power_limit_w is not None:
        ratio = result.power_draw_w / result.power_limit_w
        if ratio > 0.98:
            result.warnings.append(
                f"Power {result.power_draw_w:.0f}W at {ratio*100:.0f}% of limit {result.power_limit_w:.0f}W"
            )

    return result


def check_all_nvidia_smi(gpu_indices: list) -> dict:
    """
    Query nvidia-smi once for all requested GPU indices.
    Returns {gpu_index: NvidiaSmiResult}.
    """
    results = {}

    try:
        proc = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=" + _FIELDS,
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=30
        )
    except FileNotFoundError:
        err = "nvidia-smi not found — is this a CUDA host?"
        for idx in gpu_indices:
            r = NvidiaSmiResult(gpu_index=idx, passed=False, error=err)
            results[idx] = r
        return results
    except subprocess.TimeoutExpired:
        err = "nvidia-smi timed out (30s)"
        for idx in gpu_indices:
            r = NvidiaSmiResult(gpu_index=idx, passed=False, error=err)
            results[idx] = r
        return results
    except Exception as e:
        for idx in gpu_indices:
            r = NvidiaSmiResult(gpu_index=idx, passed=False, error=str(e))
            results[idx] = r
        return results

    if proc.returncode != 0:
        err = f"nvidia-smi exit {proc.returncode}: {proc.stderr.strip()[:200]}"
        for idx in gpu_indices:
            r = NvidiaSmiResult(gpu_index=idx, passed=False, error=err)
            results[idx] = r
        return results

    lines = [l for l in proc.stdout.strip().splitlines() if l.strip()]
    # lines[i] corresponds to GPU index i (nvidia-smi orders by index)
    line_map = {i: line for i, line in enumerate(lines)}

    for idx in gpu_indices:
        if idx not in line_map:
            results[idx] = NvidiaSmiResult(
                gpu_index=idx, passed=False,
                error=f"GPU {idx} not found in nvidia-smi output ({len(lines)} GPUs reported)"
            )
        else:
            results[idx] = _parse_line(line_map[idx], idx)

    return results


def check_nvidia_smi(gpu_index: int = 0) -> NvidiaSmiResult:
    """Query a single GPU. Convenience wrapper around check_all_nvidia_smi."""
    return check_all_nvidia_smi([gpu_index]).get(
        gpu_index,
        NvidiaSmiResult(gpu_index=gpu_index, passed=False, error="query failed")
    )


def count_gpus() -> int:
    """Return number of CUDA GPUs visible to nvidia-smi."""
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10
        )
        if proc.returncode != 0:
            return 0
        return len([l for l in proc.stdout.strip().splitlines() if l.strip()])
    except Exception:
        return 0
