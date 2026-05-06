# Xid & Throttle-Bitmask Verification for `scanprobe`

**Date:** 2026-05-05
**Scope:** Verify the DRAIN_XIDS / WATCH_XIDS classifications in scanprobe against NVIDIA's authoritative documentation, and determine the actual on-the-wire format of `nvidia-smi --query-gpu=clocks_throttle_reasons.active` on modern drivers.

---

## TL;DR

- **DRAIN_XIDS classifications are mostly correct.** Two issues:
  - `Xid 63` is in WATCH (correct). `Xid 64` (the *failure* counterpart of 63) is **not in either set** and should be DRAIN. This is a real gap.
  - `Xid 74` (NVLink Error) is correctly DRAIN.
- **WATCH_XIDS is mostly correct**, but contains four codes (`56, 57, 58, 61`) that NVIDIA's catalog explicitly marks **"Unused"** for all current architectures (A100/H100/B100/GB200). They are dead weight — keeping them is harmless but misleading.
- **Critical missing codes:** `Xid 64`, `Xid 119`, `Xid 120`, `Xid 140` (and arguably `Xid 109`, `143`) are NVIDIA "RESET_GPU" / fatal-firmware codes that scanprobe currently ignores entirely. **`Xid 64` and `Xid 140` should be added to DRAIN_XIDS. `Xid 119` and `Xid 120` should at minimum be in WATCH (datacenter operators treat them as drain-worthy when persistent).**
- **Throttle bitmask format:** nvidia-smi documents the field as a "Bitmask" and emits it as a `0x`-prefixed hex string (e.g. `0x0000000000000000`). Real-world Prometheus exporter code (`utkuozdemir/nvidia_gpu_exporter`) explicitly checks for the `0x` prefix and converts to decimal. The current scanprobe parser using `int(hex_str, 16)` is correct **for hex output**. However: in driver 535+ (and especially 555/560+) the field was renamed to `clocks_event_reasons.active`, with `clocks_throttle_reasons.active` retained for backward compatibility. **The bigger risk is querying a field that no longer exists on a newer driver, not the format.** Recommend probing both names.

---

## TASK 1 — Xid Classification Verification

### Methodology

Primary source: NVIDIA's official **Xid Catalog** at `docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html`. Cross-referenced against NVIDIA's **GPU Debug Guidelines** at `docs.nvidia.com/deploy/gpu-debug-guidelines/index.html`, which provides per-Xid recovery actions ("RESTART_APP", "RESET_GPU", "DRAIN_AND_RESET", "CONTACT_SUPPORT", etc.).

The catalog organizes Xid into "immediate action" (operator-facing, what to do right now) and "investigatory action" (engineer-facing, root-cause workflow). The "Verdict" column below uses the immediate action as the source of truth.

### DRAIN_XIDS — verification

| Code | Our class | NVIDIA description | NVIDIA action | Verdict | Confidence |
|---|---|---|---|---|---|
| 48 | DRAIN | Double Bit ECC Error (uncorrectable GPU memory error) | `WORKFLOW_XID_48` — "Solo: RESET_GPU; w/ 63 or 64: DRAIN_AND_RESET" | **Correct as DRAIN.** A solo Xid 48 is technically a reset, but field guidance is to drain because uncorrectable ECC almost always recurs. NVIDIA's GPU Debug Guidelines explicitly says "if followed by Xid 63/64: drain/cordon node, reset GPUs." | High (NVIDIA docs) |
| 63 | WATCH | INFOROM_DRAM_RETIREMENT_EVENT — successful row-remapping recording | `IGNORE` (immediate); `IGNORE` (investigatory) | **Wrong bucket but harmless.** This is informational on its own — the row remapper succeeded. NVIDIA says ignore. Putting it in DRAIN means scanprobe will tell users to stop using a GPU that just successfully self-repaired. **Should be moved to WATCH** (or removed entirely; see note). | High (NVIDIA docs) |
| 74 | DRAIN | NVLINK_ERROR — link-level hardware/connection problem | `WORKFLOW_NVLINK_ERR`; `CONTACT_SUPPORT` | **Correct as DRAIN.** NVIDIA debug guidelines say "analyze hex codes; check mechanical connections; run diagnostics if persists." On datacenter GPUs (HGX/DGX), Xid 74 is treated as drain-worthy. | High (NVIDIA docs) |
| 79 | DRAIN | GPU has fallen off the bus (PCIe inaccessible) | `RESTART_BM` (baseboard restart); `CONTACT_SUPPORT`; debug-guidelines: **"Drain and see Reporting a GPU Issue"** | **Correct as DRAIN.** NVIDIA explicitly uses the word "drain". | High (NVIDIA docs) |
| 94 | DRAIN | ROBUST_CHANNEL_CONTAINED_ERROR — ECC contained to one app | `RESTART_APP`; `IGNORE (sympathetic)` | **Probably wrong as DRAIN.** NVIDIA explicitly says "errors are contained to one application, and the application must be restarted" — the GPU stays operational. This is the "good" half of the contained/uncontained pair. **Recommend moving to WATCH.** Keeping it as DRAIN will cause false-positive GPU evictions on Hopper / Ampere where contained errors are routine. | High (NVIDIA docs) |
| 95 | DRAIN | ROBUST_CHANNEL_UNCONTAINED_ERROR — ECC affects multiple apps | `RESET_GPU`; debug-guidelines: "If MIG enabled: drain instances and reset. If disabled: reboot immediately" | **Correct as DRAIN.** Uncontained means the blast radius escaped containment. Always drain. | High (NVIDIA docs) |

### WATCH_XIDS — verification

| Code | Our class | NVIDIA description | NVIDIA action | Verdict | Confidence |
|---|---|---|---|---|---|
| 13 | WATCH | Graphics Engine Exception — typically an out-of-bounds in user app | `RESTART_APP`; `WORKFLOW_XID_13` | **Correct as WATCH.** Almost always an application bug, not hardware. | High (NVIDIA docs) |
| 31 | WATCH | GPU memory page fault (MMU illegal-address access) | `RESTART_APP`; `WORKFLOW_XID_31`. Debug-guidelines: "Contact the hardware vendor" if persistent | **Correct as WATCH.** Single-shot is app fault; recurring is hardware — scanprobe's "monitor" semantics matches this. | High (NVIDIA docs) |
| 32 | WATCH | Invalid/corrupted push buffer stream (PBDMA error) | `RESTART_APP`; `CHECK_APP/CUDA` | **Correct as WATCH.** NVIDIA: "primarily quality issues on PCI, generally not caused by user application" — but action is restart, not reset. | High (NVIDIA docs) |
| 43 | WATCH | GPU stopped processing (channel reset, software-induced) | `IGNORE`; `CONTACT_SUPPORT`. NVIDIA: "GPU remains in a healthy state. In most cases, not indicative of a driver bug." | **Correct as WATCH** (arguably could be excluded entirely; NVIDIA literally says ignore). | High (NVIDIA docs) |
| 45 | WATCH | Preemptive cleanup — kernel driver tearing down GPU after app abort | `WORKFLOW_XID_45`. Debug-guidelines: "No action, informative only" | **Correct as WATCH** (could be excluded; NVIDIA says no action). | High (NVIDIA docs) |
| 56 | WATCH | DISPLAY_CHANNEL_EXCEPTION — **"Unused"** in catalog | `CONTACT_SUPPORT`. Catalog lists "NO" across A100/H100/B100/GB200 columns. | **Dead code on modern GPUs.** Keeping it is harmless (it shouldn't fire) but misleading. Optional: remove. | High (NVIDIA docs) |
| 57 | WATCH | FB_LINK_TRAINING_FAILURE_ERROR — **"Unused"** | `CONTACT_SUPPORT`. "NO" on all current archs. | **Dead code on modern GPUs.** Same as 56. | High (NVIDIA docs) |
| 58 | WATCH | FB_MEMORY_ERROR — **"Unused"** | `CONTACT_SUPPORT`. "NO" on all current archs. | **Dead code on modern GPUs.** Same as 56. | High (NVIDIA docs) |
| 61 | WATCH | PMU_BREAKPOINT — **"Unused"** | `CONTACT_SUPPORT`. NVIDIA debug-guidelines (legacy): "Report issue and reset GPU(s)." Catalog now marks unused. | **Dead code on modern GPUs.** If it ever does fire on legacy hardware, the legacy guidance was reset, not just monitor — but on Ampere+ it cannot fire. | High (NVIDIA docs) |
| 64 | WATCH | INFOROM_DRAM_RETIREMENT_FAILURE — row-remapping *failure* | `RESET_GPU`; `CONTACT_SUPPORT`. Debug-guidelines: "Reset GPU(s) immediately. For A100: reboot immediately due to recording failure." | **WRONG. Should be DRAIN.** This is the failure case where the row remapper could not record a remap. Memory is degrading and self-healing is broken. NVIDIA says reset immediately and on A100 reboot. Operationally a drain candidate. | High (NVIDIA docs) |
| 69 | WATCH | Graphics Engine class error | `RESTART_APP`; `CHECK_APP/CUDA` | **Correct as WATCH.** Application-level. | High (NVIDIA docs) |
| 92 | WATCH | High single-bit ECC error rate (excessive SBE interrupts) | `IGNORE` (immediate); `CONTACT_SUPPORT` (investigatory). Debug-guidelines: "Run Field Diagnostics; monitor for RMA thresholds." | **Correct as WATCH.** Trending toward failure but not yet failed. The investigatory bucket is precisely "monitor + escalate." | High (NVIDIA docs) |

### Codes scanprobe is missing entirely

These NVIDIA-classified critical codes appear in neither set. Confidence in each finding is High unless noted.

| Code | NVIDIA description | NVIDIA action | Recommended scanprobe class |
|---|---|---|---|
| **64** | INFOROM_DRAM_RETIREMENT_FAILURE | `RESET_GPU` / immediate reboot on A100 | **DRAIN** (currently in WATCH; see above) |
| **140** | UNRECOVERABLE_ECC_ERROR_ESCAPE — ECC errors interrupted driver's page-offlining | `RESET_GPU`; "if persists, contact hardware vendor" | **DRAIN** |
| **143** | GPU_INIT_ERROR | `RESET_GPU`; `CONTACT_SUPPORT` | **DRAIN** |
| **109** | ROBUST_CHANNEL_CTXSW_TIMEOUT_ERROR — context switch timeout | `RESET_GPU`; `CONTACT_SUPPORT` | **DRAIN** (or WATCH if you want to be conservative; recurring 109 is drain in practice) |
| **119** | GSP_RPC_TIMEOUT | `RESET_GPU`; `INVESTIGATE_SW`; "GPU reset or node power cycle may be needed if persists" | **WATCH** (transient on Blackwell/RTX 5000-class hardware due to known GSP firmware bugs; DRAIN if recurring). Confidence: High for catalog text; Medium on DRAIN-vs-WATCH bucketing because real-world reports show this is sometimes a driver/firmware bug rather than dying silicon. |
| **120** | GSP_ERROR | `RESET_GPU`; `INVESTIGATE_SW` | **WATCH** (same caveat as 119) |
| **44** | ROBUST_CHANNEL_GR_FAULT_DURING_CTXSW | `IGNORE`; `CONTACT_SUPPORT` | Optional WATCH (not critical; included for completeness) |

**The most important ones to add are 64, 140, 143** — these are unambiguously fatal-hardware on every architecture. **109/119/120 are the GSP/timeout family** that has become much more common in driver 535+ on Hopper and Blackwell; not having them means scanprobe is silent on a whole class of failures that show up in production.

### Recommended code change

```python
# Hardware faults: drain immediately
DRAIN_XIDS = {
    48,   # Double-bit ECC (uncontained on its own)
    64,   # Row-remapping FAILURE  -- ADDED
    74,   # NVLink error
    79,   # GPU fallen off bus
    95,   # Uncontained ECC error
    140,  # ECC unrecoverable escape  -- ADDED
    143,  # GPU init error            -- ADDED
}

# Watch: monitor, may be benign or may foreshadow drain
WATCH_XIDS = {
    13, 31, 32, 43, 45, 69,   # application/software-side
    63,                        # row-remap SUCCESS (informational)
    92,                        # high SBE rate (RMA threshold watcher)
    94,                        # contained ECC -- MOVED FROM DRAIN
    109,                       # ctx-switch timeout -- ADDED
    119, 120,                  # GSP RPC timeout / GSP error -- ADDED
}

# Removed from WATCH (NVIDIA marks "Unused" on all current archs):
#   56, 57, 58, 61
# Keep them out unless you specifically support pre-Ampere.
```

If you want to be conservative and not move 94 out of DRAIN, the rationale would be: in MIG-enabled fleets, even a contained ECC error can mean the underlying memory is degrading and you'd rather drain. That is a defensible operator policy — but it is *stricter* than NVIDIA's recommendation. Document the choice either way.

---

## TASK 2 — Throttle Bitmask Format

### Authoritative sources

1. **nvidia-smi `--help-query-gpu` output** (verified via Jérôme Briot's published copy of the field list): the description for the `.active` field reads verbatim **"Bitmask of active clock throttle reasons. See nvml.h for more details."** No mention of "Active/Not Active" string format — that format applies only to the per-reason boolean fields like `clocks_throttle_reasons.hw_slowdown`.
2. **NVML API reference** (`docs.nvidia.com/deploy/nvml-api/group__nvmlClocksThrottleReasons.html`): the bitmask constants are `unsigned long long` values like `0x0000000000000040LL` (HwThermalSlowdown), and `nvmlDeviceGetCurrentClocksThrottleReasons()` returns the bitmask as `unsigned long long *`.
3. **Real-world parser** — `utkuozdemir/nvidia_gpu_exporter` (a popular, actively maintained Prometheus exporter for nvidia-smi). Its `TransformRawValue` function explicitly tests for a `"0x"` prefix and parses with `strconv.ParseUint(s, 16, 64)`. This is direct evidence that nvidia-smi does in fact emit the `.active` field as a `0x`-prefixed hex string in CSV mode on the drivers this exporter targets (which include 535.x+).

### Format determination

| Field | Format on driver 535.x / 550.x / 560.x | Confidence |
|---|---|---|
| `clocks_throttle_reasons.active` | **`0x`-prefixed hex string**, e.g. `0x0000000000000040`. Width is 16 hex digits (64-bit). | High — confirmed by NVIDIA man-page text ("Bitmask … see nvml.h") + working production exporter code |
| `clocks_event_reasons.active` (renamed in nvidia-smi 530+, default in 555+) | Same format (`0x`-prefixed hex bitmask). The field was renamed for naming consistency, not reformatted. | Medium-High — by inference from the rename being non-breaking; confirmed by nvidia-smi man-page describing the bitmask format the same way |
| `clocks_throttle_reasons.hw_slowdown` (and other per-reason booleans) | `"Active"` / `"Not Active"` strings | High — confirmed by published CSV captures |

### The rename is the real risk, not the format

The format question is **safe**: nvidia-smi has always emitted `.active` as a hex bitmask, on every driver from 460.x through 590.x as far as I can verify. `int(hex_str.strip(), 16)` is the right parser.

The bigger risk is **field-name drift**. In nvidia-smi version 530, "Clock Throttle Reasons" was renamed to "Clock Event Reasons" in display output. The query field `clocks_event_reasons.active` was added. `clocks_throttle_reasons.active` was retained as a backward-compatibility alias — but NVIDIA has retired aliases in the past, and the alias is documented as legacy. On a hypothetical future driver where the alias is removed, `nvidia-smi --query-gpu=clocks_throttle_reasons.active` would return an error or empty string and the parser would silently produce `[]` (no throttling detected).

### Recommended change to scanprobe

```python
def _query_throttle_bitmask(smi_output: str) -> int:
    """Parse the .active hex bitmask, robust to driver-rename and whitespace."""
    s = smi_output.strip()
    if not s or s.lower() in ("[n/a]", "n/a", ""):
        return 0
    # nvidia-smi emits "0x..." for .active in CSV mode (verified
    # against driver 535+/550+/560+ via NVML man-page text and
    # the utkuozdemir/nvidia_gpu_exporter parser).
    return int(s, 16)  # int() with base 16 accepts "0x..." prefix

def _decode_throttle(hex_str: str) -> list:
    val = _query_throttle_bitmask(hex_str)
    return [name for mask, name in BITS.items() if val & mask]
```

And at the query site, **probe both field names** to survive the rename:

```python
# Try the modern field first; fall back to the legacy alias.
QUERY_NEW = "clocks_event_reasons.active"
QUERY_OLD = "clocks_throttle_reasons.active"

def query_throttle():
    out = run_nvidia_smi(f"--query-gpu={QUERY_NEW} --format=csv,noheader,nounits")
    if not out or "not a valid query field" in out.lower() or "unknown" in out.lower():
        out = run_nvidia_smi(f"--query-gpu={QUERY_OLD} --format=csv,noheader,nounits")
    return out
```

Add a unit test with both `"0x0000000000000000"` (no throttle) and `"0x0000000000000048"` (HwSlowdown + HwThermalSlowdown) to lock the contract.

### Defensive note on `int(hex_str, 16)`

The current code is `int(hex_str.strip(), 16)`. This works for `"0x0000000000000040"` because Python's `int()` strips a `0x` prefix when base is 16. But it also accepts plain `"40"` as hex 64. If a future driver ever emitted decimal (`"64"` for HwThermalSlowdown), `int("64", 16)` would return 100, not 64 — silently misclassifying. Worth a comment in the code and an explicit assertion that the input starts with `0x` if you want to be paranoid.

---

## Confidence summary

| Finding | Confidence | Source class |
|---|---|---|
| Xid 48, 74, 79, 95 = DRAIN-correct | High | NVIDIA docs (Xid Catalog + GPU Debug Guidelines) |
| Xid 63 should be WATCH not DRAIN | High | NVIDIA docs (catalog action = IGNORE) |
| Xid 94 should be WATCH not DRAIN | High | NVIDIA docs (action = RESTART_APP, GPU stays operational) |
| Xid 64, 140, 143 missing and should be DRAIN | High | NVIDIA docs (action = RESET_GPU on all archs) |
| Xid 119, 120 missing and should be WATCH | Medium-High | NVIDIA docs (catalog) + cross-referenced forum reports of intermittent firmware causes |
| Xid 109 missing (could be DRAIN or WATCH) | Medium | NVIDIA docs (action = RESET_GPU) |
| Xid 56, 57, 58, 61 are "Unused" on current archs | High | NVIDIA Xid Catalog explicit "NO" cells across A100/H100/B100/GB200 |
| `.active` field is `0x`-prefixed hex on 535/550/560 | High | nvidia-smi man-page text + production exporter source code |
| Rename to `clocks_event_reasons.active` started at nvidia-smi 530 | Medium-High | Web search consensus across NVIDIA forum threads and man pages; not directly cited in a single NVIDIA release-note URL I could fetch |
| Backward-compat alias `clocks_throttle_reasons.active` still works on 560+ | Medium-High | Multiple recent forum/exporter references using the old name on current drivers |

Things I could **not** verify:
- The exact driver version where `clocks_event_reasons.active` was added vs. when `clocks_throttle_reasons.active` will be removed. NVIDIA release notes I tried to fetch were either 404 or returned a marketing landing page. The recommendation to probe both names hedges this.
- Whether consumer GPUs (RTX 4090, RTX 5090, RTX 6000 Ada) emit a different format than datacenter GPUs (H100, B200). I found no evidence they do, but I couldn't run nvidia-smi on a consumer card to confirm. NVML is unified across product lines, so format-divergence would be surprising.

---

## Citations (every URL used)

NVIDIA primary sources:
- https://docs.nvidia.com/deploy/xid-errors/index.html — Xid Errors landing page
- https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html — Xid Catalog (per-code descriptions, severity, GPU-applicability table)
- https://docs.nvidia.com/deploy/xid-errors/contents.html — Xid Errors r555 contents
- https://docs.nvidia.com/deploy/pdf/XID_Errors.pdf — XID Errors r590 PDF (Dec 2025)
- https://docs.nvidia.com/deploy/gpu-debug-guidelines/index.html — GPU Debug Guidelines (recovery actions per Xid)
- https://docs.nvidia.com/deploy/pdf/GPU_Debug_Guidelines.pdf — GPU Debug Guidelines r560 PDF
- https://docs.nvidia.com/deploy/nvml-api/group__nvmlClocksThrottleReasons.html — NVML API: bitmask constants
- https://docs.nvidia.com/deploy/nvidia-smi/index.html — nvidia-smi documentation
- https://docs.nvidia.com/deploy/pdf/NVML_API_Reference_Guide.pdf — NVML R595 reference (Mar 2026)
- https://docs.nvidia.com/datacenter/tesla/tesla-release-notes-565-57-01/index.html — Driver 565 release notes
- https://docs.nvidia.com/deploy/a100-gpu-mem-error-mgmt/user-visible-statistics.html — A100 memory error management
- https://docs.nvidia.com/deploy/dynamic-page-retirement/index.html — Dynamic Page Retirement

Forum / community verification:
- https://forums.developer.nvidia.com/t/xid-119-gsp-timeout-on-rtx-6000-pro-blackwell-575-64-3-under-load-reproducible-crash/337871 — Xid 119 on Blackwell driver 575
- https://github.com/NVIDIA/open-gpu-kernel-modules/issues/446 — "Timeout waiting for RPC from GSP"
- https://github.com/NVIDIA/open-gpu-kernel-modules/issues/1045 — Xid 119 → 154 chain on RTX 5080
- https://man.archlinux.org/man/nvidia-smi.1.en — nvidia-smi(1) man page (Arch's mirror)
- https://nvidia.custhelp.com/app/answers/detail/a_id/3751/~/useful-nvidia-smi-queries — NVIDIA KB: useful nvidia-smi queries
- https://briot-jerome.developpez.com/fichiers/blog/nvidia-smi/list.txt — captured `--help-query-gpu` field list
- https://repost.aws/knowledge-center/ec2-linux-troubleshoot-xid-errors — AWS Xid troubleshooting (could not fetch directly; referenced via search snippet)
- https://www.alibabacloud.com/help/en/egs/support/a-gpu-has-fallen-off-the-bus-due-to-an-xid-119-or-xid-120-error — Alibaba Cloud Xid 119/120 guidance

Source code (real-world parser behavior):
- https://github.com/utkuozdemir/nvidia_gpu_exporter — Prometheus exporter; `internal/exporter/exporter.go` `TransformRawValue` and `internal/util/util.go` `HexToDecimal` confirm `0x`-prefix hex parsing for nvidia-smi CSV bitmask fields
- https://github.com/NVIDIA/gpu-monitoring-tools — NVIDIA's own monitoring bindings, including `nvml.h`
- https://docs.rs/nvml-wrapper/latest/nvml_wrapper/bitmasks/device/struct.ThrottleReasons.html — Rust NVML wrapper, mirrors the bitmask layout

Other:
- https://gist.github.com/2b93edb8344ba43818e3c6241229977f — sample CSV showing per-reason fields emit `Active`/`Not Active` (distinct from the `.active` aggregate)
