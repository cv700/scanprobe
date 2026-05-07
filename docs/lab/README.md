# Lab Notebook

This directory holds scanprobe specimen notes.

A specimen is one observed case:

- a live `scanprobe` run
- a redacted user report
- a public troubleshooting report with raw-ish NVIDIA evidence
- a fixture candidate that may change parsing, wording, or triage

The purpose is to keep the product scientific: observation first, fixture
second, code change last.

## Rules

- Do not commit private raw logs.
- Redact hostnames, usernames, IP addresses, GPU UUIDs, job IDs, container IDs,
  absolute user paths, customer names, secrets, and unrelated application text.
- Record where the command ran: host, container, notebook, Slurm, Kubernetes,
  cloud VM, local workstation, or another shell.
- Record whether kernel logs were visible from that shell.
- Record whether the output was useful, confusing, wrong, or action-changing.
- Turn a specimen into code only if the change is read-only, source-backed,
  fixture-backed, common in real reports, and changes the user's next action.

## Daily Note

Use one file per research day:

```text
docs/lab/YYYY-MM-DD-alpha-specimens.md
```

Keep notes short. The value is the chain:

```text
real observation -> redacted fixture -> test -> code or doc decision
```
