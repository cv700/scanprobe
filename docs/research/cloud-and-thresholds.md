# Cloud Provider Quirks, Container dmesg, GPU Thresholds, MIG/vGPU

**Date:** 2026-05-05
**Scope:** Inputs needed to make scanprobe robust across the real fleet of GPU cloud providers, to handle containers that restrict kernel-log access, and to use per-model thermal thresholds rather than a single H100-centric set.

---

## TL;DR

- **Container reality is the dominant constraint.** RunPod (Community/Secure), Vast.ai, Lambda 1-Click and CoreWeave all run user code inside Docker/Kubernetes containers where `dmesg`/`/dev/kmsg` is host-restricted by default. Lambda's bare on-demand VM, AWS DLAMI, and GCP A2/A3 with the default OS image are the easy environments — the user IS root on a VM.
- **scanprobe should always probe `/dev/kmsg` AND `dmesg` AND fall back to "xid check unavailable, see DCGM" rather than emit a misleading clean result.** This is a real silent-failure hazard in the existing code path.
- **The 88°C threshold is appropriate for H100/A100 datacenter SKUs but wrong for T4 (slowdown ~92°C), V100 (slowdown 87°C), L40S (~90°C), RTX 4090 (~84°C throttle, hotspot), and not the right *direction* of conservatism for some of them.** scanprobe should query `temperature.gpu.tlimit` (driver 535+) or read `nvidia-smi -q -d TEMPERATURE` GPU Slowdown / Shutdown / Max Operating fields and compute thresholds *relative to those*, with a per-model fallback table when the field is unavailable.
- **MIG instances do appear as discrete entries via `nvidia-smi -L` and via `--query-gpu` if you pass the right ID format.** ECC is reported at the *parent GPU* level, not per MIG instance. scanprobe needs to detect MIG mode and either skip ECC checks per-instance or aggregate at parent.
- **vGPU is detectable via `nvidia-smi -q | grep "GPU Virtualization Mode"` and via the existence of `nvidia-smi vgpu` output.** Inside a vGPU guest, ECC counters are typically zero or absent, and Xid signals do NOT propagate from host to guest. scanprobe should mark vGPU-detected GPUs with reduced confidence and surface that explicitly in the report.

---

## TASK 1 — Cloud Provider Audit

Confidence legend: **H** = stated explicitly in primary docs; **M** = inferred from multiple secondary sources; **L** = best-guess / single-source.

| Provider | Default env | dmesg w/o sudo | /dev/kmsg | DCGM pre-installed | Driver (mid-2025/2026) | ECC default | nvidia-smi quirks | Known gotchas | Conf. |
|---|---|---|---|---|---|---|---|---|---|
| **RunPod Community** | Docker container on peer-host (multi-tenant, vetted hosts) | **No** — host owns dmesg, container is unprivileged | **No** by default; not bind-mounted | **No** — user must install in container | Whatever host has; varies (commonly 535–550) | On (host-controlled) | Container often misses `--cap-add SYS_ADMIN`; DCGM profiling needs that | Host driver/runtime mismatch with container CUDA; nvidia-smi can return "Failed to initialize NVML: Unknown Error" hours into a session (cgroup device migration bug); host-side throttling invisible to container | M |
| **RunPod Secure Cloud** | Docker container on dedicated single-tenant host | Same as Community (still container) | Same | Same | Tier-1 DCs run more uniform stacks (typically ≥550) | On | Same | Same as community but without noisy-neighbor variance | M |
| **Lambda Labs On-Demand** | **Bare VM** (Ubuntu Server / Lambda Stack image), user is sudo | **Yes** (you're root on the VM) | **Yes** | **No** by default — Lambda Stack ships drivers + CUDA + NCCL + Docker, **not DCGM** | 535–565 range, image-dependent | On | Standard | DLAMI-style "open" driver vs proprietary mismatch is rare here; cuDNN/NCCL versions can lag | H |
| **Lambda 1-Click Cluster** | Mostly bare VM, occasionally K8s if user opts in | Yes for VMs; container rules apply for K8s | Yes/depends | No | Same as on-demand | On | Standard | Same as on-demand | M |
| **CoreWeave** | **Kubernetes pods** (CKS or SUNK) — GPU Operator manages drivers | **No** — pod is unprivileged by default; needs `securityContext.privileged: true` or `CAP_SYSLOG` | **No** unless explicitly bind-mounted via `volumeMounts: /dev/kmsg` | DCGM exporter typically deployed cluster-wide as DaemonSet, **not in user pod** | GPU Operator pins via label `gpu.coreweave.cloud/driver-version`; commonly 550/560 | On | MIG profile may be pre-set on certain H100 SKUs; check `nvidia-smi -L` for instance IDs first | dcgm-exporter metrics reachable cluster-side but NOT from user pod unless service-mesh; host kernel logs only via Node Problem Detector | H |
| **AWS p4d.24xlarge (A100 40GB)** | EC2 instance, DLAMI default | **Yes** (root on VM) | **Yes** | **Yes** if DLAMI; install via `nvidia-dcgm` package on Ubuntu | DLAMI ALinux2023: 570.172 + DCGM 4.x; older AMIs ship 535/550 | On | Standard | DLAMI uses **OpenRM** open-source driver; some legacy field names differ. Image age varies wildly | H |
| **AWS p5.48xlarge (H100)** | Same | Yes | Yes | Yes (DLAMI) | 570+ recommended; 580.126 in newest DLAMI | On | Standard; sometimes EFA peer mode advertised | Customer-launched custom AMIs may run older OSS drivers; verify | H |
| **AWS g5.xlarge (A10G)** | Same | Yes | Yes | DLAMI: yes; community AMI: typically no | 535+ | On | A10G has slightly different temp behavior than data-center A10 (it's the Cloud variant); slowdown closer to 90°C | A10G has fewer ECC bits than A100; some `nvidia-smi -q` ECC sub-fields return N/A | H |
| **GCP A3 (H100 80GB)** | GCE VM (Container-Optimized OS, Ubuntu, or DLVM) | Yes (VM) — pods on GKE follow K8s rules | Yes (VM); pod-dependent on GKE | No on raw VM; yes via NVIDIA GPU Operator on GKE | 525.125 (early A3) to 555+ now; users frequently stuck on 525 if they didn't update | On | Standard | The GCE driver-installer DaemonSet sometimes lags driver upgrades by months | M |
| **GCP A2 (A100 40/80GB)** | GCE VM | Yes | Yes | No on raw VM | 470/525-era is still common | On | Standard | A2 instances created from old templates often run 470.x with deprecated `clocks_throttle_reasons.active` field name | M |
| **Vast.ai** | **Unprivileged Docker container** on consumer/prosumer hardware (RTX 4090, 3090, A6000, occasional H100) | **No** | **No** | No | Whatever host has; **wide variance** (500–565) | Often **Off** (consumer cards default to ECC off; some don't expose it at all) | Many SKUs are GeForce, not datacenter — `ecc.mode.current` may return "[Not Supported]"; `pcie.link.width` may be at x4/x8 because of mining-rig risers | Hosts can override fan/power curves; thermal stability NOT guaranteed; some hosts run vGPU underneath; `nvidia-smi --query-gpu=ecc.mode.current` returns N/A → scoring should not penalize | H |
| **Together AI** | Bare-metal H100/H200 clusters with InfiniBand; sometimes K8s overlay | Bare → yes; K8s pod → no | Same | Cluster-wide DCGM exporter typical | Pinned 550/555+ for H100, 570 for H200/B200 | On | Standard datacenter behavior | InfiniBand HCAs add NIC-side errors that don't appear in nvidia-smi; mostly hidden from scanprobe | M |
| **Crusoe** | Bare metal or Crusoe Managed Kubernetes (CMK) | Bare → yes; CMK pod → no | Same | CMK has telemetry stack incl. DCGM; bare metal varies | 550–560 | On | Standard | Sustainable-energy datacenters: ambient temps occasionally higher (warmer inlet than typical), so GPUs run a few °C hotter at idle | L |
| **Voltage Park** | Bare-metal HGX H100 with InfiniBand, self-serve | Bare → yes | Yes | Not pre-installed; user adds | 550+ | On | Standard | 8-1016 GPU spinups in 15min: drivers/kernel uniform within a cluster but vary between clusters | M |

### Sources

- RunPod docs and articles, [`docs.runpod.io/pods/overview`](https://docs.runpod.io/pods/overview), [`runpod.io/articles/guides/run-openchat-docker-cloud-gpu`](https://www.runpod.io/articles/guides/run-openchat-docker-cloud-gpu), AnswerOverflow Secure-vs-Community thread.
- Lambda docs, [`docs.lambda.ai/public-cloud/on-demand/`](https://docs.lambda.ai/public-cloud/on-demand/), [Lambda Stack page](https://lambda.ai/lambda-stack-deep-learning-software).
- CoreWeave: [`docs.coreweave.com/products/sunk/gpu-driver-management/target-driver-versions`](https://docs.coreweave.com/products/sunk/gpu-driver-management/target-driver-versions), [`docs.coreweave.com/docs/products/cks/nodes/gpu-driver-management/update-gpu-driver`](https://docs.coreweave.com/docs/products/cks/nodes/gpu-driver-management/update-gpu-driver).
- AWS DLAMI changes: [`docs.aws.amazon.com/dlami/latest/devguide/important-changes.html`](https://docs.aws.amazon.com/dlami/latest/devguide/important-changes.html), [Base GPU AMI Ubuntu 20.04](https://docs.aws.amazon.com/dlami/latest/devguide/aws-deep-learning-base-gpu-ami-ubuntu-20.04.html).
- GCP A3/A2: [`docs.cloud.google.com/compute/docs/gpus/create-gpu-vm-accelerator-optimized`](https://docs.cloud.google.com/compute/docs/gpus/create-gpu-vm-accelerator-optimized), [Introducing A3 with H100](https://cloud.google.com/blog/products/compute/introducing-a3-supercomputers-with-nvidia-h100-gpus).
- Vast.ai: [Security FAQ](https://docs.vast.ai/documentation/reference/faq/security), [`vast-ai/base-image` repo](https://github.com/vast-ai/base-image).
- Together AI: [`together.ai/gpu-clusters`](https://www.together.ai/gpu-clusters).
- Crusoe MIG support: [`support.crusoecloud.com/hc/en-us/articles/32663887706651`](https://support.crusoecloud.com/hc/en-us/articles/32663887706651-How-to-use-Nvidia-MIG-to-create-GPU-Instances).
- Voltage Park: [`voltagepark.com/product/cloud-h100`](https://www.voltagepark.com/product/cloud-h100).
- NVIDIA Container Toolkit / DCGM image notes: [`hub.docker.com/r/nvidia/dcgm`](https://hub.docker.com/r/nvidia/dcgm), [`docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html`](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).

### Honest gaps

- I could not get current driver versions for Vast.ai or Crusoe-bare-metal in any documentation; both vary per host and per cluster generation.
- Voltage Park does not publish their default OS image driver pinning publicly.
- Whether RunPod's per-host kernel sets `kernel.dmesg_restrict=0` is **not in their public docs**; community reports indicate it is `=1` (the Ubuntu/Debian default since ~2018), making container dmesg almost always blocked.

---

## TASK 2 — Container Restrictions on dmesg

### Default Docker behavior

The kernel exposes the printk ring buffer via two paths: the `syslog(2)` syscall (used by `dmesg(1)`) and the `/dev/kmsg` character device. Two gates apply:

1. **`kernel.dmesg_restrict` sysctl** — when set to `1` (the modern default on Ubuntu, Debian, Fedora since ~2017–2018), reading the kernel buffer requires `CAP_SYSLOG`.
2. **Container capabilities** — the default Docker capability set **drops `CAP_SYSLOG`**. Even when the container appears to read the host's `dmesg_restrict` value as `0` (sysctls are namespaced), the kernel still rejects the syscall because the container lacks the capability. ([moby/moby#37897](https://github.com/moby/moby/issues/37897), [Fedora bugzilla 903192](https://bugzilla.redhat.com/show_bug.cgi?id=903192))

The net effect: **inside a default Docker container, `dmesg` returns "read kernel buffer failed: Operation not permitted" on essentially every modern host distro**, regardless of whether `--privileged` is on, unless the operator explicitly added `--cap-add SYSLOG` or set the host sysctl to 0.

### NVIDIA Container Toolkit specifics

NVIDIA Container Toolkit injects driver libraries and the GPU device files (`/dev/nvidia*`, `/dev/nvidia-uvm*`). It does **not** modify capabilities, does **not** mount `/dev/kmsg`, and does **not** loosen `dmesg_restrict`. So adding `--gpus all` or `--runtime=nvidia` gives you nvidia-smi but does nothing for kernel logs.

DCGM itself does NOT need privileged mode to read GPU telemetry (per the [official `nvidia/dcgm` image docs](https://hub.docker.com/r/nvidia/dcgm)) — DCGM talks to nvml, not kmsg. But DCGM *profiling metrics* (DCP) do need `--cap-add SYS_ADMIN` for the perf_event interface.

### Kubernetes pod securityContext

To get dmesg in a pod you need either:
```yaml
securityContext:
  capabilities:
    add: ["SYSLOG"]
  privileged: false   # SYSLOG is enough
```
or full privilege. Additionally, to read `/dev/kmsg`:
```yaml
volumeMounts:
- name: kmsg
  mountPath: /dev/kmsg
volumes:
- name: kmsg
  hostPath:
    path: /dev/kmsg
    type: CharDevice
```
([kind issue #662](https://github.com/kubernetes-sigs/kind/issues/662) is the canonical reference for why Kubernetes itself struggles with this.)

### `journalctl -k --since boot` inside a container

**Useful only in narrow cases.** `journalctl -k` reads the journald-saved kernel log, not the live ring buffer. It works inside a container only if:
1. The container has `/run/log/journal` or `/var/log/journal` bind-mounted from the host, AND
2. systemd-journald is the host logging backend (true on most modern hosts), AND
3. The user inside the container has read access to those files (often only `root` does).

In practice, **none of the major GPU cloud providers mount the host journal into customer containers**. Treating `journalctl -k` as a fallback would succeed on Lambda bare VMs (where you don't need the fallback anyway) and fail on RunPod, CoreWeave pods, Vast.ai, and AWS containers.

### Does `/dev/kmsg` work where dmesg doesn't?

**Sometimes.** Reading `/dev/kmsg` requires the same `CAP_SYSLOG` when `dmesg_restrict=1`, BUT the `/dev/kmsg` permission gate fires at *open*, not at *read*, and some container runtimes' default device-cgroup rules block opening it entirely (you'll get `Operation not permitted` or `No such file or directory` rather than a permission denied on read).

There is **one useful asymmetry**: on a host that has `dmesg_restrict=0` (older Debian/RHEL, some K8s nodes), the syscall API used by `dmesg(1)` will work for non-root, AND `/dev/kmsg` will work if the container runtime allows the device. So scanprobe should try **both** approaches and treat success on either as "log access works."

### Recommended detection + fallback for scanprobe

Add an `xid_log_source` field to the Xid check result with one of:
- `dmesg-cmd` — `dmesg` returned content
- `kmsg-dev` — `/dev/kmsg` was readable (consider `seek=end` + read newest N bytes)
- `journalctl-k` — fallback succeeded
- `unavailable-restricted` — all three failed; record this so the report says "Xid check unavailable (containerized; dmesg restricted)" rather than emitting a clean check
- `unavailable-no-driver` — `nvidia-smi` itself failed

The current `xid.py` likely just runs `dmesg` and treats failure as no Xids found. **That is a silent false negative on every default-Docker GPU host.** Add an explicit "log source unavailable" signal to the score (weight 0.05 — informational, not a tier change) and surface it in `recommendations` so users know to ask their provider for `--cap-add SYSLOG`.

### Sources

- [moby/moby #37897](https://github.com/moby/moby/issues/37897) "docker exposes dmesg to containers by default"
- [moby/moby #41318](https://github.com/moby/moby/issues/41318) "Cannot use dmesg, even with --privileged"
- [Fedora discussion 106419](https://discussion.fedoraproject.org/t/dmesg-read-kernel-buffer-failed-operation-not-permitted/106419)
- [cyberciti.biz nixCraft on dmesg_restrict](https://www.cyberciti.biz/faq/how-to-prevent-unprivileged-users-from-viewing-dmesg-command-output-on-linux/)
- [kind issue #662 on /dev/kmsg](https://github.com/kubernetes-sigs/kind/issues/662)
- [fluent-bit kmsg in container issue #2080](https://github.com/fluent/fluent-bit/issues/2080)
- [Proxmox sharing /dev/kmsg](https://forum.proxmox.com/threads/kubernetes-sharing-of-dev-kmsg-with-the-container.61622/)

---

## TASK 3 — Per-GPU-Model Thermal Thresholds

These are the values reported by `nvidia-smi -q -d TEMPERATURE` (driver 470+ uses absolute °C; driver 535+ adds T.Limit relative °C as well) on representative deployed units. Where multiple values circulate I list the most common deployed-fleet value and note the variance.

| GPU | TDP | Max Op (target ceiling) | Slowdown (HW throttle ~50% clocks) | Shutdown | Conf. |
|---|---|---|---|---|---|
| **H100 SXM5 80GB** | 700W | ~85°C *(NVIDIA-partner-restricted; widely reported as 85)* | ~87°C *(commonly observed in `nvidia-smi -q`)* | ~90°C | M (NVIDIA does not publish; values from operator reports + DGX H100 user guide) |
| **H100 PCIe 80GB** | 350W | ~83°C | ~87°C | ~90°C | M |
| **H100 NVL** | 350–400W (per chip) | ~83°C | ~87°C | ~90°C | L |
| **A100 80GB SXM4** | 400W | 85°C | 89°C | 92°C | H (multiple `nvidia-smi -q` outputs in NVIDIA forum threads) |
| **A100 40GB SXM4** | 400W | 85°C | 89°C | 92°C | H |
| **A100 80GB PCIe** | 300W | 85°C | 89°C | 92°C | H |
| **A100 40GB PCIe** | 250W | 85°C | 89°C | 92°C | H |
| **A10G** (AWS Cloud variant) | 150W (TDP, *not* 300W — common confusion) | 90°C | 95°C | 100°C | M (A10/A10G product brief; AWS-specific TDP differs from datacenter A10) |
| **A10** (datacenter) | 150W | 90°C | 95°C | 100°C | M |
| **V100 SXM2 32GB** | 300W | 83°C | 87°C | 90°C | H (V100 PCIe Product Brief PB-08744-001; HBM max 85°C) |
| **V100 PCIe** | 250W | 83°C | 87°C | 90°C | H |
| **T4** | 70W | 92°C | 94°C | 97°C | H (typical `nvidia-smi -q` output; T4 thermal integration thread) |
| **RTX 4090 (consumer)** | 450W | 84°C (throttle starts) | 90°C | 96°C / 105°C hotspot | M (consumer; values from TechPowerUp/Overclock.net + NVIDIA Help A2752) |
| **L40** | 300W | ~87°C | ~90°C | ~100°C | L (NVIDIA does not publish; community reports) |
| **L40S** | 350W | ~87°C | ~90°C | ~100°C | L |

**Important caveats**

1. **NVIDIA does not publish exact thermal thresholds for Hopper (H100/H200) or Ada datacenter (L40/L40S) in customer-facing materials.** A NVIDIA forum staff response to a direct H100 question in 2024 said: *"The answers to those are available to NVIDIA partners building systems using our NVIDIA H100 GPUs. You can contact the OEM."* ([forum thread](https://forums.developer.nvidia.com/t/nvidia-h100-recommended-operating-temperature/342125)). The values in the H100/L40 rows above come from `nvidia-smi -q` output on deployed units.
2. **The `nvidia-smi --query-gpu=temperature.gpu.tlimit` field (driver 535+) is the source of truth at runtime.** Where it is available, scanprobe should use it instead of any hardcoded table. T.Limit returns "degrees remaining until target" — when it hits 0 the GPU is at slowdown.
3. **`temperature.memory` (HBM) has its own thresholds** typically 5°C lower than the GPU die. The current scanprobe ignores `temperature_memory`; when present and ≥ memory-max, it should be its own warning. HBM stress is now a *bigger* killer of H100s than core temp.
4. **A10G TDP** in the original task table is wrong. AWS g5.xlarge advertises 300W per GPU at the *instance level including margin*; the chip itself is 150W. Don't compare power% against 300.

### Recommended scoring change

Replace the hardcoded `temp > 88` and `temp > 83` with:

```python
# Per-model thresholds (GPU die temperature)
# Format: (max_operating, slowdown, shutdown)
GPU_TEMP_THRESHOLDS = {
    "H100":  (85, 87, 90),    # SXM5 / PCIe / NVL
    "H200":  (85, 87, 90),
    "A100":  (85, 89, 92),
    "A10":   (90, 95, 100),
    "A10G":  (90, 95, 100),
    "L40":   (87, 90, 100),
    "L40S":  (87, 90, 100),
    "V100":  (83, 87, 90),
    "T4":    (92, 94, 97),
    "RTX 4090": (84, 90, 96),
    # Default fallback for unknown SKU: H100-conservative
    "_default": (83, 87, 90),
}

def thresholds_for(name: str):
    """Match by substring; H100 SXM5 80GB → 'H100' key."""
    n = name.upper()
    for k, v in GPU_TEMP_THRESHOLDS.items():
        if k != "_default" and k.upper() in n:
            return v
    return GPU_TEMP_THRESHOLDS["_default"]
```

In `nvidia_smi.py`, **prefer the runtime-queried values** and only fall back to the table:

```python
# Try driver-reported limits first (driver 535+, sometimes earlier)
_FIELDS = ",".join([
    ...,
    "temperature.gpu",
    "temperature.memory",
    "temperature.gpu.tlimit",      # remaining degrees to throttle (relative)
    ...
])
```
And if `temperature.gpu.tlimit` parses cleanly, skip the table lookup: throttle warning fires when `tlimit ≤ 3`, critical when `tlimit ≤ 0`.

### Sources

- [Tesla V100 PCIe Product Brief PB-08744-001](https://images.nvidia.com/content/tesla/pdf/Tesla-V100-PCIe-Product-Brief.pdf) (V100 thermals, HBM max)
- [NVIDIA H100 Recommended operating Temperature thread](https://forums.developer.nvidia.com/t/nvidia-h100-recommended-operating-temperature/342125) (NVIDIA confirmed values are partner-restricted)
- [Nvidia-smi GPU T.Limit / Shutdown T.Limit Temp](https://forums.developer.nvidia.com/t/nvidia-smi-gpu-t-limit-gpu-shutdown-t-limit-temp/292006) (semantics of relative vs absolute fields)
- [Nvidia-smi GPU target temperature / Maximum Operating](https://forums.developer.nvidia.com/t/nvidia-smi-gpu-target-temperature-maximum-operating-temperature/229325)
- [NVIDIA GPU max op temp custhelp A2752](https://nvidia.custhelp.com/app/answers/detail/a_id/2752) (general behavior, no model values)
- [Dell PowerEdge XE8545 + A100 thermal throttling KB](https://www.dell.com/support/kbdoc/en-us/000182430/xe8545)
- [L40S throttle thread](https://forums.developer.nvidia.com/t/what-is-the-temperature-at-which-the-l40s-gpu-starts-to-throttle/346424) (no concrete answer; "generally throttle near 90, keep below 95")
- [T4 thermal integration thread](https://forums.developer.nvidia.com/t/t4-thermal-integration/75470)

---

## TASK 4 — MIG and vGPU

### MIG (Multi-Instance GPU)

When MIG is enabled on an H100 or A100, a single physical GPU is partitioned into up to seven independent compute slices, each with isolated SM, L2, memory, and memory bandwidth.

#### How MIG appears in nvidia-smi

`nvidia-smi -L` example on a 4-way-partitioned A100:
```
GPU 0: NVIDIA A100-SXM4-80GB (UUID: GPU-xxx)
  MIG 1g.10gb     Device  0: (UUID: MIG-yyy-1)
  MIG 1g.10gb     Device  1: (UUID: MIG-yyy-2)
  MIG 2g.20gb     Device  2: (UUID: MIG-yyy-3)
  MIG 3g.40gb     Device  3: (UUID: MIG-yyy-4)
```

In tabular `nvidia-smi` output, MIG instances appear in a separate "MIG devices" table beneath the parent GPU table, with columns `GPU | GI ID | CI ID | MIG Dev | Memory-Usage | Vol Util | Shared CE/ENC/DEC/OFA/JPG | ECC`.

`--query-gpu` (the field set scanprobe uses) operates at the **parent GPU** level. To enumerate MIG instances you must use `nvidia-smi --query-compute-apps=...` or `nvidia-smi mig -lgip / -lgi / -lci`. ECC counters are reported only at the parent.

#### Indices

- The parent GPU keeps integer index 0,1,2,...
- MIG instances are addressed via UUID (`MIG-xxx`) or via the legacy `<gpu>:<gi>:<ci>` triplet.
- `CUDA_VISIBLE_DEVICES=MIG-GPU-uuid` is the canonical way to bind a workload to a single MIG instance.
- When MIG mode is enabled, the parent GPU is **not** itself usable for CUDA — only the instances are.

#### ECC counters per MIG?

**No.** ECC events are reported at the *physical* GPU level. NVML / nvidia-smi does not split SBE/DBE counts per MIG instance because ECC is tied to the physical memory controllers. A DBE in any partition surfaces as a DBE on the parent. ([NVIDIA MIG User Guide](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/index.html))

#### Can scanprobe distinguish MIG from full GPU?

Yes, three signals:
1. `nvidia-smi --query-gpu=mig.mode.current --format=csv` returns `Enabled` / `Disabled` / `[Not Supported]` per parent.
2. `nvidia-smi -L` text output contains the literal token "MIG " under each parent line.
3. The parent GPU's `compute_mode` becomes `Default` and CUDA cannot enumerate it; MIG UUIDs are the addressable units.

#### Recommended scanprobe behavior on MIG

- Add `mig.mode.current` to the `_FIELDS` query.
- When MIG enabled is detected:
  - **ECC checks run only at parent** — report parent-level ECC once, not duplicated per instance.
  - **Temperature, throttle, power are at parent level** — same.
  - **DCGM diag** can run per-MIG-instance with `dcgmi diag -r1 -i <gi-id>`; check whether the user has provided a MIG ID and route accordingly.
  - **matmul check should be per-instance** since each MIG sees only its slice.
  - **Surface MIG topology** in the report: "GPU 0 (A100-80GB) — MIG enabled, 4 instances, ECC reported at parent."

### vGPU (virtualized GPU under hypervisor)

#### Detection — what metadata indicates vGPU

In a guest OS where the GPU is being virtualized:
- **`nvidia-smi -q | grep -i Virtualization`** — outputs `GPU Virtualization Mode : VGPU` (vs `Pass-Through` or `None` for bare metal / SR-IOV passthrough).
- **`nvidia-smi vgpu`** — exits non-zero or returns "Not supported" on bare metal; on a vGPU host returns vGPU table; on a vGPU guest returns the assigned vGPU profile.
- **Driver name in `nvidia-smi -q`** — Guest drivers report as "GRID-vGPU" or include `vGPU` in the licensing fields. Look for `License Status` and `vGPU Type` fields.
- **System DMI** — `/sys/class/dmi/id/product_name` typically reads `KVM`, `VMware Virtual Platform`, `Microsoft Corporation Virtual Machine`, etc. on virtualized hosts. Combined with nvidia-smi GRID driver markings this is conclusive.
- **PCI vendor/device IDs** — vGPU instances expose distinctive sub-device IDs (e.g. NVIDIA `1eb8`-family for GRID).

#### Are ECC and Xid signals reliable in vGPU?

**No, not reliably.** Specifically:

- **ECC counters in the guest are usually all zero.** Guest NVML cannot read physical ECC registers. The hypervisor sees ECC; the guest does not. So `ecc.errors.uncorrected.volatile.total` will read 0 even on a degrading GPU.
- **Xid events do NOT propagate to the guest's `dmesg`.** Guest dmesg shows only the guest kernel; Xids occur in the host kernel ring buffer. Even if the guest had `CAP_SYSLOG`, it would see no GPU Xids.
- **Throttle reasons may or may not be visible** depending on vGPU profile and hypervisor pass-through settings. Time-sliced vGPU usually masks throttle bits.
- **Temperature is sometimes proxied, sometimes hidden.** Some vGPU profiles return the parent physical GPU temperature; others return N/A.

This means scanprobe running inside a vGPU guest is operating on a partial signal set. The DCGM checks are similarly degraded.

#### How scanprobe should behave under vGPU

- Detect vGPU via `nvidia-smi -q` Virtualization-Mode field.
- Mark each affected GPU with a `virtualization_mode` field on the result.
- **Do not penalize zero ECC** — under vGPU it's expected.
- **Do not run the Xid check** (or run it but mark it as "vGPU — Xids not visible to guest").
- Surface explicitly in the report: "GPU 0 — vGPU detected; ECC and Xid checks disabled (require host-side monitoring)."
- Lower the maximum confidence of the resulting tier — a vGPU "HEALTHY" should be reported as "HEALTHY (limited visibility)".

#### Sources

- [NVIDIA MIG User Guide](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/index.html), [PDF release r580](https://docs.nvidia.com/datacenter/tesla/pdf/MIG_User_Guide.pdf)
- [DGX A100 MIG section](https://docs.nvidia.com/dgx/dgxa100-user-guide/using-mig.html)
- [Scaleway MIG how-to](https://www.scaleway.com/en/docs/gpu/how-to/use-nvidia-mig-technology/)
- [NVIDIA Virtual GPU Software User Guide](https://docs.nvidia.com/vgpu/13.0/grid-vgpu-user-guide/index.html)
- [Querying vGPU info with nvidia-smi](https://forums.developer.nvidia.com/t/query-on-vgpu-and-nvidia-smi-command/162210)
- [nvidia-smi vgpu monitoring guide A4137](https://nvidia.custhelp.com/app/answers/detail/a_id/4137)

---

## TASK 5 — Specific Code Change Recommendations

### `ashiba_scanprobe/checks/nvidia_smi.py`

1. **Extend `_FIELDS` to query relative T.Limit and MIG/vGPU mode and HBM.**
   Append:
   ```python
   "temperature.gpu.tlimit",
   "mig.mode.current",
   "vgpu_instance",     # presence ≠ guest, but useful host-side; on guest use -q parser
   "driver_version",
   ```
   Note: `temperature.gpu.tlimit` is driver-535+. Older drivers will return `[Not Supported]`. The parser already tolerates that.

2. **Add fields to `NvidiaSmiResult`:**
   ```python
   gpu_temp_tlimit: Optional[float] = None      # degrees remaining until throttle
   mig_enabled: bool = False
   virtualization_mode: str = "None"            # "None", "VGPU", "Pass-Through", "Host-VGPU"
   driver_version: str = "unknown"
   max_operating_temp_c: Optional[float] = None # from per-model table
   slowdown_temp_c: Optional[float] = None
   shutdown_temp_c: Optional[float] = None
   ```

3. **Populate thresholds at parse time** using a `thresholds_for(name)` helper (table above).

4. **Add a separate one-shot call to `nvidia-smi -q -d TEMPERATURE,SUPPORTED_CLOCKS`** — only once per scanprobe run — to harvest `GPU Slowdown Temp / GPU Shutdown Temp / GPU Max Operating Temp` directly. These have been in nvidia-smi's verbose output for many years, longer than the relative tlimit field. Parse with regex; cache per-GPU-uuid.

5. **Detect vGPU from `nvidia-smi -q | grep "GPU Virtualization Mode"`.** Set `virtualization_mode` and skip Xid in the calling code.

6. **Detect MIG via `mig.mode.current`** — if enabled, set a flag the caller uses to skip per-instance ECC and to run DCGM at the right scope.

### `ashiba_scanprobe/scoring.py`

1. **Replace hardcoded `temp > 88` / `temp > 83`** with thresholds carried on `nvidia_result`:
   ```python
   max_op = nvidia_result.max_operating_temp_c or 83
   slowdown = nvidia_result.slowdown_temp_c or 87
   if temp >= slowdown:
       signals["temp_critical"] = 0.35
       recs.append(f"GPU temperature {temp:.0f}°C ≥ slowdown {slowdown:.0f}°C — HW throttle imminent")
   elif temp >= max_op:
       signals["temp_elevated"] = 0.12
       recs.append(f"GPU temperature {temp:.0f}°C ≥ max operating {max_op:.0f}°C")
   ```
   Or, when `gpu_temp_tlimit` is available, prefer the relative form:
   ```python
   if nvidia_result.gpu_temp_tlimit is not None:
       if nvidia_result.gpu_temp_tlimit <= 0:
           signals["temp_critical"] = 0.35
       elif nvidia_result.gpu_temp_tlimit <= 3:
           signals["temp_elevated"] = 0.12
   ```

2. **Don't penalize zero ECC under vGPU.** Add early branch:
   ```python
   if nvidia_result.virtualization_mode == "VGPU":
       signals["vgpu_limited_visibility"] = 0.08    # informational only
       recs.append("vGPU detected — ECC/Xid checks not reliable; consult host metrics")
       # skip the ECC zero-checks, they're meaningless here
   ```

3. **Power threshold should not fire on consumer GPUs at default fan curves.** RTX 4090 and 3090 routinely run at 99% of stated power limit with no thermal stress; the current `> 0.98` warning is noisy. Either skip the power warning when the GPU name doesn't contain a datacenter SKU substring, OR raise to `> 1.05` (over the rated cap) to catch only suspicious overpower.

4. **Add a `log_source_unavailable` weight (0.05, monitor-class)** so when the Xid check could not read kmsg or dmesg, the report tells the user. Currently a silent miss looks identical to "no Xid events found."

5. **Add per-GPU "data confidence" tag** to `RiskScore`:
   - `full` — bare-metal, dmesg works, DCGM available
   - `partial-container` — nvidia-smi only, no kernel logs
   - `partial-vgpu` — virtualization-limited
   - `partial-mig-parent` — values are parent-GPU; per-instance health unknown
   This is more honest than tier-only output and matches the existing repo's "evidence layers" design philosophy.

### Additional small fixes

- The current `_decode_throttle_reasons` parses hex; on driver 470 and earlier the field is decimal. Parse-tolerant: try base 16, then base 10.
- The current implementation reads `clocks_throttle_reasons.active`. Driver 555+ deprecated this in favor of `clocks_event_reasons.active`. Probe both names; the existing xid-and-throttle research doc already flagged this.
- The 30s `nvidia-smi` timeout is too long for an interactive scanprobe run on a hung GPU. Consider 10s with explicit "GPU may be hung" report on timeout — a hung nvidia-smi is itself a strong DRAIN signal.

---

## Confidence summary

| Section | Confidence |
|---|---|
| Cloud provider table (Lambda, AWS, GCP) | High — primary docs |
| Cloud provider table (RunPod, CoreWeave, Vast.ai) | Medium — community + provider docs |
| Cloud provider table (Together, Crusoe, VP) | Low–Medium — vendor pages, no operator confirmation |
| Container dmesg behavior | High — multiple primary sources |
| H100 / L40S thresholds | Medium — NVIDIA does not publish; community/observed values |
| A100, V100, T4 thresholds | High — directly observable in `nvidia-smi -q` and product briefs |
| RTX 4090 thresholds | Medium — consumer card, partial NVIDIA docs |
| MIG behavior | High — NVIDIA MIG User Guide |
| vGPU detection | High — NVIDIA vGPU User Guide |
| vGPU signal reliability claims | Medium — synthesized from multiple sources, would benefit from a real test inside a vGPU guest |

## Honest gaps

1. I could not verify whether `temperature.gpu.tlimit` reports usefully on H100 / L40S guests under vGPU — needs a real test.
2. RunPod, Vast.ai, Crusoe driver versions are host-by-host; any "common version" should be probed at runtime, not assumed.
3. Together AI / Voltage Park / Crusoe bare-metal default OS images are not publicly documented; field-test required.
4. The L40 / L40S thresholds in the table are operator-reported, not NVIDIA-confirmed. Treat as approximate.
5. The current scanprobe code path for the Xid check was not fully read in this research session — the recommended `xid_log_source` field needs to be cross-checked against `checks/xid.py` before implementation.
