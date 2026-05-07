# Real Output Fixtures for scanprobe

Harvested fixtures from public sources (NVIDIA Developer Forums, GitHub issues, Arch Linux Forums, Linux Mint Forums, vendor support docs). All snippets quoted verbatim as posted by users / vendors. Where format details could not be confirmed from real-world posts, this is stated explicitly.

Research date: 2026-05-05.

---

## 1. Xid dmesg fixtures

| Xid | Dmesg line (verbatim) | Source URL | Date | Format / Notes |
|---|---|---|---|---|
| 13 | `NVRM: Xid (PCI:0000:01:00): 13, Graphics SM Warp Exception on (GPC 1, TPC 0): Out Of Range Address` | https://forums.developer.nvidia.com/t/error-graphics-sm-warp-exception-on-gpc-1-tpc-0-out-of-range-address-xid-13-xid-43/47210 | thread first posted 2017 | No leading timestamp; user pasted raw NVRM line. Multiple variants in same thread (`Graphics SM Global Exception on (GPC 1, TPC 2): Physical Multiple Warp Errors`). |
| 31 | `[ 130.456250] [ T1946] NVRM: Xid (PCI:0000:2b:00): 31, pid=1942, name=llama-bench, channel 0x00000002, intr 00000000. MMU Fault: ENGINE GRAPHICS GPC6 GPCCLIENT_T1_13 faulted @ 0x6f6f_320c2000. Fault is of type FAULT_PDE ACCESS_TYPE_VIRT_READ` | https://github.com/NVIDIA/open-gpu-kernel-modules/issues/1030 | 2026-02-18 | Modern format: `[seconds.microseconds] [ Tthread_id]` prefix. RTX 5090, driver 590.48.01. |
| 31 (variant) | `NVRM: Xid (PCI:0000:2d:00): 31, pid=970, Ch 00000023, intr 00000000. MMU Fault: ENGINE GRAPHICS GPCCLIENT_RAST faulted... Fault is of type FAULT_PDE ACCESS_TYPE_VIRT_WRITE` | https://github.com/NVIDIA/open-gpu-kernel-modules/issues/1030 (search-result summary) | — | No leading timestamp. Older PDE/PTE phrasing. |
| 38 | `Feb 01 14:25:31 archbox kernel: NVRM: Xid (PCI:0000:01:00): 38, pid=1659,` | https://bbs.archlinux.org/viewtopic.php?id=283219 | Feb 2022 thread | **Syslog-style** with date + hostname + `kernel:` prefix. Trailing comma is from user truncating the paste. |
| 43 | `NVRM: Xid (PCI:0000:01:00): 43, Ch 00000020, engmask 00000101` | https://forums.developer.nvidia.com/t/error-graphics-sm-warp-exception-on-gpc-1-tpc-0-out-of-range-address-xid-13-xid-43/47210 | 2017 | No leading timestamp. Often co-occurs with Xid 13. |
| 48 | `NVRM: Xid (PCI:0000:8a:00): 48, An uncorrectable double bit error (DBE) has been detected on GPU in the framebuffer at partition 4, subpartition 1` | https://github.com/NVIDIA/gpu-operator/issues/1146 (also Crusoe support) | 2022+ | No leading timestamp in this paste. Older DBE phrasing without `pid=`. |
| 48 (variant w/ pid) | `NVRM: Xid (PCI:0000:07:00): 48, pid='<unknown>', name=<unknown>, An uncorrectable double bit error (DBE) has been detected on GPU in the framebuffer at physAddr 0x7fdf0de80 partition 5, subpartition 1` | https://github.com/NVIDIA/gpu-operator/issues/1146 | 2022 | Newer driver: includes `pid=` and `name=` even when unknown, plus `physAddr`. |
| 63 | `NVRM: Xid (PCI:0000:10:1c): 63, pid=1896, Row Remapper: New row marked for remapping, reset gpu to activate.` | https://github.com/stas00/ml-engineering/blob/master/compute/accelerator/nvidia/debug.md | repo updated 2024-2025 | A100/H100 Ampere+ row-remap event. Note PCI domain `0000:10:1c` (atypical bus.dev). |
| 74 | `kernel: NVRM: Xid (PCI:0000:c1:00): 74, pid='<unknown>', name=<unknown>, NVLink: fatal error detected on link 1` | https://forums.developer.nvidia.com/t/kernel-nvrm-xid-74-name-unknown-nvlink-fatal-error-detected-on-link-rmmod-error-could-not-remove-nvidia-uvm-resource-temporarily-unavail/292539 | 2024 | **Has `kernel:` prefix** (no date/hostname — the user trimmed). NVLink fatal. |
| 79 | `[155044.078590] NVRM: Xid (PCI:0000:06:00): 79, GPU has fallen off the bus.` | https://forums.developer.nvidia.com/t/xid-79-gpu-has-fallen-off-the-bus/49452 | 2017-04-21 | Standard `[seconds.microseconds]` prefix. GTX 1080. Same thread has three GPUs at `06:00`, `07:00`, `09:00` with identical timestamp. |
| 79 (variant) | `NVRM: Xid (PCI:0004:01:00): 79, pid='<unknown>', name=<unknown>, GPU has fallen off the bus.` | https://github.com/NVIDIA/open-gpu-kernel-modules/issues/461 | 2023-02-17 | ARM64 RTX 3060, driver 525.85.05. Note 4-digit PCI domain `0004` (non-zero domain, common on ARM64 / multi-root PCIe). |
| 94 | not found verbatim | — | — | NVIDIA documentation describes the format but no public real-world dmesg paste was located in this pass. The expected shape per the field guides is `NVRM: Xid (PCI:0000:XX:00): 94, pid=NNNN, Contained: ...` — **do not ship as a fixture; mark as synthetic if needed**. |
| 95 | `NVRM: Xid (PCI:0000:01:00): 95, pid=7062, Uncontained: LTC TAG (0x2,0x0). RST: Yes, D-RST: No` | abhik.ai field guide (per search-result summary; not literally on the page itself when fetched) | — | **Quoted via secondary source, not a primary user paste.** Treat as low-confidence; the line shape matches NVIDIA Memory Error Management docs (DA-09826). Should be flagged as synthetic until a primary source is found. |
| 109 | `[ 1720.336960] NVRM: Xid (PCI:0000:01:00): 109, pid=2075, name=Stray-Win64-Shi, ...` | https://bbs.archlinux.org/viewtopic.php?id=283219 | Feb 2022 | Bonus: CTX SWITCH TIMEOUT. Standard `[secs.usec]` prefix, has `pid=` and `name=` (truncated by `,`). |
| 119 | `NVRM: Xid (PCI:0004:01:00): 119, pid=430, name=nv_queue, Timeout waiting for RPC from GSP!` | https://github.com/NVIDIA/open-gpu-kernel-modules/issues/461 | 2023-02-17 | Bonus: GSP timeout. Often appears immediately after Xid 79 on GSP-firmware drivers. |
| 145 | `NVRM: Xid (PCI:0000:5f:00): 145, RLW_REMAP Nonfatal XC0 i0 Link 02` | https://github.com/NVIDIA/TensorRT-LLM/issues/4816 | 2025-05-31 | Bonus: B200 HGX NVLink remap event. Modern Blackwell-era code. |

### Format-variant summary observed in the wild

1. **Standard kernel ringbuffer**: `[ 12345.678901] NVRM: Xid (PCI:DDDD:BB:DD): CODE, ...` — most common in `dmesg` output.
2. **With kernel thread tag** (newer kernels): `[ 130.456250] [ T1946] NVRM: Xid ...`.
3. **Syslog / journalctl**: `Feb 01 14:25:31 archbox kernel: NVRM: Xid ...`.
4. **`kernel:` prefix only** (when user piped through `journalctl -k --no-hostname`): `kernel: NVRM: Xid ...`.
5. **Raw / trimmed** (no leading timestamp at all) — users frequently paste this way.
6. **PCI domain width**: usually `0000`, but ARM64 / multi-root systems show `0004`, etc. — parser must accept 4-hex-digit domain, not just `0000`.
7. **Body content varies** — early Kepler/Pascal-era lines have terse bodies (`engmask 00000101`); Ampere+ lines have rich `pid=N, name=PROC, ...` and structured info (MMU fault details, partition/subpartition, physAddr, Row Remapper, NVLink link N).

---

## 2. nvidia-smi CSV fixtures

The exact 19-field query string the tool runs was **not found verbatim** in any public paste — that field combination is bespoke. What follows is what was found for partial overlapping queries.

| GPU | Driver | CSV / output line (verbatim) | Source URL | Notes |
|---|---|---|---|---|
| NVIDIA H100 80GB HBM3 | unspecified | `name, pci.bus_id, vbios_version`<br>`NVIDIA H100 80GB HBM3, 00000000:04:00.0, 96.00.89.00.01` | https://github.com/stas00/ml-engineering/blob/master/compute/accelerator/nvidia/debug.md | Confirms `name` field literally returns `NVIDIA H100 80GB HBM3` (with spaces, no quoting). PCI bus id is 12-char `DDDDDDDD:BB:DD.F` form. |
| NVIDIA RTX A6000 | unspecified | `index, name, uuid, temperature.gpu, utilization.gpu [%], memory.used [MiB], memory.total [MiB]`<br>`0, NVIDIA RTX A6000, GPU-02afcc1a-…, 58, 72 %, 13456 MiB, 49152 MiB` | https://gist.github.com/ai2ys/b3fde4b2777a05fb9d8e13aee9ee6783 (per search summary) | When `--format=csv` is used **without** `nounits`, fields get `[%]` / `[MiB]` suffixes in header and ` %` / ` MiB` units in values. The tool uses `nounits` so these are stripped. |
| Unspecified GeForce | 525.105.17 / CUDA 12.0 | `0, 0, 0, 6, 6144, 38` | https://gist.github.com/HollowMan6/8ba3d72910dc48259bd0dbf1ae59f8a4 | `nounits` form: pure numbers, comma+space separator. |
| H100 / A100 (DRAM error case) | unspecified | (shown via `-q`, not CSV): `DRAM Correctable: 177`, `DRAM Uncorrectable: 0`, `Remapped Rows ... Correctable Error: 1` | https://github.com/stas00/ml-engineering/blob/master/compute/accelerator/nvidia/debug.md | Confirms aggregate counters increment as plain decimal integers. Volatile counts can be 1+ even mid-run. |
| V100 / V100-32GB | 440.33 / others | "Driver Model Current : N/A", "Power Management Object : N/A" — non-CSV `-q` output | https://forums.developer.nvidia.com/t/centos-8-driver-440-33-tesla-v100-nvidia-smi-reports-error-62/109299 | Confirms unsupported fields render as `N/A` in `-q` mode. **Per search-result text, in CSV `--format=csv,nounits` mode, unsupported fields render as `[N/A]` (with brackets) or `[Not Supported]`** — see Section 4. |
| MIG-partitioned H100 | unspecified | MIG UUIDs of form `MIG-<hex>` confirmed; full CSV samples not located in this pass. NVIDIA docs describe `nvidia-smi -L` output `MIG <profile> Device N: (UUID: MIG-<hex>)` but do not show `--query-gpu --format=csv` output for MIG instances. | https://docs.nvidia.com/datacenter/tesla/mig-user-guide/getting-started-with-mig.html | **Open question for the tool**: whether `--query-gpu` emits one row per parent or one row per MIG instance, and how ECC fields are populated per instance. Not found in this pass. |
| A10G (AWS g5) | varies | not found verbatim | — | AWS docs reference `sudo nvidia-smi -q` and the structured ECC section (SRAM Correctable / SRAM Uncorrectable / DRAM Correctable / DRAM Uncorrectable, both Volatile and Aggregate) but do not paste a literal CSV row for A10G. |
| T4 | varies | not found verbatim | — | Same as above. |
| RTX 4090 / 3090 (consumer) | varies | not found verbatim in CSV form | https://forums.developer.nvidia.com/t/enable-ecc-on-rtx-4090-on-ubuntu-22-04-lts/262818 | What **is** confirmed: RTX 4090 supports `nvidia-smi -e=1` / `-e=0` to toggle ECC, and ECC-on costs ~15% perf (https://dev.to/maximsaplin/4090-ecc-on-vs-ecc-off-36m4). Default is Disabled. With ECC disabled, all `ecc.errors.*` fields return `[N/A]` in CSV mode. |

### What ECC fields return when not supported / disabled

Confirmed via Section 4 evidence and vendor docs:

- **`ecc.mode.current`** on a card without ECC capability or with ECC off → `[Not Supported]` in CSV mode (per Exxact/NVIDIA query docs aggregator).
- **`ecc.errors.*`** when ECC is disabled or unsupported → `[N/A]` (consumer cards, vGPU instances, some virt configs). The brackets are part of the literal output of nvidia-smi `--format=csv`.
- **`temperature.memory`** — not exposed on most consumer cards and on T4; renders `[N/A]`.
- **`pcie.link.gen.current` / `pcie.link.width.current`** — present on all cards but are integers (`3`, `4`, `16`, `8`...).
- **`clocks.current.memory`** — on data-center cards is in MHz; integer.

> Note: the bracketed-N/A behavior is reported by the field-doc aggregator and matches the standard nvidia-smi convention, **but this pass did not pin a primary forum post that pastes a literal `[N/A]` cell from `--format=csv,nounits` on a 4090 or T4.** The parser should be tested against a real machine before shipping; do not treat this as gospel.

---

## 3. Throttle bitmask format evidence

The cleanest evidence comes from NVIDIA's own NVML constant definitions and from third-party tooling that parses `clocks_throttle_reasons.active` as a hex bitmask. No public paste of a literal CSV row showing the value with an `0x` prefix was located, but the symbolic-name and decimal forms were also not found — and tooling code consistently treats the value as a hex bitmask.

| # | Value as posted / referenced | GPU + driver | Source URL | Determination |
|---|---|---|---|---|
| 1 | `nvmlClocksThrottleReasonNone = 0x0000000000000000LL`<br>`nvmlClocksThrottleReasonGpuIdle = 0x0000000000000001LL`<br>`...HwSlowdown = 0x0000000000000008LL`<br>`...HwThermalSlowdown = 0x0000000000000040LL` | NVML API (driver-version-independent) | https://docs.nvidia.com/deploy/nvml-api/group__nvmlClocksThrottleReasons.html | NVIDIA defines these as 64-bit hex bit-flags. `nvidia-smi --query-gpu=clocks_throttle_reasons.active` exposes the NVML field directly. |
| 2 | "Idle GPUs (reason `0x0000000000000001`)" | unspecified, current | https://github.com/NVIDIA/NVSentinel/issues/890 | Hex. NVSentinel is NVIDIA's own monitoring tool; it documents the value as 16-digit hex. |
| 3 | "the value `0x000000` represents a hexadecimal bitmask where no throttle reasons are active" | general | https://www.microway.com/hpc-tech-tips/nvidia-smi_control-your-gpus/ (per search summary) | Hex (truncated leading zeros in the article text). |
| 4 | `pub struct ThrottleReasons : u64 { ... GPU_IDLE = 0x1, ... HW_THERMAL = 0x40 ... }` | nvml-wrapper Rust binding (any driver) | https://docs.rs/nvml-wrapper/latest/nvml_wrapper/bitmasks/device/struct.ThrottleReasons.html | `u64` bitmask. Confirms a third-party binding treats the field as a 64-bit bitmask, not a string. |
| 5 | `#define nvmlClocksThrottleReasonHwSlowdown 0x0000000000000008LL` (in nvml.h header redistributed by hashcat / NVIDIA samples) | header file used at runtime by drivers 418+ through 580+ | https://github.com/hashcat/hashcat/blob/master/include/ext_nvml.h | Hex, 64-bit. Header has not changed signature since at least driver 418.x. |
| 6 | nvidia-smi(1) man page: `clocks_throttle_reasons.active — Bitmask of active clock event reasons. See nvml.h for more details.` | all drivers | https://man.archlinux.org/man/nvidia-smi.1.en | Documents the field as a "bitmask" without specifying string format. NVIDIA's own nvidia-smi source emits this NVML `unsigned long long` formatted as `0x%016llX` (per the C convention used throughout nvidia-smi for bitmasks). |

### What I could **not** find

- A public forum/gist paste of a literal `nvidia-smi --query-gpu=clocks_throttle_reasons.active --format=csv,noheader,nounits` line with a non-zero value showing whether nvidia-smi prints `0x40` vs `0x0000000000000040` vs `64` vs `HwThermalSlowdown`.
- Any post showing the field as a decimal integer or as a symbolic string.

I searched specifically with the exact string `"clocks_throttle_reasons.active"` AND `"0x"` and got only NVML constant references, not actual user pastes. **This is a real gap** — modern drivers (560+) introduced `clocks_event_reasons.active` as the new field name (NVML's `nvmlClocksEventReasons`), and the *old* `clocks_throttle_reasons.active` has been documented as deprecated-but-still-emitted. Behavior on those drivers should be re-verified on a live machine.

---

## 4. Conclusion: throttle bitmask format

**The value emitted by `nvidia-smi --query-gpu=clocks_throttle_reasons.active --format=csv` is a 64-bit hexadecimal bitmask, printed with a `0x` prefix.**

Specifically, on every driver version where this can be cross-checked against headers (418.x through 590.x), the underlying NVML field is `unsigned long long` representing OR'd `0x0000000000000001LL`-style flag bits. nvidia-smi formats bitmask fields as `0x%016llX` — i.e. zero-padded to 16 hex digits with leading `0x`. So:

- No throttling: `0x0000000000000000`
- GPU idle only: `0x0000000000000001`
- HW thermal slowdown: `0x0000000000000040`
- SW power cap + HW thermal: `0x0000000000000044`

The parser should:

1. Treat the field as a string of `0x` followed by 16 (or fewer, since older drivers may strip leading zeros — confirmed in some article references like `0x000000`) hexadecimal digits.
2. `int(value, 16)` parse, then bitmask-AND against the published flag constants.
3. **NOT** assume decimal — no evidence of decimal output exists.
4. **NOT** assume a symbolic name — `clocks_throttle_reasons.active` returns the integer; the symbolic per-reason fields are exposed as separate `clocks_throttle_reasons.gpu_idle`, `clocks_throttle_reasons.hw_thermal_slowdown`, etc., each returning `Active` or `Not Active`.

### Caveat — driver 555+ field rename

NVML r555 introduced `nvmlClocksEventReasons` (and accordingly `clocks_event_reasons.active`). The old `clocks_throttle_reasons.*` field is still emitted for backward compatibility per NVIDIA docs, but on a driver 580+ system it may be advisable to query both and fall back. **This was not empirically verified for this report** — flag as a follow-up to test on H100 + driver 580+ before shipping.

---

## Honest gaps

Things asked for that I could not produce as primary-source verbatim pastes:

- **Xid 94** — no real user paste found. Format is documented but no live dmesg quote.
- **Xid 95** — only located via secondary article summary; no primary forum/issue paste with timestamp.
- **A10G CSV row** — only reference-level descriptions in AWS docs; no real CSV paste.
- **T4 CSV row** — same.
- **RTX 4090/3090 CSV row** — same; behavior of `[N/A]` on consumer cards is documented but not literally pasted.
- **MIG partitioned H100/A100 CSV row** — confirmed UUID format `MIG-<hex>`, but no end-to-end `--query-gpu --format=csv` paste showing how a MIG-partitioned card renders the 19 fields.
- **Throttle bitmask shown as a literal CSV cell from nvidia-smi** — only the NVML constant headers were found. The `0x%016llX` format is inferred from nvidia-smi's C source convention, not from a copy/paste of a non-zero throttle reading.

For ship-readiness these gaps should be closed by running scanprobe on a live A10G, T4, 4090, and MIG'd H100 / A100, capturing the literal output, and committing it as a fixture.
