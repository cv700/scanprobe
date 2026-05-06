# Release Readiness

`scanprobe` should earn trust through real fixtures before broad operator
outreach.

## Current Status

The current repo is suitable for:

- local development
- source review
- mocked parser/output tests
- cautious private runs by people who understand the caveats
- collecting redacted fixtures

It is not yet suitable for:

- broad public operator outreach
- scheduler integration
- automated drain workflows
- claims of MIG or vGPU support
- claims that `CLEAR` means a GPU is healthy

## Private Alpha Gate

Before calling this a private alpha, require:

- all local tests pass
- default command surface matches `docs/safety-audit.md`
- at least 3 real no-GPU or no-visible-GPU fixtures
- at least 3 real healthy GPU-node fixtures
- at least 2 restricted-kernel-log fixtures
- at least 1 warning or drain fixture from a real incident or public report
- README and product contract still say the scan is read-only, advisory-only,
  and not a health proof

## Public Operator Outreach Gate

Before public operator outreach, require the full fixture target from
`docs/hardware-testing.md`:

- 5 no-GPU or no-visible-GPU fixtures
- 10 healthy GPU-node fixtures across at least 3 environments
- 5 restricted-kernel-log fixtures
- 3 real warning/drain fixtures from incidents or public reports
- 1 multi-GPU non-contiguous index fixture, if observed
- 2 MIG/vGPU fixtures before claiming support for either mode

Also require:

- Xid bucket changes cite source notes and include tests
- no default DCGM, NCCL, matmul, bandwidth, thermal, or stress workload
- no default network, telemetry, sudo, reset, drain, clock, persistence, or
  scheduler action
- issue templates still ask for redacted text output, JSON output, environment,
  kernel-log visibility, and MIG/vGPU context

## Feature Gate

New default checks need all five:

- read-only
- source-backed
- fixture-backed
- common in real reports
- able to change the user's next action

If any one is missing, keep the feature out of the default scan.

## Launch Principle

Do not ship confidence faster than fixtures. A small, honest tool with real
coverage is more useful than a broad diagnostic that operators cannot trust.
