# Adjacent Tool Patterns

This note captures what `scanprobe` should borrow from adjacent tools without
expanding the default scan.

## Tools Reviewed

- NVIDIA GPU Debug Guidelines:
  https://docs.nvidia.com/deploy/gpu-debug-guidelines/index.html
- NVIDIA DCGM Diagnostics:
  https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/dcgm-diagnostics.html
- NVIDIA GPU Operator troubleshooting:
  https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/troubleshooting.html
- NVIDIA RMA / `nvidia-bug-report.sh` flow:
  https://docs.nvidia.com/deploy/rma-process/index.html
- PyTorch `collect_env`:
  https://docs.pytorch.org/docs/main/generated/torch.utils.collect_env.run.html
- `gpustat`:
  https://pypi.org/project/gpustat/
- `nvitop`:
  https://github.com/XuehaiPan/nvitop
- `npm doctor`:
  https://docs.npmjs.com/cli/v10/commands/npm-doctor/
- `kubectl cluster-info dump`:
  https://kubernetes.io/docs/reference/kubectl/generated/kubectl_cluster-info/kubectl_cluster-info_dump/
- Cilium `sysdump`:
  https://docs.cilium.io/en/latest/cmdref/cilium_sysdump.html
- NVIDIA `nccl-tests`:
  https://github.com/NVIDIA/nccl-tests
- NVIDIA `nvbandwidth`:
  https://github.com/NVIDIA/nvbandwidth

## Patterns To Borrow

- Make the operating mode visible. A practitioner should see that the default
  scan is read-only before reading any verdict.
- Produce pasteable support output. The first user action is often Slack, Jira,
  GitHub, or a provider support ticket.
- Prefer evidence, then advice. Do not ask the user to trust an opaque score.
- Treat unavailable signals as `UNKNOWN`, not failure.
- Keep JSON for automation, but optimize the default output for a human in an
  incident.
- Add support-bundle behavior only as an explicit future command, not as the
  default scan.
- Treat active diagnostics such as DCGM diagnostics, NCCL tests, and bandwidth
  tests as opt-in future probes.

## Patterns Not To Borrow Yet

- Do not add a broad doctor-check framework before real hardware fixtures.
- Do not add active stress or performance tests to the default path.
- Do not collect full logs by default.
- Do not attempt fixes, resets, drains, clock changes, persistence-mode changes,
  or scheduler actions.
- Do not pretend to replace DCGM, NVIDIA support tools, or provider observability.

## Current Product Implication

The default report should state:

```text
No external claim supplied; checking local visible NVIDIA evidence only.
Mode: read-only; no stress workload run; no fixes attempted.
```

That is the smallest useful synthesis of the adjacent-tool research: be
pasteable like `collect_env`, bounded like DCGM diagnostics, glanceable like GPU
status tools, and cautious like vendor support guidance.
