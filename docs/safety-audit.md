# Safety Audit

`scanprobe` is designed to be safe to run on a production GPU node because the
default scan only reads local evidence.

## Default Command Surface

| Purpose | Command | Timeout | Mutation risk |
|---------|---------|---------|---------------|
| Discover visible GPU indices | `nvidia-smi --query-gpu=index --format=csv,noheader` | 10s | Read-only query |
| Read GPU counters | `nvidia-smi --query-gpu=<fields> --format=csv,noheader,nounits` | 30s | Read-only query |
| Read filtered kernel log | `dmesg --level=err,warn,crit,alert,emerg` | 10s | Read-only log read |
| Read full kernel log fallback | `dmesg` | 10s | Read-only log read |
| Read boot kernel log fallback | `journalctl -k -b --no-pager` | 10s | Read-only log read |

The tool does not use `sudo`, shell expansion, network commands, background
processes, temporary files, or daemon state.

## Explicit Non-Actions

The default scan does not:

- reset GPUs
- drain nodes
- change clocks
- change persistence mode
- configure DCGM health watches
- run `dcgmi diag`
- run NCCL collectives
- run matmul, memory, thermal, or stress workloads
- write logs or telemetry
- contact any remote service

## Failure Behavior

If a command is unavailable, denied, returns no visible GPU state, or times out,
`scanprobe` reports `UNKNOWN` or records unavailable evidence instead of trying
to escalate privileges.

Kernel-log access is best-effort. If `dmesg` is restricted, `scanprobe` tries a
read-only `journalctl -k -b --no-pager` fallback. If that is unavailable too,
the output says the Xid scan is unavailable rather than guessing.

## Guardrail

Any new default command must be added to this document and covered by a test
that verifies:

- the command is invoked without a shell
- output is captured
- text mode is enabled
- a timeout is set
- the command is read-only
