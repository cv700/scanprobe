# scanprobe

The low-hanging-fruit GPU evidence scan.

Run `scanprobe` when a GPU node acts weird and you need to know what local
NVIDIA evidence is visible before rerunning, draining, or filing a support
ticket.

Design rule: easy to use, and above everything, do no harm.

Feature rule: add a feature only if it is read-only, source-backed,
fixture-backed, common in real reports, and changes the user's next action. If a
signal does not change the next action, it does not belong in the default scan.

The product contract is in
[`docs/product-contract.md`](docs/product-contract.md).
The default command surface is documented in
[`docs/safety-audit.md`](docs/safety-audit.md).
Release gates are documented in
[`docs/release-readiness.md`](docs/release-readiness.md).

It answers one narrow question:

> Do local, visible NVIDIA GPU signals suggest this node is risky to use right
> now?

By default, no external claim is supplied. `scanprobe` checks only local visible
NVIDIA evidence and says so in the report.

The default mode is read-only. It does not run stress workloads or attempt
fixes.

Kernel-log evidence comes from readable current-boot logs. `scanprobe` does not
yet interpret event recency.

It does two things:

1. Reads `nvidia-smi` for ECC errors, temperature, and throttle reasons.
2. Scans local kernel logs for NVIDIA Xid events when they are available.

That is all. No DCGM, no matmul benchmark, no collective test, no background
service, no telemetry.

It never resets GPUs, changes clocks, changes persistence mode, starts stress
workloads, drains nodes, or sends data anywhere. It only reads local signals and
prints a verdict.

If `nvidia-smi` is unavailable, `scanprobe` reports `UNKNOWN` with the visible
reason instead of guessing. If `nvidia-smi` exists but cannot discover GPUs or
cannot report GPU state, `scanprobe` still includes readable current-boot Xid
evidence when local kernel logs are visible.

Xid events from kernel logs are node-level evidence. `scanprobe` does not blame
every visible GPU for a node-level Xid unless the signal is explicitly tied to
that GPU by a local source.

Where you run it matters. In containers, notebooks, Kubernetes pods, Slurm jobs,
or cloud shells, `nvidia-smi` may be visible while host kernel logs are hidden.
In MIG or vGPU environments, GPU count, names, indices, and unsupported fields
may not mean the same thing as full physical GPUs. Treat partial evidence as
partial evidence.

Command-derived text in JSON is redacted for common host identifiers such as
journal hostnames, GPU UUIDs, IP addresses, long hex IDs, and user home paths.
Review output before sharing; redaction is a guardrail, not a guarantee.

```bash
python3 scanprobe.py
python3 scanprobe.py --json
```

## What It Wraps

`scanprobe` runs a short, read-only NVIDIA evidence scan:

- `nvidia-smi --query-gpu=index --format=csv,noheader`
- `nvidia-smi --query-gpu=index,name,ecc.errors.corrected.volatile.total,ecc.errors.uncorrected.volatile.total,ecc.errors.uncorrected.aggregate.total,temperature.gpu,clocks_throttle_reasons.active --format=csv,noheader,nounits`
- `dmesg --level=err,warn,crit,alert,emerg`
- `dmesg`
- `journalctl -k -b --no-pager`

It parses those outputs for local NVIDIA evidence and maps the evidence to
`CLEAR`, `WATCH`, `DRAIN`, or `UNKNOWN`.

## What It Does Not Wrap

`scanprobe` does not run DCGM, NCCL tests, CUDA samples, PyTorch, matmul tests,
HBM bandwidth tests, thermal stress tests, Slurm commands, Kubernetes commands,
cloud provider APIs, daemons, network calls, or telemetry.

It does not reset GPUs, change clocks, change persistence mode, drain nodes,
change scheduler state, change kernel state, or start a workload.

We currently ship NVIDIA local evidence only. We will add AMD support after real
AMD SMI, ROCm, and kernel-log fixtures show which read-only signals change an
operator's next action.

## How To Use It

Run `scanprobe` when a GPU job fails, hangs, slows down, or a node looks
suspicious and you need quick local evidence before choosing the next action.

Run it from the most host-like shell you have access to:

```bash
python3 scanprobe.py
```

Use JSON when attaching output to an issue, ticket, wrapper, or runbook:

```bash
python3 scanprobe.py --json
```

Interpret the result as triage evidence:

- `DRAIN`: do not launch new work on this node until the listed evidence is
  resolved.
- `WATCH`: inspect the listed evidence before rerunning long or expensive work.
- `UNKNOWN`: this shell could not see enough local GPU or kernel-log state.
- `CLEAR`: no local drain/watch evidence was visible; keep debugging outside
  this scan.

If filing a support ticket, include the text output, JSON output, where the
command ran from (host, container, Slurm job, notebook, Kubernetes pod), and
whether the failure was a hang, crash, slowdown, NCCL timeout, or training
failure.

## Output

```text
scanprobe
No external claim supplied; checking local visible NVIDIA evidence only.
Mode: read-only; no stress workload run; no fixes attempted.
Kernel-log scope: readable current-boot logs; event recency not interpreted.

Node: WATCH
Primary issue: GPU 1 reports hardware throttle.

Visibility:
  - nvidia-smi GPU query visible on 2 selected GPUs
  - Xid scan available via dmesg-cmd

Node-level evidence:
  - no node-level drain/watch evidence observed

GPU evidence:

GPU 0: CLEAR temp=72C name=NVIDIA H100 80GB HBM3
  - nvidia-smi: no local GPU drain/watch evidence observed

GPU 1: WATCH temp=91C name=NVIDIA H100 80GB HBM3
  - nvidia-smi: HW throttle active: HwThermalSlowdown

Next action:
  - Inspect the listed evidence before rerunning long or expensive work.
  - If this followed a NCCL or training failure, correlate with rank, app, and fabric logs.

Not checked: silent data corruption, NCCL/fabric health, application correctness.
Completed in 1.2s
```

Exit codes:

```text
0 CLEAR (no visible local drain/watch evidence)
1 WATCH
2 DRAIN
3 UNKNOWN or error
```

## Triage Tiers

The tiers are triage labels, not proof of health.

- `CLEAR`: no local drain/watch evidence was visible. Keep debugging app, data,
  NCCL/fabric, scheduler, storage, or provider-level logs if the job still
  failed.
- `WATCH`: visible evidence deserves inspection before rerunning long or
  expensive work. Correlate with rank, app, and fabric logs.
- `DRAIN`: visible evidence suggests this node should not receive new work until
  resolved. `scanprobe` does not drain anything; it only reports evidence.
- `UNKNOWN`: this shell could not observe enough local GPU state. Run from the
  host if possible, or ask the provider/admin to check host GPU and kernel logs.

## Risk Signals

| Scope | Signal | Tier effect |
|-------|--------|-------------|
| GPU | DBE ECC volatile error | DRAIN |
| GPU | Critical temperature plus HW throttle | DRAIN |
| GPU | HW throttle or thermal slowdown | WATCH |
| GPU | GPU temperature > 88C | WATCH |
| GPU | DBE ECC aggregate lifetime count | WATCH |
| GPU | `nvidia-smi` unavailable, unsupported required fields, NVML init failure, or driver/library mismatch | UNKNOWN |
| Node | `nvidia-smi` cannot determine GPU device handle | DRAIN |
| Node | Reset/restart-class Xid | DRAIN |
| Node | Xid 154 reset/reboot/drain recovery action | DRAIN |
| Node | Watch-class Xid | WATCH |
| Node | Kernel logs unavailable from this shell | UNKNOWN |

JSON output includes an internal advisory score for wrappers and scripts, but
tier decisions are explicit rules rather than score thresholds. JSON also
includes an `automation` object that marks the report as advisory-only. The
default human output does not show scores because the useful thing is visible
evidence and next action, not fake precision.

## Caveat

This is a static scan. It can catch visible problems before a run, but it cannot
prove a GPU is healthy and it does not detect silent data corruption.

It also does not assess fabric conformance, collective tail latency, or network
failure absorption. Those are real AI infrastructure problems, but they are
outside this local first-pass scan.

MIG and vGPU environments are not validated yet. Reports from those environments
are useful, but they should become fixtures before `scanprobe` adds special
handling.

## Development

```bash
python3 tests/test_scanprobe.py
python3 scanprobe.py --help
```

For real-machine validation, see
[`docs/hardware-testing.md`](docs/hardware-testing.md).
Before broad operator outreach, see
[`docs/release-readiness.md`](docs/release-readiness.md).
For reports and feature proposals, see
[`CONTRIBUTING.md`](CONTRIBUTING.md) and the GitHub issue templates.

MIT license.
