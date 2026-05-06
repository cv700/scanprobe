# Validation guide

This is the troubleshooting playbook for the five known unknowns in scanprobe.
For each: the authoritative source, the format variants you need to handle,
real-world scenarios that break naive parsers, and what "validated" means.

When real GPU hardware output becomes available, the answers here become test
fixtures. Until then, this document is the next-best thing.

---

## 1. Xid error codes

**Authoritative source**:
[NVIDIA Xid Errors](https://docs.nvidia.com/deploy/xid-errors/index.html) — the
official taxonomy with severity recommendations per code. Cite this URL in
xid.py before classifying any code.

**What to validate**:
- Every code in `DRAIN_XIDS` is documented as critical in NVIDIA's table
- Every code in `WATCH_XIDS` matches NVIDIA's "may indicate" or "monitor" guidance
- Descriptions in `XID_DESCRIPTIONS` match NVIDIA's exact phrasing where possible
- No code is claimed without documentation backing

**Known dmesg format variants** (the regex must handle all of these):

```
# Standard format — most common
[12345.678] NVRM: Xid (PCI:0000:3b:00): 94, pid='<unknown>', name=<unknown>, Ch 00000008

# With .function suffix on PCI address
[12345.678] NVRM: Xid (PCI:0000:3b:00.0): 94, pid='<unknown>'

# Kernel prefix (some distributions)
[12345.678] kernel: NVRM: Xid (PCI:0000:3b:00): 94, Ch 00000008

# syslog/journalctl prefix (rare in dmesg, common in journalctl -k)
May  5 10:23:45 hostname kernel: NVRM: Xid (PCI:0000:3b:00): 94, ...

# Without trailing info (older drivers)
[12345.678] NVRM: Xid (PCI:0000:3b:00): 94

# Different PCI format (older systems)
[12345.678] NVRM: Xid (PCI:01:00): 94, Ch 00000001
```

**Scenarios to add fixtures for**:
- Clean dmesg (no Xid events) — most common case
- Single transient Xid 13 (graphics exception, common, mostly benign)
- Repeated Xid 43 (long compute) — should appear once in events, not many times
- Xid 94 + Xid 79 same boot — node has multiple distinct faults
- Xid from a previous boot session (still present in dmesg ring buffer)
- Container without `/dev/kmsg` access — dmesg returns empty

---

## 2. nvidia-smi ECC field names

**Authoritative source**:
Run `nvidia-smi --help-query-gpu` on a real node and capture the full field list.
Match every field used in `_FIELDS` against the live output. NVIDIA driver
release notes occasionally rename or deprecate fields.

**Current fields used** (validate each):
```
ecc.mode.current
ecc.errors.corrected.volatile.total
ecc.errors.uncorrected.volatile.total
ecc.errors.corrected.aggregate.total
ecc.errors.uncorrected.aggregate.total
```

**ECC scenarios that break naive parsers**:

| Scenario | What `--query-gpu` returns | Code must handle |
|----------|----------------------------|------------------|
| ECC enabled, clean | `Enabled, 0, 0, 0, 0` | Score zero |
| ECC enabled, SBE | `Enabled, 5, 0, 12, 0` | Score watch |
| ECC enabled, DBE volatile | `Enabled, 0, 1, 0, 1` | Score drain |
| ECC disabled (cloud) | `Disabled, [N/A], [N/A], ...` | No score, not an error |
| Consumer GPU | `[Not Supported], [Not Supported], ...` | No score, no warning |
| Driver mismatch | Field returns empty string | Default to 0, mark warning |

**Validation checklist**:
- Run on H100, A100, T4, A10G if available
- Diff the field names across driver versions (535.x vs 550.x vs 560.x)
- Confirm `[Not Supported]` and `[N/A]` and empty string all parse to 0
- Confirm comma-thousands work (some fields return `1,234`)

---

## 3. Clock throttle bitmask

**Authoritative source**:
[NVML Clock Throttle Reasons](https://docs.nvidia.com/deploy/nvml-api/group__nvmlClocksThrottleReasons.html)
documents the bit values. Cite this URL in the `_THROTTLE_BITS` definition.

**Bit values** (validate against NVML documentation):
```
0x0000000000000001  GpuIdle
0x0000000000000002  ApplicationsClocksSetting
0x0000000000000004  SwPowerCap
0x0000000000000008  HwSlowdown
0x0000000000000010  SyncBoost
0x0000000000000020  SwThermalSlowdown
0x0000000000000040  HwThermalSlowdown
0x0000000000000080  HwPowerBrakeSlowdown
0x0000000000000100  DisplayClockSetting
```

**Format variants**:

| Driver behavior | Returned format | Code currently handles? |
|-----------------|-----------------|-------------------------|
| Modern drivers | `0x0000000000000040` (hex) | Yes |
| Some 470.x drivers | `64` (decimal) | **No — needs validation** |
| ECC-disabled query | `[Not Supported]` | Yes |
| Idle GPU | `0x0000000000000001` | Yes |

**Scenarios to test**:
- Idle GPU at boot (only `GpuIdle` set)
- GPU under sustained training load (no flags or only ApplicationsClocksSetting)
- GPU at thermal limit (`HwThermalSlowdown` set, often combined with HwSlowdown)
- Power-capped GPU (`SwPowerCap` set, common on cloud VMs with conservative limits)
- Multiple flags (e.g., `0x60` = HwSlowdown + SwThermalSlowdown)

**The validation that matters most**: confirm whether modern drivers
(550.x, 560.x) return decimal or hex. The current code assumes hex.
If decimal, the parser silently returns empty list — which means
**thermal throttling would go undetected**. This is a credibility-killer bug
if it's wrong.

---

## 4. dmesg access on cloud nodes

**Sources**:
- `man dmesg` — flag documentation
- Linux kernel docs on `/dev/kmsg` permissions
- `dmesg.conf` and `kernel.dmesg_restrict` sysctl

**Access patterns by environment**:

| Environment | Default dmesg access | What works |
|-------------|---------------------|------------|
| Bare metal Linux | Full access | `dmesg` |
| Most cloud VMs (RunPod, Lambda) | Full access as root | `dmesg` |
| Privileged containers | Full access | `dmesg` |
| Unprivileged containers | Often restricted | `dmesg` may fail |
| Kubernetes pods (CoreWeave) | Depends on securityContext | `dmesg` may fail |
| `kernel.dmesg_restrict=1` | Root only | Need sudo |
| Ubuntu 20.04+ default | Often restricted | Need sudo |

**dmesg flag compatibility**:

```bash
# Modern kernels (3.5+) — works
dmesg --level=err,warn,crit,alert,emerg

# Older kernels — flag not supported, falls through to plain dmesg
dmesg

# Some systems — needs explicit kernel-only filter
dmesg -k
```

**Scenarios to handle**:
- dmesg available, full output
- dmesg available, but ring buffer cleared since last NVIDIA event
- dmesg returns "Operation not permitted" → mark unavailable, do not crash
- dmesg returns empty → not a fault, just no events
- `--level` flag not supported → fall through to plain dmesg (current behavior)
- journalctl available but dmesg restricted → consider `journalctl -k --since boot` as fallback

**Hint message Codex should preserve**:
When dmesg fails with permission error, the user-facing message must say
`try: sudo scanprobe`. Don't just say "dmesg failed".

---

## 5. DCGM diagnostics

**Authoritative source**:
[DCGM Diagnostics User Guide](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/dcgm-diagnostics.html)

**Current invocation**: `dcgmi diag -r 1`

**Things to validate**:
- Is `dcgmi diag -r 1` still the correct syntax in DCGM 3.x and 4.x?
- Does `-r 1` mean "level 1 quick test" or something else?
- Output format: text or JSON? The current parser is minimal.
- DCGM version matrix:
  - DCGM 2.x: older syntax, may not have `-r 1`
  - DCGM 3.x: current target
  - DCGM 4.x: newer, may have changes
- Does DCGM auto-detect GPUs, or does it need group ID?
  (Earlier code tried `-g {gpu_index}` which was wrong — `-g` is group ID)

**DCGM availability by environment**:

| Environment | DCGM installed by default? |
|-------------|----------------------------|
| NVIDIA NGC containers | Yes |
| Cloud GPU images (most) | Sometimes |
| Bare metal post-driver-install | No, separate package |
| RunPod PyTorch template | Sometimes |

The graceful degradation is critical: if DCGM is not installed, the tool
should silently note "DCGM not available" and not affect the score.

---

## How to validate, in order

When you get access to a real GPU node, run these commands in this order
and capture all output:

```bash
# 1. Basic info — confirms which fields nvidia-smi supports on this driver
nvidia-smi --version
nvidia-smi --help-query-gpu | head -100

# 2. The full query as scanprobe does it — confirms field names parse
nvidia-smi --query-gpu=index,name,uuid,ecc.mode.current,\
ecc.errors.corrected.volatile.total,ecc.errors.uncorrected.volatile.total,\
ecc.errors.corrected.aggregate.total,ecc.errors.uncorrected.aggregate.total,\
temperature.gpu,temperature.memory,power.draw,power.limit,\
clocks.current.sm,clocks.current.memory,clocks_throttle_reasons.active,\
memory.used,memory.total,pcie.link.gen.current,pcie.link.width.current \
--format=csv,noheader,nounits

# 3. Throttle bitmask format — confirm hex vs decimal
nvidia-smi --query-gpu=clocks_throttle_reasons.active --format=csv

# 4. Xid scan — confirm dmesg format
dmesg | grep -i "NVRM\|Xid" | head -20
sudo dmesg | grep -i "NVRM\|Xid" | head -20

# 5. DCGM availability and syntax
which dcgmi
dcgmi --version
dcgmi diag -r 1 2>&1 | head -50

# 6. The actual scanprobe run — JSON for paste-back
python3 scanprobe.py --json
```

Paste this output into a GitHub issue or PR. It becomes test fixtures.

---

## Stress scenarios to add as test fixtures

These are scenarios that have a good chance of breaking naive parsers.
Codex should add fixtures for each as real output becomes available.

**Multi-tenant / virtualized**:
- MIG-partitioned H100 (one GPU appears as multiple smaller GPUs)
- vGPU (NVIDIA virtual GPU under hypervisor)
- AWS p4de.24xlarge (NVLink topology)

**Stressed hardware**:
- GPU at thermal limit (real `HwThermalSlowdown` flags)
- GPU with row remapping in progress (Xid 63 with surrounding context)
- GPU after a previous training job left it warm (high temperature, throttle active)

**Edge cases that have bitten others**:
- nvidia-smi output truncated mid-line (rare but happens on driver crash)
- nvidia-smi reports GPU but PCIe link width is 1 (degraded link, rare)
- ECC fields show numerical value but nvml reports ECC disabled
- Two GPUs with identical UUID prefix (truncation issue)

**Cloud-specific**:
- RunPod fresh node — should be clean
- RunPod node that's been running for weeks — likely has some Xid 13s
- Spot instance reclaimed and reissued — dmesg may show prior tenant's faults

---

## What "validated" means

A check is **validated** when:

1. The field names, regex, or invocation has been confirmed against authoritative
   NVIDIA documentation (URL cited in the code as a comment)
2. Real output from at least one real GPU has been parsed correctly
3. Test fixtures using that real output exist in the test suite
4. The "Hardware tested" table in README.md lists the hardware

Until all four are true, the check is **unvalidated** and should be marked
`# UNVALIDATED:` in the code with a specific note on what's missing.
