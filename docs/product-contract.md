# Product Contract

`scanprobe` is a read-only local NVIDIA evidence scan.

By default, no external claim is supplied. The default scan checks only local
visible NVIDIA evidence and reports that boundary before giving a verdict.
The default scan also reports that it is read-only and does not run stress
workloads or attempt fixes.
Command-derived text is redacted for common host identifiers before it appears
in reports, but users should still review output before sharing.
Kernel-log evidence is scoped to readable current-boot logs, and event recency is
not interpreted yet.
JSON output is advisory-only. It is for wrappers and operator workflows, not an
automatic remediation or drain authority.

It exists for one moment:

> A GPU node acts weird, and the user needs to know what local NVIDIA evidence is
> visible before rerunning, draining, or filing a support ticket.

## Promise

`scanprobe` should:

- run as one command
- read local signals only
- avoid network access
- avoid telemetry upload
- avoid host, GPU, driver, scheduler, and monitoring-state mutation
- show visible evidence before advice
- say when no external claim was supplied
- say when the scan is read-only and non-remediating
- say that kernel-log event recency is not interpreted
- explain what was not checked
- redact common host identifiers in command-derived text
- give a small next-action list
- mark JSON automation semantics as advisory-only
- say `UNKNOWN` instead of guessing when evidence is unavailable

## Non-Promise

`scanprobe` does not:

- prove a GPU is healthy
- prove application correctness
- detect silent data corruption
- assess NCCL or fabric health
- replace DCGM
- replace NVIDIA support tools
- run stress tests or active diagnostics by default
- drain nodes, reset GPUs, change clocks, or configure persistence mode

## Verdicts

- `CLEAR`: no local drain/watch evidence was visible in this scan. Human output
  expands this label to avoid implying that the node is healthy.
- `WATCH`: visible evidence deserves inspection before long or expensive work.
- `DRAIN`: visible evidence suggests the node should not receive new work until
  resolved.
- `UNKNOWN`: the scan could not observe enough local GPU state from here.

## Feature Rule

Add a feature only if it is:

- read-only
- source-backed
- fixture-backed
- common in real reports
- able to change the user's next action

If a signal does not change the next action, it does not belong in the default
scan.
