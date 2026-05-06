# NVIDIA DCGM Scope Note

Sources:

- NVIDIA DCGM diagnostics:
  https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/dcgm-diagnostics.html
- NVIDIA DCGM exporter:
  https://docs.nvidia.com/datacenter/dcgm/latest/gpu-telemetry/dcgm-exporter.html
- NVIDIA DCGM field identifiers:
  https://docs.nvidia.com/datacenter/dcgm/latest/dcgm-api/dcgm-api-field-ids.html

## Why It Matters

DCGM is NVIDIA's serious datacenter GPU management and diagnostics stack. It
already covers much of the territory that an ambitious GPU health checker might
try to claim:

- cluster readiness diagnostics before workloads
- epilogue and post-mortem diagnostics after failures
- active diagnostic runs with multiple levels and plugins
- GPU telemetry for Prometheus/Grafana through `dcgm-exporter`
- field-level signals for Xids, PCIe, NVLink, thermal/power violations, row
  remapping, retired pages, and GPU recovery action

This means `scanprobe` should not position itself as a DCGM replacement.

## Boundary For scanprobe

`scanprobe` is the low-hanging-fruit local evidence scan. Its default path
should remain:

- zero dependency
- stdlib only
- no daemon requirement
- no hostengine requirement
- no active diagnostics
- no stress tests
- no health-watch mutation
- no scheduler integration

Do not run `dcgmi diag` by default. DCGM diagnostics can be active workloads and
belong behind explicit operator intent.

Do not run commands that configure DCGM health watches by default. The default
scan should not mutate local monitoring state.

## Possible Future Read-Only DCGM Work

A future optional `--dcgm` mode may be reasonable only after real fixtures exist.
Candidates must be read-only and source-backed:

- detect whether `dcgmi` exists
- read `dcgmi -v`
- parse `dcgmi discovery -l`
- parse existing `dcgmi health -c` output, if this is confirmed read-only in
  practice
- parse local `dcgm-exporter` metrics only when explicitly requested

Each candidate still needs fixtures from real machines before it enters the CLI.

## Product Implication

The honest positioning is:

`scanprobe` is what a user runs when they need the simplest visible NVIDIA
evidence now. DCGM is what an admin uses for deeper NVIDIA diagnostics,
telemetry, readiness validation, and fleet integration.

That boundary is a strength. It keeps `scanprobe` harmless and adoptable while
leaving room to interoperate with DCGM later.
