# ashiba scanprobe

[![tests](https://github.com/cv700/scanprobe/actions/workflows/test.yml/badge.svg)](https://github.com/cv700/scanprobe/actions/workflows/test.yml)

GPU cluster health check. Runs in ~20 seconds. No install required.

```bash
curl -fsSL https://raw.githubusercontent.com/cv700/scanprobe/main/scanprobe.py | python3
```

Or install permanently:

```bash
pip install ashiba-scanprobe
scanprobe
```

---

## Example output

```
ashiba scanprobe  v0.1.0  ─  github.com/cv700/scanprobe

  GPU 0  ✓ HEALTHY  H100 SXM5          72°C  no ECC errors
  GPU 1  ✓ HEALTHY  H100 SXM5          74°C  no ECC errors
  GPU 2  !  WATCH   H100 SXM5          91°C  HW thermal throttle: HwThermalSlowdown
  GPU 3  ✓ HEALTHY  H100 SXM5          71°C  no ECC errors

  Node:  WATCH
  → GPU 2  HW thermal throttle: HwThermalSlowdown
  → GPU 2  Temperature critical: 91°C

  Checked: nvidia-smi · ECC counters · Xid scan  (18s)
  Skipped: DCGM (not found), matmul/collective (--tier 2)
  Tip: python3 scanprobe.py --tier 2  for DCGM + matmul checks (~3 min)
```

---

## What it checks

| Check | What it catches | Tier |
|-------|----------------|------|
| nvidia-smi | ECC errors, temperature, clock throttle | 1 |
| Xid scan (dmesg) | Hardware faults written to kernel ring buffer | 1 |
| DCGM diagnostics | GPU memory bandwidth, compute validation | 1+ |
| Matmul correctness | Silent numerical errors (SDC) vs FP64 reference | 2 |
| Collective latency | Per-rank allreduce outliers — NVLink / fabric faults | 2+ |

```
--tier 1   ~20s    nvidia-smi + ECC + Xid               (default)
--tier 2   ~3min   + DCGM diagnostics + matmul
--tier 3   ~10min  + collective latency sweep (all GPUs)
```

---

## Risk tiers

| Tier | Score | Meaning |
|------|-------|---------|
| HEALTHY | < 0.20 | No detected issues. Proceed. |
| WATCH | 0.20 – 0.49 | Anomaly present. Investigate before a long run. |
| DRAIN | ≥ 0.50 | Hardware fault confirmed. Do not use this GPU. |

Exit codes: `0=HEALTHY  1=WATCH  2=DRAIN  3=error`

---

## Key signals and weights

| Signal | Weight | Source |
|--------|--------|--------|
| Xid drain-class (48, 63, 74, 79, 94, 95) | 0.85 | dmesg |
| DBE ECC volatile | 0.70+ | nvidia-smi |
| DCGM diagnostic failure | 0.55+ | dcgmi |
| nvidia-smi query failure | 0.55 | nvidia-smi |
| HW thermal throttle | 0.40 | nvidia-smi |
| Temperature > 88°C | 0.35 | nvidia-smi |
| Matmul >50% anomalous shapes | 0.35 | torch |
| DBE ECC aggregate (lifetime) | 0.30 | nvidia-smi |
| Collective latency outlier >3σ | 0.30 | torch.distributed |
| Xid watch-class | 0.25 | dmesg |

Scores aggregate with geometric decay: the strongest signal dominates, each additional contributes half as much. Two WATCH signals (e.g., HW throttle + high temperature) combine to DRAIN.

---

## What it doesn't do

This covers pre-flight signals. It will **not** detect dormant faults that only manifest after hundreds of training steps — some GPUs pass all pre-flight checks and fail at step 450+. NVIDIA's diagnostic tooling has ~70% recall on silent data corruption events.

For in-flight detection, see [ByteRobust (§4–5)](https://arxiv.org/abs/2509.16293) and [XPUTimer](https://arxiv.org/abs/2409.02053).

---

## Install

```bash
# Zero dependencies (nvidia-smi + dmesg only)
pip install ashiba-scanprobe

# With pretty terminal output
pip install ashiba-scanprobe[display]

# With matmul + collective checks (requires PyTorch)
pip install ashiba-scanprobe[full]
```

Python 3.9+. The single-file `scanprobe.py` has no dependencies beyond stdlib.

---

## Why

GPU hardware failures are common at scale. Meta's Llama 3 training on 16,384 H100s experienced one job failure every 3 hours. NVIDIA's EUD diagnostic has ~70% recall on SDC events (ByteRobust §4.3). Pre-flight checks catch the detectable subset before you commit 72 hours of compute.

Built by [Ashiba](https://ashibaresearch.com) · MIT license

---

## Hardware tested

Real-hardware validation is in progress. The checks were written from documentation
and papers. If you run this on real hardware, please open an issue or PR with your
`scanprobe --json` output — it is the most valuable contribution you can make.

| Hardware | Driver | Status |
|----------|--------|--------|
| H100 SXM5 | — | in progress |
| A100 80GB | — | not yet |
| A10G | — | not yet |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). You don't need a GPU.
