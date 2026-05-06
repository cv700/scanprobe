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

## Why correctness is the only thing that matters

The author of this tool is unknown in the ML infrastructure community. This tool
is their only credibility anchor. It will be read like a cover letter by people
who know exactly what's right and what's wrong.

When a ByteRobust author (Jiayi Yuan et al.) opens this repo, they are not reading
it as a user. They are reading it as a peer reviewer. Every Xid code, every ECC
field name, every signal weight, every paper citation is a test. They know the right
answers. One wrong Xid classification and they close the tab. One right one and they
keep reading. There is no benefit of the doubt for an unknown author.

**A nobody cannot ship ten things that are 80% right.**
The only credible position is: fewer things, totally correct.

If a check hasn't been validated on real hardware:
- Mark it `# UNVALIDATED:` with a specific note on what needs confirmation, OR
- Remove it entirely

A tool that does three things correctly is more trustworthy than a tool that does
ten things uncertainly. Scope is not credibility. Correctness is.

## The quality bar

Before shipping any code, ask: would a ByteRobust author (Jiayi Yuan et al., the
team that wrote the canonical paper on GPU cluster failure at scale) open this file
and nod, or close the tab?

Things that make them nod:
- Xid codes classified correctly with accurate descriptions
- ECC thresholds that match NVIDIA's own documentation
- Honest caveats about what the tool does NOT detect
- Paper citations with correct section numbers (`# ByteRobust §4.1`)
- Clean output that answers one question: "is this GPU safe to use?"
- "This does not detect dormant faults" somewhere prominent — proves you read §5.2

Things that make them close the tab:
- Wrong Xid codes or wrong drain/watch classification
- ECC field names that don't match actual nvidia-smi output
- Overclaiming ("detects all GPU failures")
- Reinventing DCGM badly instead of calling it correctly
- Enterprise dashboard noise instead of a clear verdict
- Any line of code that doesn't earn its place
- Citations that don't match what the paper actually says

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

## Testing: this must be rock solid

The test suite is not a formality. It is the foundation of credibility for an
unknown author. Every claim the tool makes must be backed by a test that would
catch it if it broke.

### Run before every commit

```bash
python tests/test_scoring.py
python tests/test_nvidia_smi_parsing.py
python scanprobe.py --help
python -m ashiba_scanprobe --help
```

All 56 tests must pass. If a test is wrong, fix or delete it — never write
code to make a wrong test pass.

### What "rock solid" means

The current test suite covers scoring logic and CSV parsing. That is necessary
but not sufficient. Rock solid means:

**1. Every signal weight has a test.**
If DBE volatile ECC maps to DRAIN, there is a test that asserts that.
If HW throttle + critical temperature combines to DRAIN, there is a test.
If the combination of two WATCH signals hits DRAIN threshold, there is a test.
Currently: mostly covered. Audit every signal in scoring.py and confirm a test exists.

**2. Every parser branch has a test.**
If the throttle bitmask parser handles `0x0`, `0x40`, `0x4000000000000000`, and
`N/A`, there are tests for all of them.
If the ECC parser handles `[Not Supported]`, `0`, `1`, `100`, there are tests.
If the Xid regex matches the correct dmesg format and rejects malformed lines,
there are tests.
Currently: partially covered. Add tests for every edge case that real hardware
might produce.

**3. The parser tests use real nvidia-smi output format.**
When you get real nvidia-smi output from a GPU node, add those exact CSV lines
as test fixtures. Do not invent test data — use real output, anonymized if needed.
Test against reality, not against your assumptions about reality.

**4. Error paths are tested.**
- nvidia-smi not found → correct error, exit 3
- nvidia-smi returns exit 1 → correct error, exit 3
- dmesg permission denied → graceful, Xid marked unavailable, score unaffected
- GPU index out of range → correct error
- Zero GPUs found → correct error

**5. The single-file scanprobe.py has independent tests.**
It has separate implementations of some functions. They must produce identical
results to the package for identical inputs. Add a test that cross-checks them.

### How to add tests

Add to `tests/test_scoring.py` for scoring logic.
Add to `tests/test_nvidia_smi_parsing.py` for parsing.
Create `tests/test_xid_parsing.py` for Xid regex and classification.
Create `tests/test_edge_cases.py` for error paths and malformed input.

Test names must read like documentation:
  `test_dbe_volatile_single_is_drain`          ← correct
  `test_parsing_works`                          ← wrong, too vague

### When real hardware output arrives

When `scanprobe --json` output from a real GPU node is available:
1. Add the raw nvidia-smi CSV lines as fixtures in test_nvidia_smi_parsing.py
2. Add the real dmesg Xid lines (if any) as fixtures in test_xid_parsing.py
3. Confirm every field parses to the expected value
4. If any field parses incorrectly, fix the parser and add the test before committing
5. Update the "Hardware tested" table in README.md with the confirmed hardware

Real hardware output is ground truth. Tests that contradict real hardware output
mean the code is wrong, not the hardware.

## What not to do

- Do not add features before the existing ones are validated on real hardware
- Do not add a web UI, dashboard, or database
- Do not add telemetry or phone-home without explicit user opt-in
- Do not make the output longer — every line in the output must earn its place
- Do not add dependencies to the zero-dep tier (Tier 1 / scanprobe.py)
- Do not break the single-file invariant of scanprobe.py
- Do not write aspirational docstrings ("in future versions, this will...")
- Do not write tests that test your assumptions — test against documented behavior
  or real hardware output
- Do not mark something as validated unless it has run on real GPU hardware
- Do not increase the claimed scope of the tool — it does not detect dormant faults,
  and it should say so clearly rather than quietly omitting the caveat

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
