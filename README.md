# scanprobe

Minimal GPU health scan.

Design rule: easy to use, and above everything, do no harm.

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

Install from a clone:

```bash
pip install -e .
scanprobe
```

## Output

```text
scanprobe
GPU 0: HEALTHY score=0.00 temp=72C name=NVIDIA H100 80GB HBM3
GPU 1: WATCH score=0.40 temp=91C name=NVIDIA H100 80GB HBM3
  - HW thermal throttle active: HwThermalSlowdown
Node: WATCH
Completed in 1.2s
```

Exit codes:

```text
0 HEALTHY
1 WATCH
2 DRAIN
3 error
```

## Risk Signals

| Signal | Tier effect |
|--------|-------------|
| DBE ECC volatile error | DRAIN |
| Drain-class Xid: 48, 64, 74, 79, 95, 140, 143 | DRAIN |
| `nvidia-smi` query failure | DRAIN |
| HW thermal throttle | WATCH |
| GPU temperature > 88C | WATCH |
| DBE ECC aggregate lifetime count | WATCH |
| Watch-class Xid | WATCH |

Scores use geometric decay, so the strongest signal dominates and smaller
signals contribute less.

## Caveat

This is a static scan. It can catch visible problems before a run, but it cannot
prove a GPU is healthy and it does not detect silent data corruption.

## Development

```bash
python3 tests/test_scoring.py
python3 tests/test_nvidia_smi_parsing.py
python3 tests/test_xid_parsing.py
python3 tests/test_edge_cases.py
python3 scanprobe.py --help
python3 -m ashiba_scanprobe --help
```

MIT license.
