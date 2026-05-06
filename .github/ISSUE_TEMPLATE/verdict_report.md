---
name: Verdict or parsing report
about: Report a verdict, parser, or output issue from a real environment
title: "[report] "
labels: report, fixture-needed
---

## What happened?

Describe what felt wrong or surprising.

Examples:

- `scanprobe` said `DRAIN`, but the node looked usable.
- `scanprobe` said `CLEAR`, but the job still failed.
- `scanprobe` could not parse local GPU evidence.
- `scanprobe` missed an Xid or reported one unclearly.

## Redacted output

Paste redacted output from:

```bash
python3 scanprobe.py
python3 scanprobe.py --json
```

Review before sharing. Redaction is a guardrail, not a guarantee.

## Environment

- GPU model(s):
- Driver version:
- `nvidia-smi` works from this shell? yes/no
- Kernel logs visible from this shell? yes/no/unknown
- Host, container, Kubernetes, Slurm, notebook, or other:
- MIG/vGPU enabled? yes/no/unknown
- Cloud/provider or hardware context, if shareable:

## Why was `scanprobe` run?

- Before rerun
- After job failure
- Before draining
- Before filing support ticket
- Other:

## Expected next action

What did you expect the tool to help decide?

- rerun here
- inspect
- drain/exclude node
- file provider/admin support ticket
- other:

## Fixture permission

Can a sanitized version of this report become a test fixture?

- yes
- no
- ask me first
