# scanprobe

## A read-only first-pass GPU evidence scan

`scanprobe` saves a few minutes in the standard GPU troubleshooting workflow.

Use it when a GPU node has become a suspect and you need obvious local NVIDIA
evidence before rerunning, draining, or filing a support ticket.

```bash
python3 scanprobe.py
python3 scanprobe.py --json
```

## What It Checks

`scanprobe` gathers the first-pass checks an operator often runs by hand:

- `nvidia-smi` GPU visibility and basic GPU state
- ECC counters exposed by `nvidia-smi`
- temperature and hardware throttle flags
- current-boot NVIDIA Xid/kernel-log evidence when visible

It prints a primary issue, visible evidence, next action, and one conservative
triage label:

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

It does not replace NVIDIA DCGM. DCGM is the serious datacenter GPU management
and diagnostics stack. `scanprobe` is the smaller first-pass scan before rerun,
drain, escalation, or deeper diagnostics.

## Why It Exists

In GPU incidents, the first pass often scatters across shell commands,
screenshots, Slack threads, and support-ticket fragments. `scanprobe` puts that
first pass into one read-only command and one pasteable report.

It answers one narrow question:

> Is this node/GPU obviously weird from local NVIDIA evidence, or should I keep
> looking elsewhere?

## Alpha Ask

We need skeptical feedback from GPU infrastructure engineers on the ordinary
workflow:

- real outputs from normal and weird nodes
- wrong verdicts
- confusing wording
- whether it saved time versus the manual first pass
- missing visible local evidence that would have changed the next action
- notes on where the command ran from: host, container, Slurm job, notebook, or
  Kubernetes pod

Success does not mean `scanprobe` diagnoses everything. Success means it saves a
few minutes, catches obvious local evidence, and makes the first response easier
to paste into Slack, Jira, or a provider ticket.

Use it for the first look after something feels off. Do not use it as a routine
heartbeat or a health certificate.

In alpha, we are testing whether that means roughly 2-10 minutes saved per
first-pass incident, depending on the user's access and familiarity with the
GPU stack.

Repo: https://github.com/cv700/scanprobe
