# scanprobe

## A read-only first-pass GPU evidence scan

Run `scanprobe` when a GPU node acts weird and you need local NVIDIA evidence
before rerunning, draining, or filing a support ticket.

```bash
python3 scanprobe.py
python3 scanprobe.py --json
```

## What It Checks

`scanprobe` reads local, visible NVIDIA evidence:

- `nvidia-smi` GPU visibility and basic GPU state
- ECC counters exposed by `nvidia-smi`
- temperature and hardware throttle flags
- current-boot NVIDIA Xid/kernel-log evidence when visible

It returns one of four triage labels:

- `CLEAR`: no local drain/watch evidence was visible
- `WATCH`: inspect visible evidence before rerunning expensive work
- `DRAIN`: do not launch new work until listed evidence is resolved
- `UNKNOWN`: this shell cannot see enough local GPU or kernel-log state

## What It Does Not Check

`scanprobe` does not check silent data corruption, NCCL/fabric health,
application correctness, cloud-provider state, storage, scheduler behavior, or
performance regressions without visible local evidence.

It does not run DCGM, NCCL tests, CUDA samples, PyTorch, matmul tests, bandwidth
tests, thermal stress tests, daemons, network calls, or telemetry.

It does not reset GPUs, change clocks, change persistence mode, drain nodes, or
start a workload.

## Why It Exists

In GPU incidents, the first pass often scatters across shell commands, Slack
threads, screenshots, and support-ticket fragments. `scanprobe` makes that
first pass consistent, conservative, and pasteable.

It answers one narrow question:

> Do local, visible NVIDIA GPU signals suggest this node is risky to use right
> now?

## Alpha Ask

We need skeptical feedback from GPU infrastructure engineers:

- real outputs from normal and weird nodes
- wrong verdicts
- confusing wording
- missing evidence that would have changed the next action
- notes on where the command ran from: host, container, Slurm job, notebook, or
  Kubernetes pod

Success does not mean `scanprobe` diagnoses everything. Success means it saves
time and reduces ambiguity during the first pass of a GPU incident.

Repo: https://github.com/cv700/scanprobe
