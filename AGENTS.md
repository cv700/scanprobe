# scanprobe

Keep this repo small.

Product rule: easy to use, and above everything, do no harm.

`scanprobe` is a minimal GPU health scanner:

- `nvidia-smi` telemetry
- Xid events from `dmesg`
- per-GPU `HEALTHY` / `WATCH` / `DRAIN`

Do not add DCGM, matmul, NCCL, dashboards, telemetry, agents, databases, or new
frameworks until the current scan has real hardware fixtures.

## Files

```text
scanprobe.py                       single-file stdlib edition
tests/test_scanprobe.py            hardware-free tests
```

## Rules

- One command should be enough for the default use case.
- Read local signals only; do not mutate host, driver, GPU, clock, persistence,
  scheduler, or kernel state.
- Do not start stress workloads by default.
- Do not send telemetry or logs anywhere.
- Prefer deletion over speculative code.
- Keep dependencies at zero.
- Keep the implementation in `scanprobe.py` until duplication earns its way back.
- Treat Xid classifications as high-risk correctness work.
- Do not claim the tool proves a GPU is healthy.
- Real hardware output beats assumptions.

## Test

Run before every commit:

```bash
python3 tests/test_scanprobe.py
python3 scanprobe.py --help
```

If a test is wrong, fix or delete the test. Do not bend code around a bad test.
