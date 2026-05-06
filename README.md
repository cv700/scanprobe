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

If `nvidia-smi` is unavailable or reports no visible GPUs, `scanprobe` reports
`UNKNOWN` with the visible reason instead of guessing.

Xid events from kernel logs are node-level evidence. `scanprobe` does not blame
every visible GPU for a node-level Xid unless the signal is explicitly tied to
that GPU by a local source.

Command-derived text in JSON is redacted for common host identifiers such as
journal hostnames, GPU UUIDs, IP addresses, long hex IDs, and user home paths.
Review output before sharing; redaction is a guardrail, not a guarantee.

```bash
python3 scanprobe.py
python3 scanprobe.py --json
```

## Output

```text
scanprobe
No external claim supplied; checking local visible NVIDIA evidence only.
Mode: read-only; no stress workload run; no fixes attempted.
Kernel-log scope: readable current-boot logs; event recency not interpreted.

Node: WATCH

Node-level evidence:
  - no node-level Xid drain/watch evidence observed

GPU evidence:

GPU 0: CLEAR temp=72C name=NVIDIA H100 80GB HBM3
  - no local GPU drain/watch evidence observed

GPU 1: WATCH temp=91C name=NVIDIA H100 80GB HBM3
  - HW throttle active: HwThermalSlowdown

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

## Risk Signals

| Scope | Signal | Tier effect |
|-------|--------|-------------|
| GPU | DBE ECC volatile error | DRAIN |
| GPU | `nvidia-smi` cannot determine GPU device handle | DRAIN |
| GPU | HW throttle or thermal slowdown | WATCH |
| GPU | GPU temperature > 88C | WATCH |
| GPU | DBE ECC aggregate lifetime count | WATCH |
| GPU | `nvidia-smi` unavailable or NVML init failure | UNKNOWN |
| Node | Reset/restart-class Xid | DRAIN |
| Node | Xid 154 reset/reboot/drain recovery action | DRAIN |
| Node | Watch-class Xid | WATCH |

JSON output includes an internal score for wrappers and scripts, plus an
`automation` object that marks the report as advisory-only. The default human
output does not show scores because the useful thing is visible evidence and
next action, not fake precision.

## Caveat

This is a static scan. It can catch visible problems before a run, but it cannot
prove a GPU is healthy and it does not detect silent data corruption.

It also does not assess fabric conformance, collective tail latency, or network
failure absorption. Those are real AI infrastructure problems, but they are
outside this local first-pass scan.

## Development

```bash
python3 tests/test_scanprobe.py
python3 scanprobe.py --help
```

For real-machine validation, see
[`docs/hardware-testing.md`](docs/hardware-testing.md).
For reports and feature proposals, see
[`CONTRIBUTING.md`](CONTRIBUTING.md) and the GitHub issue templates.

MIT license.
