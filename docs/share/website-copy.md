# scanprobe Website Copy

Use this as a small `/scanprobe` page or a "Tools" subpage on
ashibaresearch.com.

## Header

scanprobe

## Subhead

The low-hanging-fruit GPU evidence scan.

## Body

Run `scanprobe` when a GPU node acts weird and you need local NVIDIA evidence
before rerunning, draining, or filing a support ticket.

`scanprobe` runs a short, read-only scan of local NVIDIA evidence:

- `nvidia-smi` GPU visibility and basic GPU state
- ECC counters exposed by `nvidia-smi`
- temperature and hardware throttle flags
- readable current-boot NVIDIA Xid/kernel logs

It returns a conservative triage label:

- `CLEAR`: no local drain/watch evidence was visible
- `WATCH`: inspect visible evidence before rerunning expensive work
- `DRAIN`: do not launch new work until listed evidence is resolved
- `UNKNOWN`: this shell cannot see enough local GPU or kernel-log state

## Command

```bash
python3 scanprobe.py
```

## JSON

```bash
python3 scanprobe.py --json
```

## Constraints

No daemon. No telemetry. No mutation. No stress workload. No API key. No hidden
benchmark. No claim that a GPU is healthy.

## Footer

We currently ship NVIDIA local evidence only. We will add AMD support after real
AMD SMI, ROCm, and kernel-log fixtures show which read-only signals change an
operator's next action.

GitHub: https://github.com/cv700/scanprobe
