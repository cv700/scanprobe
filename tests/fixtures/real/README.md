# Real Fixtures

This directory is for redacted real-world fixtures.

Do not commit raw machine logs. Use `local-fixtures/` for private captures, then
copy only sanitized files here.

Each fixture should include:

- where it came from at a coarse level, for example `cloud-container-h100`
- what was expected, for example `healthy`, `restricted-kernel-log`, `xid-79`
- raw command output after redaction
- expected `scanprobe` verdict
- any known caveat

Good fixture names:

```text
cloud-container-h100-clear/
lambda-a100-restricted-kernel-log/
baremetal-h100-xid-79/
nogpu-macos-nvidia-smi-missing/
```

Never include secrets, hostnames, usernames, IP addresses, customer names, or
private workload details.
