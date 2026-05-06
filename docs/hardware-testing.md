# Hardware Testing Plan

Mocked tests prove parser and output behavior. Hardware testing proves the tool
survives real environments.

The goal is not to prove a GPU is healthy. The goal is to collect real command
outputs and confirm that `scanprobe` remains safe, readable, and useful.

## Test Ladder

### 1. No-GPU Machine

Purpose: verify the `UNKNOWN` path.

```bash
python3 scanprobe.py
python3 scanprobe.py --json
```

Expected: `UNKNOWN` with visible `nvidia-smi` absence or failure evidence.

### 2. GPU Node Without Host Kernel Log Access

Purpose: verify common container/cloud behavior where `nvidia-smi` works but
`dmesg` is restricted.

```bash
python3 scanprobe.py
python3 scanprobe.py --json
```

Expected: GPU rows are parsed; Xid scan is unavailable or uses the read-only
`journalctl` fallback. The tool must not suggest `sudo`.

### 3. GPU Host With Kernel Log Access

Purpose: verify real Xid scan behavior.

```bash
python3 scanprobe.py
python3 scanprobe.py --json
```

Expected: visible Xid events become node-level evidence. Xids should not be
attributed to every visible GPU unless the source supports that attribution.

### 4. Real Incident Node

Purpose: verify that output changes the user's next action.

Useful incident classes:

- NVML init failure
- `nvidia-smi` cannot determine device handle
- Xid 79 / 95 / 154
- volatile DBE ECC
- thermal or hardware slowdown
- no visible GPUs on a node expected to have GPUs

Expected: output is pasteable into Slack/Jira/provider support and gives the
operator a clear rerun/drain/escalate decision.

## Minimum Fixture Targets

Before calling this ready for public operator outreach, aim for:

- 5 no-GPU or no-visible-GPU fixtures
- 10 healthy GPU-node fixtures across at least 3 environments
- 5 restricted-kernel-log fixtures
- 3 real warning/drain fixtures from incidents or public reports
- 1 multi-GPU non-contiguous index fixture, if observed

## Environments To Try

- local machine without NVIDIA GPU
- single-GPU workstation
- cloud GPU container
- cloud GPU VM with host access
- 8x H100/H200 or A100 node
- RunPod / Lambda / CoreWeave / bare metal, if available
- MIG environment, later and explicitly marked as unsupported until fixtures
  exist

## Redaction Rules

Do not commit raw logs blindly.

Before committing a fixture, remove or replace:

- hostnames
- usernames
- IP addresses
- job IDs
- container IDs
- absolute user paths
- secrets or tokens
- customer names
- anything unrelated to NVIDIA GPU evidence

Keep enough structure that the parser still sees realistic lines.

## Collection Helper

Use:

```bash
bash scripts/collect-fixture.sh
```

By default, the helper writes local files under `local-fixtures/`, which is
ignored by git. It captures `scanprobe` output and `nvidia-smi` query outputs.

Kernel logs can be sensitive, so they are skipped by default. To collect them
for local review:

```bash
SCANPROBE_INCLUDE_KERNEL_LOGS=1 bash scripts/collect-fixture.sh
```

Review and redact before moving anything into `tests/fixtures/real/`.
