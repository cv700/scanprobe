# scanprobe Website Copy

Use this as a small `/scanprobe` page or a "Tools" subpage on
ashibaresearch.com.

## Header

scanprobe

## Subhead

A tiny read-only scan for the first few minutes of GPU troubleshooting.

## Body

Use `scanprobe` when a GPU node has become a suspect and you need obvious local
NVIDIA evidence before rerunning, draining, or filing a support ticket.

`scanprobe` gathers the first-pass checks an operator often runs by hand:

- `nvidia-smi` GPU visibility and basic GPU state
- ECC counters exposed by `nvidia-smi`
- temperature and hardware throttle flags
- readable current-boot NVIDIA Xid/kernel logs

It prints a primary issue, visible evidence, next action, and one conservative
triage label:

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

Not a DCGM replacement. DCGM is the serious datacenter GPU management and
diagnostics stack. `scanprobe` is the smaller first-pass scan before rerun,
drain, escalation, or deeper diagnostics.

The goal is modest: save a few minutes, catch obvious local evidence, and make
the first response easier to paste into Slack, Jira, or a provider ticket.

In alpha, we are testing whether that means roughly 2-10 minutes saved per
first-pass incident.

Use it for the first look after something feels off. Do not use it as a routine
heartbeat or a health certificate.

## Roadmap

`scanprobe` starts with the smallest useful promise.

- **v0: save one minute.** One command gathers obvious local NVIDIA evidence and
  prints a pasteable summary.
- **v1: save ten minutes.** Better fixtures, clearer issue tags, and stronger
  handling for common visibility, Xid, ECC, throttle, and `nvidia-smi` failure
  cases.
- **v2: save an hour.** A read-only runbook pilot helps operators decide which
  deeper tool to run next, including DCGM or provider diagnostics when
  appropriate.

The rule does not change: no mutation, no telemetry, no hidden benchmarks, and
no claim that a GPU is healthy.

## Footer

We currently ship NVIDIA local evidence only. We will add AMD support after real
AMD SMI, ROCm, and kernel-log fixtures show which read-only signals change an
operator's next action.

GitHub: https://github.com/cv700/scanprobe
