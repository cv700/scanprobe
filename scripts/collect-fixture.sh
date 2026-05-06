#!/usr/bin/env bash
set -u

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out_dir="${1:-local-fixtures/$stamp}"
include_kernel_logs="${SCANPROBE_INCLUDE_KERNEL_LOGS:-0}"

mkdir -p "$out_dir"

printf 'scanprobe fixture collection\n' > "$out_dir/README.txt"
printf 'created_utc=%s\n' "$stamp" >> "$out_dir/README.txt"
printf 'include_kernel_logs=%s\n' "$include_kernel_logs" >> "$out_dir/README.txt"
printf 'uname=%s\n' "$(uname -srm 2>/dev/null || true)" >> "$out_dir/README.txt"
printf '\nReview and redact before sharing or committing.\n' >> "$out_dir/README.txt"

run_capture() {
  name="$1"
  shift

  printf '%s\n' "$*" > "$out_dir/$name.cmd.txt"
  "$@" > "$out_dir/$name.stdout.txt" 2> "$out_dir/$name.stderr.txt"
  status=$?
  printf '%s\n' "$status" > "$out_dir/$name.exit.txt"
}

run_capture scanprobe-text python3 scanprobe.py
run_capture scanprobe-json python3 scanprobe.py --json
run_capture nvidia-smi-indices nvidia-smi --query-gpu=index --format=csv,noheader
run_capture nvidia-smi-fields nvidia-smi "--query-gpu=index,name,ecc.errors.corrected.volatile.total,ecc.errors.uncorrected.volatile.total,ecc.errors.uncorrected.aggregate.total,temperature.gpu,clocks_throttle_reasons.active" --format=csv,noheader,nounits

if [ "$include_kernel_logs" = "1" ]; then
  run_capture dmesg-filtered dmesg --level=err,warn,crit,alert,emerg
  run_capture dmesg-full dmesg
  run_capture journalctl-kernel journalctl -k -b --no-pager
else
  printf 'Skipped. Re-run with SCANPROBE_INCLUDE_KERNEL_LOGS=1 to collect locally.\n' > "$out_dir/kernel-logs.skipped.txt"
fi

printf 'Wrote %s\n' "$out_dir"
