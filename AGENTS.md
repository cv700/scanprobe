# scanprobe — Codex brief

## What this is

`scanprobe` is a GPU cluster health check tool. One command, ~20 seconds, tells you
whether your GPUs are safe to train on. Zero mandatory dependencies — runs anywhere
with Python 3.9+ and nvidia-smi.

Two layers:
- **Scan** (Tier 1): passive telemetry — reads nvidia-smi, ECC counters, Xid events
  from dmesg. No perturbation.
- **Probe** (Tier 2+): active workloads — matmul numerical correctness vs FP64
  reference, collective allreduce latency per rank.

## The quality bar

Before shipping any code, ask: would a ByteRobust author (Jiayi Yuan et al., the
team that wrote the canonical paper on GPU cluster failure at scale) open this file
and nod, or close the tab?

Things that make them nod:
- Xid codes classified correctly with accurate descriptions
- ECC thresholds that match NVIDIA's own documentation
- Honest caveats about what the tool does NOT detect
- Paper citations with correct section numbers
- Clean output that answers one question: "is this GPU safe to use?"

Things that make them close the tab:
- Wrong Xid codes or wrong drain/watch classification
- Overclaiming ("detects all GPU failures")
- Reinventing DCGM badly instead of calling it correctly
- Enterprise dashboard noise instead of a clear verdict
- Any line of code that doesn't earn its place

## The code has never run on real GPU hardware

This is the most important fact. Every field name, every regex, every threshold was
written from documentation and papers — not from running against real H100s or A100s.

When you find something that looks wrong, uncertain, or unvalidated:
- **Delete it or mark it clearly** rather than shipping it silently
- A smaller correct tool is better than a larger uncertain one
- If a check can't be validated, wrap it in a clear warning or remove it

## Red commits are as valuable as green ones

A commit that deletes 50 lines of unvalidated code is worth more than a commit that
adds 50 lines of aspirational code. Deletion is a feature.

If something in the codebase is:
- Aspirational (not yet tested against real hardware)
- Duplicated
- Wrong in a subtle way
- Not earning its place in the output

Delete it. Revert it. The repo should only contain code that has earned the right to
be there.

## Architecture

```
ashiba_scanprobe/
  checks/
    nvidia_smi.py   — ECC, temperature, throttle. One subprocess for all GPUs.
    xid.py          — dmesg scan. Pure stdlib. DRAIN_XIDS / WATCH_XIDS sets.
    dcgm.py         — dcgmi diag wrapper. Skips gracefully if not installed.
    matmul.py       — GEMM numerical correctness. Requires torch+numpy.
    collective.py   — allreduce latency via torchrun. Requires torch+numpy.
  scoring.py        — Geometric decay aggregation → HEALTHY/WATCH/DRAIN
  cli.py            — Entry point. Zero mandatory deps (typer optional).
  report.py         — Output. Rich optional, falls back to plain text.

scanprobe.py        — Single-file edition. Stdlib only. curl | python3 works.
tests/
  test_scoring.py           — 31 tests, full truth table. Hardware-free.
  test_nvidia_smi_parsing.py — 25 tests, CSV parsing. Hardware-free.
```

## Scoring model

Geometric decay: signals sorted by weight descending, each at half the previous.
`score = w[0] + w[1]*0.5 + w[2]*0.25 + ...`

Thresholds: WATCH ≥ 0.20, DRAIN ≥ 0.50.

This is intentional: a pile of monitor-class signals should not mimic a drain event.
Two watch-class signals CAN combine to drain. Do not change this without understanding
why it was designed this way.

## Known unknowns (investigate before shipping)

1. **ECC field names**: `ecc.errors.corrected.volatile.total` and
   `ecc.errors.uncorrected.volatile.total` — validate these are the right
   nvidia-smi query fields on actual hardware. Field names vary by driver version.

2. **Throttle bitmask**: The hex bitmask decoding in `nvidia_smi.py` was written
   from NVML documentation. Validate against actual `clocks_throttle_reasons.active`
   output. On some drivers this field returns a decimal string, not hex.

3. **Xid regex**: `NVRM: Xid (PCI:XXXX): YY, ...` — validate this pattern against
   actual dmesg output. Cloud nodes (RunPod, Lambda, CoreWeave) may have different
   kernel message formatting.

4. **dmesg access**: On most cloud nodes, plain `dmesg` works without sudo. The
   `--level=err,warn,...` flag may not be supported on older kernels. The fallback
   to plain `dmesg` exists but needs testing.

5. **DCGM invocation**: `dcgmi diag -r 1` — validate this is the correct command
   on current DCGM versions. The result parsing in `dcgm.py` is minimal and may
   need adjustment for actual output format.

## Output format

The output answers one question: am I safe to start my training run?

```
ashiba scanprobe  v0.1.0  ─  github.com/cv700/scanprobe

  GPU 0  ✓ HEALTHY  H100 SXM5    72°C  no ECC errors
  GPU 1  ✓ HEALTHY  H100 SXM5    74°C  no ECC errors
  GPU 2  !  WATCH   H100 SXM5    91°C  HW thermal throttle
  GPU 3  ✓ HEALTHY  H100 SXM5    71°C  no ECC errors

  Node:  WATCH
  → GPU 2  HW thermal throttle: HwThermalSlowdown
  → GPU 2  Temperature critical: 91°C

  Checked: nvidia-smi · ECC counters · Xid scan  (18s)
  Skipped: DCGM (not found), matmul/collective (--tier 2)
```

If you change the output format, ensure the example in README.md matches.

## Running tests

```bash
python tests/test_scoring.py
python tests/test_nvidia_smi_parsing.py
python scanprobe.py --help
python -m ashiba_scanprobe --help
```

All tests are hardware-free. 56 tests, all must pass before any commit.
If a test is wrong, fix or delete the test — do not write code to make a
wrong test pass.

## What not to do

- Do not add features before the existing ones are validated on real hardware
- Do not add a web UI, dashboard, or database
- Do not add telemetry or phone-home without explicit user opt-in
- Do not make the output longer — every line in the output must earn its place
- Do not add dependencies to the zero-dep tier (Tier 1 / scanprobe.py)
- Do not break the single-file invariant of scanprobe.py
- Do not write aspirational docstrings ("in future versions, this will...")

## Exit codes

0=HEALTHY  1=WATCH  2=DRAIN  3=error

These are stable. Do not change them.

## Craft: what makes people root for this tool

These are not features. They are craft decisions that determine whether an engineer
closes the tab or stars the repo.

**When healthy, say so warmly.**
`Node: HEALTHY · 8 GPUs checked — good to go` not just `Node: HEALTHY`.
The engineer who checks before a 72-hour run and gets a clean result should
feel something.

**The Xid moment should teach.**
When Xid 94 is found, don't print `Xid 94 found`. Print:

  ✗  Xid 94 · GPC fault (uncontained error)
     This GPU has experienced a hardware fault requiring a driver reset.
     Do not use this GPU for training. File a support ticket referencing Xid 94.

The engineer learns what Xid 94 means. That is the moment they share the tool.

**Graceful degradation names the fix.**
`Xid scan unavailable — try: sudo scanprobe` not just `dmesg failed`.
Every error message should tell you the next thing to try.

**--help text is the manual.**
Nobody reads docs. Every option description should be genuinely useful:
  --tier 2    adds matmul numerical correctness probe + DCGM (~3 min, requires torch)
not:
  --tier 2    tier 2 checks

**The JSON schema is a contract.**
Once engineers pipe `scanprobe --json` into pre-training scripts, field names
cannot change without a version bump. Do not rename JSON fields. Document any
additions.

**GPU count in the verdict.**
`8 GPUs checked, all healthy` lands better than `HEALTHY`.
The number makes it feel like something real happened.

**The code is readable, not clever.**
Comments explain why, not what. `# ByteRobust §4.1 — proactive checks` belongs.
`# iterate over list` does not. Replace every clever line with an obvious line.

**No TODO comments in shipped code.**
Either do it or delete it. A shipped TODO is a broken promise to every reader.

**The "Tested on" section in README starts empty and fills honestly.**
```
## Hardware tested
- [ ] H100 SXM5 (in progress)
```
Do not claim hardware has been tested until it has. Fill this in as real
hardware validates the checks. Blank is honest. Fake checkmarks are not.

**CONTRIBUTING.md should be 10 lines.**
The most important line: "You don't need a GPU. Run the two test files.
If you have a GPU, paste your `scanprobe --json` output in the PR."
Drop the barrier to contribution to zero.
