# Background Research Wave - 2026-05-07

Purpose: collect public, source-backed specimens and tool boundaries for the
next `scanprobe` alpha pass. This note is research, not product promise.

## Core Findings

1. NVIDIA's own troubleshooting flow validates the current default scope.

   NVIDIA GPU Operator troubleshooting tells users to inspect driver pod logs,
   `dmesg`, `NVRM`, `Xid`, and `nvidia-smi -q` when GPU driver or hardware
   issues appear. It also documents that the Kubernetes device plugin monitors
   NVML Xid events and can mark GPUs unhealthy.

   Source:
   https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/troubleshooting.html

   Product implication: `scanprobe` is not inventing a new signal family. It
   compresses a standard first-pass local evidence check into one pasteable
   output.

2. Xid remains the right low-hanging kernel-log signal.

   NVIDIA describes Xid messages as driver error reports printed to the
   operating system kernel log or event log. The current Xid table includes
   common high-signal cases such as Xid 48, 79, 94/95, 119/120, 137, and 140.

   Sources:
   https://docs.nvidia.com/deploy/xid-errors/
   https://docs.nvidia.com/deploy/xid-errors/contents.html
   https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html

   Product implication: keep Xid as node-level evidence unless a source gives a
   clean GPU-index mapping.

3. `nvidia-smi` explicitly has partial-support semantics.

   NVIDIA's `nvidia-smi` documentation says some devices and environments do
   not support all information, and unsupported data appears as `N/A`.

   Source:
   https://docs.nvidia.com/deploy/nvidia-smi/index.html

   Product implication: current `UNKNOWN` handling for unsupported required
   fields is correct. Do not coerce unsupported fields to zero.

4. DCGM is the serious adjacent diagnostic stack.

   DCGM supports background health checks, prologue checks, epilogue checks,
   active diagnostics, run levels, health watches, topology, NVLink counters,
   Prometheus export, and stress-style diagnostics.

   Sources:
   https://docs.nvidia.com/datacenter/dcgm/2.4/user-guide/feature-overview.html
   https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/dcgm-diagnostics.html
   https://docs.nvidia.com/datacenter/dcgm/latest/gpu-telemetry/dcgm-exporter.html

   Product implication: do not casually add DCGM to the default scan. If DCGM
   appears later, make it explicit, read-only, fixture-backed, and opt-in.

5. Vendor and cloud docs still expect local health scripts.

   NVIDIA Fabric Manager documentation lists GPU DBE, GPU falling off the PCIe
   bus, failed enumeration, NVLink training errors, and other GPU-side errors as
   conditions reported through sources such as syslog Xids, DCGM, NVML, and
   Fabric Manager logs. It also says administrators can create scripts that
   inspect one or more of those sources.

   Source:
   https://docs.nvidia.com/hgx-platforms/fabric-manager-user-guide/index.html

   Product implication: `scanprobe` can honestly be a small local evidence
   script, not a replacement for fleet observability.

6. Cloud troubleshooting docs point to the same first-pass evidence layer.

   AWS, Google Cloud, and Azure GPU troubleshooting materials all route users
   toward combinations of `nvidia-smi`, Xid/kernel logs, DCGM diagnostics, ECC
   status, support tickets, VM reset/recreate, or provider support.

   Sources:
   https://repost.aws/knowledge-center/ec2-linux-troubleshoot-xid-errors
   https://docs.cloud.google.com/compute/docs/troubleshooting/troubleshooting-gpus
   https://cloud.google.com/kubernetes-engine/docs/troubleshooting/gpus
   https://learn.microsoft.com/en-us/azure/virtual-machines/linux/n-series-driver-setup

   Product implication: the support-ticket use case is real. `scanprobe` should
   optimize for a small pasteable local summary before the user opens a provider
   ticket.

## Public Specimen Candidates

These are candidate fixtures or lab-notebook entries. Do not commit raw private
logs. Redact all hostnames, usernames, GPU UUIDs, IPs, and paths before turning
anything into a fixture.

### Device Lost / Handle Unavailable

Source:
https://github.com/NVIDIA/k8s-device-plugin/issues/231

Observed pattern:

- Kubernetes node with multiple GPUs.
- One GPU becomes lost.
- `nvidia-smi` reports it cannot determine the device handle.
- The lost GPU creates broader scheduling blast radius for the node.

Current `scanprobe` behavior:

- Good: device-handle loss is node-level `DRAIN`.
- Good: the signal does not get blamed on every GPU as per-GPU evidence.

Decision:

- Keep.
- Add real fixture if an alpha user sees this live.

### Xid 79 / Fallen Off Bus

Sources:

- https://forums.developer.nvidia.com/t/gpu-keeps-falling-off-the-bus/80899
- https://forums.developer.nvidia.com/t/bug-report-gpu-has-fallen-off-the-bus-randomly-nvidia-geforce-rtx-4090-nvidia-geforce-rtx-5090-d-dual-setup/362749
- https://forums.developer.nvidia.com/t/gpu-has-fallen-of-the-bus-nvidia-361-28-kernel-4-2-0/41624

Observed pattern:

- `NVRM: Xid ... 79` appears in local kernel logs.
- Public discussions mention power, thermal, PCIe, GSP, slot, and driver/kernel
  possibilities; root cause is not reliably inferable from the Xid alone.

Current `scanprobe` behavior:

- Good: Xid 79 is `DRAIN`.
- Good: output says visible evidence, not root cause.

Possible fixture gap:

- Add parser coverage for the alternate line shape:
  `NVRM: GPU at <pci> has fallen off the bus.`

Decision:

- Candidate small parser fixture.
- Do not add root-cause language.

### Xid 48 / DBE ECC

Sources:

- https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html
- https://docs.nvidia.com/deploy/dynamic-page-retirement/index.html
- https://forums.developer.nvidia.com/t/gpu-has-fallen-of-the-bus-nvidia-361-28-kernel-4-2-0/41624
- https://forums.developer.nvidia.com/t/cache-and-slice-significance-in-an-xid-48-message-hopper/329679

Observed pattern:

- Xid 48 represents an uncorrectable double-bit ECC event.
- NVIDIA's catalog says this can require a GPU reset or node reboot.
- Public Hopper reports show modern H100-style L2 cache/slice DBE lines.

Current `scanprobe` behavior:

- Good: Xid 48 is `DRAIN`.
- Good: volatile DBE ECC from `nvidia-smi` is `DRAIN`.

Decision:

- Keep.
- Seek one public redacted fixture for Hopper-style Xid 48 wording.

### Driver / Library Version Mismatch

Sources:

- https://github.com/NVIDIA/nvidia-container-toolkit/issues/171
- https://forums.developer.nvidia.com/t/nvidia-smi-failed-to-initialize-nvml-driver-library-version-mismatch/222848
- https://forums.developer.nvidia.com/t/failed-to-initialize-nvml-driver-library-version-mismatch/249158

Observed pattern:

- `nvidia-smi` fails with `Failed to initialize NVML`.
- Kernel logs often include `NVRM: API mismatch`.
- The operator action is driver/package/kernel cleanup, not GPU drain.

Current `scanprobe` behavior:

- Good: this stays `UNKNOWN`, not `DRAIN`.
- Gap: discovery-failure output does not yet make
  `NVIDIA driver/library mismatch prevents local GPU state` the primary issue.

Decision:

- Candidate small output fix.
- Keep tier as `UNKNOWN`.

### `No devices were found` With Kernel Xid Evidence

Source:
https://github.com/NVIDIA/open-gpu-kernel-modules/issues/862

Observed pattern:

- `nvidia-smi` reports `No devices were found`.
- Kernel logs can still contain meaningful NVRM/Xid evidence, including Xid 143
  and adapter initialization failures.

Current `scanprobe` behavior:

- Gap: discovery failure exits before `check_xid()`, so this local kernel
  evidence is not surfaced.

Decision:

- Candidate high-value alpha fix: when `nvidia-smi` is present but discovery
  fails or returns no devices, still run the read-only Xid scan and include its
  evidence if visible.
- Do not run extra commands when `nvidia-smi` is missing unless a fixture shows
  it changes the next action.

### NVIDIA-SMI Cannot Communicate With Driver

Sources:

- https://github.com/NVIDIA/open-gpu-kernel-modules/issues/862
- https://forums.developer.nvidia.com/t/nvidia-smi-has-failed-because-it-couldn-t-communicate-with-the-nvidia-driver-make-sure/247576

Observed pattern:

- `nvidia-smi` can fail before reporting GPU state.
- Public reports often ask users to inspect dmesg for NVIDIA, NVRM, nouveau,
  module load, secure boot, or driver binding evidence.

Current `scanprobe` behavior:

- Generic `UNKNOWN` is safe.
- Discovery failure does not yet try to attach readable Xid/NVRM evidence.

Decision:

- Same as above: consider a discovery-failure Xid scan.
- Do not broaden into driver installation diagnosis yet.

### MIG / vGPU / Consumer GPU `N/A` Fields

Sources:

- https://docs.nvidia.com/datacenter/tesla/mig-user-guide/latest/getting-started-with-mig.html
- https://docs.nvidia.com/1.5/user-guide/index.html
- https://forums.developer.nvidia.com/t/0-volatile-gpu-util/188402

Observed pattern:

- MIG and vGPU examples often show `N/A` for fan, power, utilization, or other
  physical-device fields.
- Consumer GPUs often show `N/A` for ECC fields.

Current `scanprobe` behavior:

- Good: unsupported required fields produce `UNKNOWN`, not `CLEAR`.
- Weakness: alpha users with older consumer GPUs may see `UNKNOWN` and think the
  tool failed unless the report explains partial support clearly.

Decision:

- Do not special-case MIG, vGPU, or consumer GPUs before live fixtures.
- Ask alpha users to treat `UNKNOWN` as useful visibility evidence.
- Consider one fixture for consumer GPU ECC `N/A` and one fixture for MIG/vGPU
  `N/A` if the first public users hit it.

## Adjacent Open-Source Tool Map

Use these to position `scanprobe`, not to expand it.

- `gpustat`: simple status display around NVIDIA GPU state.
  https://github.com/wookayin/gpustat
- `nvitop`: interactive NVIDIA process/resource monitor.
  https://github.com/XuehaiPan/nvitop
- `nvtop`: multi-vendor interactive GPU monitor.
  https://github.com/Syllo/nvtop
- `dcgm-exporter`: Prometheus GPU metrics exporter.
  https://github.com/NVIDIA/dcgm-exporter
- NVIDIA Kubernetes device plugin: GPU allocation and health integration.
  https://github.com/NVIDIA/k8s-device-plugin

Product implication: the open-source gap is not "GPU metrics exist." They do.
The gap is the one-line, no-setup, read-only, pasteable first-pass evidence
summary for the moment before rerun, drain, or support-ticket escalation.

## Tomorrow's Best Research-To-Code Candidates

Ranked by usefulness and safety:

1. Discovery-failure Xid scan.

   If `nvidia-smi` exists but reports no devices, cannot communicate, or returns
   a driver/NVML failure, run the current read-only Xid scan before returning.
   This can surface Xid 143, Xid 79, Xid 48, or NVRM evidence that currently
   gets hidden.

2. Discovery-failure primary issue for driver/library mismatch.

   If discovery output contains `Driver/library version mismatch`, set the
   primary issue to the existing mismatch wording and keep tier `UNKNOWN`.

3. Alternate fallen-off-bus parser.

   Add coverage for `NVRM: GPU at <pci> has fallen off the bus.` if a source
   fixture needs it.

4. Better generic nvidia-smi failure naming.

   Consider recognizing the exact string
   `NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver`
   as an `UNKNOWN` primary issue. Do not diagnose secure boot, nouveau, DKMS, or
   driver installation yet.

## Do Not Add From This Research Yet

- DCGM default commands.
- Fabric Manager log reads.
- Kubernetes logs.
- `nvidia-bug-report.sh`.
- `nvidia-smi -q` full output.
- Reset, reboot, drain, persistence, or clock commands.
- Stress, bandwidth, NCCL, or matmul probes.
- Root-cause explanations for Xid 79.

The right next move is still specimen collection.
