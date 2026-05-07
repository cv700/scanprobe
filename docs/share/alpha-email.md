# Alpha Outreach Email

Subject: Skeptical read on a tiny GPU triage CLI?

Hi [Name],

I am building a small open-source CLI called `scanprobe`.

It does one narrow thing: it runs a read-only local NVIDIA evidence scan
(`nvidia-smi` plus readable current-boot Xid/kernel logs) and prints a
pasteable `CLEAR` / `WATCH` / `DRAIN` / `UNKNOWN` summary.

The goal is not to diagnose every GPU incident. The goal is to save a few
minutes in the standard first pass when a node acts weird and someone needs to
decide whether to rerun, inspect, drain, or file a support ticket.

Design constraints:

- no root requirement
- no daemon
- no telemetry
- no mutation
- no stress workload
- no API key
- bounded runtime
- `UNKNOWN` when the shell cannot see enough local state

Would you or someone on your team be willing to give it a skeptical alpha pass?

The concrete thing I want to test is modest: does this save 2-10 minutes in the
first pass, or is that estimate wrong?

What I need most:

1. Run it on a normal NVIDIA GPU node.
2. Run it on a weird node if you have one.
3. Tell me where the wording feels wrong.
4. Tell me where the verdict disagrees with operator judgment.
5. Tell me whether it saved time versus the manual first pass.
6. Share sanitized output if you can.

Totally fine if the feedback is "too basic." I want to learn that before I add
anything.

Repo: https://github.com/cv700/scanprobe

Default command:

```bash
python3 scanprobe.py
```

JSON:

```bash
python3 scanprobe.py --json
```

Thanks
