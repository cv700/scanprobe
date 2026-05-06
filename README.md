# scanprobe

Minimal NVIDIA GPU evidence scan.

Run `scanprobe` when a GPU node acts weird and you need to know what local
NVIDIA evidence is visible before rerunning, draining, or filing a support
ticket.

Design rule: easy to use, and above everything, do no harm.

Feature rule: add a feature only if it is read-only, source-backed,
fixture-backed, common in real reports, and changes the user's next action. If a
signal does not change the next action, it does not belong in the default scan.

It answers one narrow question:

> Do local, visible NVIDIA GPU signals suggest this node is risky to use right
> now?

It does two things:

1. Reads `nvidia-smi` for ECC errors, temperature, and throttle reasons.
2. Scans `dmesg` for NVIDIA Xid events when kernel logs are available.

That is all. No DCGM, no matmul benchmark, no collective test, no background
service, no telemetry.

It never resets GPUs, changes clocks, changes persistence mode, starts stress
workloads, drains nodes, or sends data anywhere. It only reads local signals and
prints a verdict.

```bash
python3 scanprobe.py
python3 scanprobe.py --json
```

## Output

```text
scanprobe
Node: WATCH

GPU 0: CLEAR temp=72C name=NVIDIA H100 80GB HBM3
Visible evidence:
  - no local drain/watch evidence observed for this GPU

GPU 1: WATCH temp=91C name=NVIDIA H100 80GB HBM3
Visible evidence:
  - HW thermal throttle active: HwThermalSlowdown

Next action:
  - Inspect the listed evidence before rerunning long or expensive work.
  - If this followed a NCCL or training failure, correlate with rank, app, and fabric logs.

Not checked: silent data corruption, NCCL/fabric health, application correctness.
Completed in 1.2s
```

Exit codes:

```text
0 CLEAR
1 WATCH
2 DRAIN
3 UNKNOWN or error
```

## Risk Signals

| Signal | Tier effect |
|--------|-------------|
| DBE ECC volatile error | DRAIN |
| Drain-class Xid: 48, 64, 74, 79, 95, 140, 143 | DRAIN |
| Xid 154 reset/reboot/drain recovery action | DRAIN |
| `nvidia-smi` cannot determine GPU device handle | DRAIN |
| HW thermal throttle | WATCH |
| GPU temperature > 88C | WATCH |
| DBE ECC aggregate lifetime count | WATCH |
| Watch-class Xid | WATCH |
| `nvidia-smi` unavailable or NVML init failure | UNKNOWN |

JSON output includes an internal score for automation. The default human output
does not show scores because the useful thing is visible evidence and next
action, not fake precision.

## Caveat

This is a static scan. It can catch visible problems before a run, but it cannot
prove a GPU is healthy and it does not detect silent data corruption.

## Development

```bash
python3 tests/test_scanprobe.py
python3 scanprobe.py --help
```

MIT license.
