# scanprobe

Keep this repo small.

Product rule: easy to use, and above everything, do no harm.

Feature rule: add a feature only if it is read-only, source-backed,
fixture-backed, common in real reports, and changes the user's next action.

`scanprobe` is the low-hanging-fruit GPU evidence scan:

- `nvidia-smi` telemetry
- Xid events from local kernel logs (`dmesg`, then read-only `journalctl` fallback)
- node and per-GPU `CLEAR` / `WATCH` / `DRAIN` / `UNKNOWN`

Build for the moment when a GPU node acts weird and the user needs local NVIDIA
evidence before rerunning, draining, or filing a support ticket.

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
- If a signal does not change the user's next action, collect it later or not at
  all.
- Treat Xid classifications as high-risk correctness work.
- Treat Xid/kernel-log findings as node-level evidence unless a source directly
  ties the event to a GPU index.
- Do not claim the tool proves a GPU is healthy.
- Real hardware output beats assumptions.

## Test

Run before every commit:

```bash
python3 tests/test_scanprobe.py
python3 scanprobe.py --help
```

If a test is wrong, fix or delete the test. Do not bend code around a bad test.
