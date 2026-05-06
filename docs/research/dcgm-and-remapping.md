# DCGM diagnostics and HBM row-remapping — research for scanprobe

Scope: verify the `dcgmi diag -r 1` invocation in `ashiba_scanprobe/checks/dcgm.py`
and design a new memory-remapping check that consumes
`nvidia-smi -q -d ROW_REMAPPER`.

Confidence column convention:

- **High** = stated directly in NVIDIA documentation or NVIDIA source code.
- **Medium** = corroborated by a reputable third-party post / GitHub issue / vendor KB.
- **Low** = inferred or extrapolated; flagged for follow-up.

---

## 1. DCGM diagnostics

### 1.1 Invocation syntax (DCGM 3.x and 4.x)

| Finding | Detail | Confidence |
|---|---|---|
| Base command is `dcgmi diag -r <level>` in both DCGM 3.x and 4.x | Confirmed in NVIDIA "DCGM Diagnostics" page and the `Diag.cpp` source on master. The `-r` flag has not changed across the 3.x→4.x release line. | High |
| `-r` accepts either a numeric level (`1`–`4`) or a named plugin (e.g. `-r pulse_test`, `-r eud`, `-r memtest`) | NVIDIA docs list both forms. Named plugins are how 4.x exposes diagnostics that don't fit the legacy levels. | High |
| `-r` is the same flag as the long form `--run` | Confirmed in issue [#158](https://github.com/NVIDIA/DCGM/issues/158): `dcgmi diag --run 4 -p ... --json`. | High |
| Per-GPU targeting uses `-i <comma-list>` (NOT `-g`) | `-g` is a *group ID* from `dcgmi group`. `-i` takes a comma-separated GPU index list, e.g. `dcgmi diag -i 0,1,2 -r 1`. Confirmed by issue [#70](https://github.com/NVIDIA/DCGM/issues/70) ("`dcgm diag -i <GPU>` not working correctly"). | High |
| GPUs are auto-detected when neither `-i` nor `-g` is given | Default behavior is to run against the implicit "all GPUs" group `DCGM_GROUP_ALL_GPUS`. Confirmed in `Diag.cpp`. | High |
| JSON output is enabled via `-j` (short) or `--json` (long) | Both forms work. `-j` is the canonical short form documented in Microsoft VirtualClient's DCGMI doc and used in NVIDIA docs. The earlier `--json` long form is also accepted; NVIDIA repo issues use both interchangeably. | High |
| Useful supplementary flags | `--statspath <dir>` (per-test stats dump), `--debugLogFile <path>`, `-d <ERROR\|WARN\|INFO\|DEBUG>` (debug verbosity), `-p "key=val;key=val"` (override plugin parameters), `--plugin-path` | High |

**Recommendation:** scanprobe's current `["dcgmi", "diag", "-r", str(level)]` is
correct. Do **not** add `-g <gpu_index>` — that mistake would silently fail
with "group X does not exist" or run against an unrelated group. If we ever
want per-GPU isolation, use `-i`.

### 1.2 Run levels — what each one means

Per NVIDIA "DCGM Diagnostics" (`/datacenter/dcgm/latest/user-guide/dcgm-diagnostics.html`)
and corroborated by the `ml-engineering` debug guide:

| Level | Name | Wall time | Recommended use | Confidence |
|---|---|---|---|---|
| `r1` | "Short" | seconds (typ. 30 s) | **Pre-flight quick check.** Software/deployment tests only — driver, libs, permissions, persistence mode, page retirement, Inforom. Does not stress hardware. | High |
| `r2` | "Medium" | < 2 minutes | Standard health probe. Adds PCIe + NVLink integration tests and basic GPU memory test. The level the `ml-engineering` guide and Stas Bekman's debug doc recommend for SLURM job epilogue. | High |
| `r3` | "Long" | < 30 minutes (≈10 min on 8×H100) | Deep validation. Adds SM stress, targeted stress, targeted power, memory bandwidth. NVIDIA's recommendation for "post-incident" / RMA-prep. | High |
| `r4` | "Extra Long" | 1–2 hours (Dell quotes ~1 h 30 m) | Comprehensive. Adds Memtest and Pulse Test (EDPp). Field-engineer level. | High |

**Recommendation for scanprobe pre-flight:**

- `r1` is too narrow for a real health check — it's essentially a software
  inventory and will pass on a GPU that has DBEs in flight, because the
  hardware tests don't run at level 1. The current scanprobe default of `r1`
  matches what the docstring says, but it's a weak signal.
- `r2` is the right default for a pre-flight run: < 2 min, includes PCIe and
  basic memory test. This is the level [stas00/ml-engineering's debug
  guide](https://github.com/stas00/ml-engineering/blob/master/compute/accelerator/nvidia/debug.md)
  recommends as a SLURM epilogue.
- `r3` is appropriate when the user opts into a "thorough" tier and accepts
  ~10 min wall time per node.
- `r4` is out of scope for pre-flight — keep it behind an explicit `--deep` flag.

### 1.3 Output format

#### Plain text (default)

Source: `Diag.cpp` master branch + DCGMI VirtualClient docs + ml-engineering. Confidence: **High** for the structural elements; **Medium** for the exact column widths (those vary by terminal width).

The text output is a four-section table. Section headers are literal strings
in `Diag.cpp`:

```
+---------------------------+------------------------------------------------+
| Diagnostic                | Result                                         |
+===========================+================================================+
|-----  Metadata  ----------+------------------------------------------------|
| DCGM Version              | 3.3.5                                          |
| Driver Version Detected   | 545.23.08                                      |
| GPU Device IDs Detected   | 0,1,2,3,4,5,6,7                                |
+-----  Deployment  --------+------------------------------------------------+
| Denylist                  | Pass                                           |
| NVML Library              | Pass                                           |
| CUDA Main Library         | Pass                                           |
| Permissions and OS Blocks | Pass                                           |
| Persistence Mode          | Pass                                           |
| Environment Variables     | Pass                                           |
| Page Retirement/Row Remap | Pass                                           |
| Graphics Processes        | Pass                                           |
| Inforom                   | Pass                                           |
+-----  Integration  -------+------------------------------------------------+
| PCIe                      | Pass - All                                     |
+-----  Hardware  ----------+------------------------------------------------+
| GPU Memory                | Pass - All                                     |
| Diagnostic                | Pass - All                                     |
+-----  Stress  ------------+------------------------------------------------+
| Targeted Stress           | Pass - All                                     |
| Targeted Power            | Pass - All                                     |
| Memory Bandwidth          | Pass - All                                     |
+---------------------------+------------------------------------------------+
Successfully ran diagnostic for group.
```

(Composed from NVIDIA's published example screenshots and the
[microsoft.github.io/VirtualClient/docs/workloads/dcgmi](https://microsoft.github.io/VirtualClient/docs/workloads/dcgmi/)
reproduction; the exact column widths vary by DCGM version.)

Result tokens (verbatim from `Diag.cpp`, `DcgmiResult::Display()`):

```cpp
case DCGM_DIAG_RESULT_PASS:    return "Pass";
case DCGM_DIAG_RESULT_SKIP:    return "Skip";
case DCGM_DIAG_RESULT_WARN:    return "Warn";
case DCGM_DIAG_RESULT_FAIL:    return "Fail";
case DCGM_DIAG_RESULT_NOT_RUN: return "Not Run";
```

Failure example (composed; see ml-engineering and DCGM issue #139 for real
fragments):

```
+-----  Hardware  ----------+------------------------------------------------+
| GPU Memory                | Fail - GPU 3                                   |
|   GPU 3                   | Memory error: ECC DBE detected at addr 0x...   |
+-----  Stress  ------------+------------------------------------------------+
| Targeted Stress           | Skip - GPU 3                                   |
| Memory Bandwidth          | Pass - All                                     |
+---------------------------+------------------------------------------------+
```

Per-GPU result lines use the form `Pass - All`, `Pass - GPU N`, `Fail - GPU N`,
or `Skip - GPU N`. A test that fails on any GPU emits a `Fail - GPU N` line
**plus** an indented detail line beneath it.

#### JSON (`-j` / `--json`)

Confidence: **High** for top-level shape, **Medium** for stable field names
(NVIDIA has bumped these between 2.x and 3.x).

Sample shape (from issue #139 and Microsoft VirtualClient parser):

```json
{
  "DCGM GPU Diagnostic": {
    "version": "3.3.1",
    "Driver Version Detected": "545.23.08",
    "GPU Device IDs": ["0", "1", "..."],
    "GPU Device Serials": {"0": "1423923001498"},
    "test_categories": [
      {
        "category": "Deployment",
        "tests": [
          {"name": "Denylist", "results": [{"gpu_id": "0", "status": "Pass"}]},
          ...
        ]
      },
      {
        "category": "Integration",
        "tests": [{"name": "PCIe", "results": [{"gpu_id": "0", "status": "Pass"}]}]
      },
      {
        "category": "Hardware",
        "tests": [
          {"name": "GPU Memory", "results": [{"gpu_id": "0", "status": "Pass"}]},
          {"name": "Diagnostic", "results": [...]},
          {"name": "EUD Test",   "results": [...]}
        ]
      },
      {
        "category": "Stress",
        "tests": [
          {"name": "Targeted Stress", "results": [...]},
          {"name": "Targeted Power",  "results": [...]},
          {"name": "Memory Bandwidth", "results": [...]}
        ]
      }
    ]
  }
}
```

A `results[i].status` of `"Fail"` carries a sibling `"warnings"` array with
the human-readable reason. JSON is strictly preferable to text grep for
programmatic use — it gives stable per-test, per-GPU resolution.

### 1.4 Test plugins

From the latest NVIDIA Diagnostics docs page, in plugin order:

| Plugin / test name | Category | r1 | r2 | r3 | r4 | What it catches | Confidence |
|---|---|:-:|:-:|:-:|:-:|---|---|
| Deployment (Denylist, NVML, CUDA, Permissions, Persistence, EnvVars, Page Retirement, Graphics Procs, Inforom) | Deployment | ✓ | ✓ | ✓ | ✓ | Software prerequisites, kernel module sanity, persistence-mode misconfig, page-retirement quota | High |
| PCIe + NVLink | Integration |   | ✓ | ✓ | ✓ | Bus errors, link width/gen mismatch, NVLink topology faults | High |
| GPU Memory (basic) | Hardware |   | ✓ | ✓ | ✓ | Quick framebuffer integrity probe; will surface DBEs already accumulated | High |
| Memory Bandwidth | Hardware/Stress |   | ✓ | ✓ | ✓ | Bandwidth below SKU spec → suggests memory or PCIe degradation | High |
| Diagnostic (SM stress) | Hardware |   |   | ✓ | ✓ | SM compute correctness under load | High |
| Targeted Stress | Stress |   |   | ✓ | ✓ | Sustained gigaflops at target — catches throttling, power-delivery faults | High |
| Targeted Power | Stress |   |   | ✓ | ✓ | Hits target wattage — catches power-supply / VRM weakness (the test that infamously fails on under-spec server power: see Dell A2 KB) | High |
| Memtest (extended) | Hardware |   |   |   | ✓ | Multi-pattern HBM stress; long | High |
| Pulse Test / EDPp | Hardware |   |   |   | ✓ | Inductive-current-spike test; H100-class | High |
| EUD Test | Hardware |   |   | ✓* | ✓ | "End-User Diagnostic" — NVIDIA's RMA-equivalent field-diag (DCGM 3.3+); requires explicit opt-in via `is_allowed=true` config | High |
| NCCL Tests, NVBandwidth | Multi-GPU |   |   |   | ✓ | Multi-node / multi-GPU collective correctness | High |

(✓\* = present in r3 only when `nvidia-validation-suite/diag-skus.yaml`
allows it; see issue #139.)

Mapping back to the failure-category framing scanprobe uses:

- **DRAIN-class** → any `Fail` in *Hardware* category (GPU Memory, Diagnostic,
  EUD), or `Fail` in *Integration* with PCIe link-width fault, or "Memory
  error" / "ECC DBE" in detail strings.
- **WATCH-class** → `Fail` in *Stress* category (Targeted Stress, Targeted
  Power, Memory Bandwidth) — these can be transient (background load, power
  cap), so retry once before draining.
- **INFO** → `Skip` results (typically MIG-mode incompatibility per issue #70,
  or missing config per issue #139).

### 1.5 Exit codes

Confidence: **Medium**. NVIDIA does not publish a stable exit-code table.

From `Diag.cpp::GetFailureResult()` and observed behavior:

| Exit code | Meaning |
|---|---|
| `0` | All tests `Pass` (or `Skip`/`Not Run`). |
| Non-zero | At least one test produced `Fail` or `Warn`, **or** an internal DCGM error (`DCGM_ST_NVVS_ERROR`, `DCGM_ST_NVVS_ISOLATE_ERROR`, `DCGM_ST_NVVS_KILLED`). |
| `226` | Reported by issue #158 for an internally-killed run (likely `DCGM_ST_NVVS_KILLED` or signal trap). Do not rely on the specific number. |

**Implication:** the exit code alone is enough to decide pass/fail at the
*node* level, but you must parse the output (text or JSON) to know **which
test** failed and **which GPU**. scanprobe's current "non-zero → fail" branch
is correct as a coarse signal.

### 1.6 Service requirements

| Finding | Detail | Confidence |
|---|---|---|
| `dcgmi diag` needs an nv-hostengine to talk to | Either an embedded one (default if no service is running) or `nv-hostengine` started via `systemctl start nvidia-dcgm`. | High |
| In containers without root / without privileged mode, dcgmi may fail to embed | Common cloud-image gotcha. The `nvcr.io/nvidia/dcgm` image is the canonical workaround. | Medium |
| dcgmi requires `CAP_SYS_ADMIN` for some hardware tests at r3/r4 | Reported intermittently in DCGM issue tracker; not a problem for r1/r2. | Medium |

**Cloud-image notes:**

- `nvcr.io/nvidia/pytorch:*` ships **without** `dcgmi`. You'd have to install
  the `datacenter-gpu-manager` package and pull `dcgmi` in.
- `nvcr.io/nvidia/dcgm:*` (separate image) has it pre-installed.
- Most GKE / EKS / Lambda H100 / CoreWeave nodes already run
  `nvidia-dcgm.service` — just call `dcgmi diag` directly.

scanprobe's current "graceful degrade if `dcgmi` not found" is correct
behavior. Worth surfacing the install hint in the report.

### 1.7 Recommended changes to `ashiba_scanprobe/checks/dcgm.py`

Confidence: **High** for syntax changes, **Medium** for default-level shift
(this is a product call, not a correctness call).

1. **Switch default level from r1 → r2** for the standard tier. Update
   `LEVEL_DESCRIPTIONS` to match real NVIDIA timings:
   ```python
   LEVEL_DESCRIPTIONS = {
       1: "software-only (~30s, no hardware tests)",
       2: "quick hardware (<2min, +PCIe +memory)",
       3: "stress (~10-30min, +SM/Power/Bandwidth)",
       4: "exhaustive (1-2hr, +Memtest +Pulse)",
   }
   LEVEL_TIMEOUTS = {1: 120, 2: 360, 3: 2400, 4: 9000}
   ```
2. **Use `--json` output and parse it.** Replace the regex-grep with
   `json.loads(proc.stdout)` and walk `test_categories[].tests[].results[]`.
   This gives reliable per-GPU per-test resolution and survives column-width
   changes.
   ```python
   proc = subprocess.run(
       ["dcgmi", "diag", "-r", str(level), "-j"],
       capture_output=True, text=True, timeout=timeout
   )
   doc = json.loads(proc.stdout).get("DCGM GPU Diagnostic", {})
   for cat in doc.get("test_categories", []):
       for test in cat.get("tests", []):
           for res in test.get("results", []):
               if res["status"] == "Fail":
                   failed_tests.append((cat["category"], test["name"],
                                        res["gpu_id"], res.get("warnings", [])))
   ```
3. **Categorize failures** into DRAIN/WATCH classes per §1.4. Surface the
   category name + test name (e.g. `"Hardware: GPU Memory"`) rather than the
   raw "fail" line.
4. **Per-GPU isolation:** keep the default of running across all GPUs (correct
   for node-level pre-flight), but expose a `gpus: list[int] | None` arg that
   maps to `-i 0,1,2`. Drop any code that uses `-g`.
5. **Document the embedded-vs-standalone distinction** in the docstring so
   users understand why dcgmi can fail in containers without `nvidia-dcgm.service`.
6. **Don't treat `Warn` as fail by default.** `Warn` is DCGM's "this looked
   borderline" signal — surface as a scanprobe `WATCH`, not a `DRAIN`.
7. **Skip the `re.search(r"\bfail\b", ...)` heuristic entirely** once JSON
   parsing is in. Today it false-positives on phrases like "no failures
   detected" or "page-retirement test will fail if..." in the explanation
   text.

---

## 2. HBM row remapping

### 2.1 Why this is a Tier-1 signal

Row remapping is NVIDIA's HBM3/HBM2e "spare-row substitution" mechanism on
A100 / H100 / H200 / B100. When a row accumulates correctable errors above
threshold or hits an uncorrectable error, the GPU schedules a row remap and
then applies it on the next reset. Three states matter:

| State | Meaning | Pre-flight action |
|---|---|---|
| `Pending: Yes` | Remap queued but not applied. Workload will keep hitting the bad row until reset. | **DRAIN + reset** — restart the GPU before scheduling work. |
| `Remapping Failure Occurred: Yes` | Hardware exhausted spares (8 per bank, or 512 total, or duplicate-row remap attempt). | **DRAIN permanent — RMA candidate.** Field-diag confirms RMA. |
| Rising `Uncorrectable Error` count | DBE precursors to fail-stop. | **WATCH** — track over time, surface trend. |

scanprobe today reads `ecc.errors.uncorrected.aggregate.total` from the
`--query-gpu` CSV but does **not** read the row-remapper state. That's a
material gap: a node can have `Pending: Yes` and zero new DBEs since boot,
and the existing check would call it healthy.

Confidence: **High** — sourced from
`docs.nvidia.com/deploy/a100-gpu-mem-error-mgmt/rma-policy-thresholds-for-row-remapping.html`
and `.../row-remapping.html`.

### 2.2 The `nvidia-smi -q -d ROW_REMAPPER` output

Verbatim healthy-with-correctable example (from
[stas00/ml-engineering debug guide](https://github.com/stas00/ml-engineering/blob/master/compute/accelerator/nvidia/debug.md),
matches NVIDIA A100 doc):

```
==============NVSMI LOG==============

Timestamp                                 : ...
Driver Version                            : 545.23.08
CUDA Version                              : 12.3

Attached GPUs                             : 8
GPU 00000000:0F:00.0
    Remapped Rows
        Correctable Error                 : 1
        Uncorrectable Error               : 0
        Pending                           : No
        Remapping Failure Occurred        : No
        Bank Remap Availability Histogram
            Max                           : 639 bank(s)
            High                          : 1 bank(s)
            Partial                       : 0 bank(s)
            Low                           : 0 bank(s)
            None                          : 0 bank(s)
```

Pending example (DRAIN+reset case):

```
    Remapped Rows
        Correctable Error                 : 0
        Uncorrectable Error               : 1
        Pending                           : Yes
        Remapping Failure Occurred        : No
        Bank Remap Availability Histogram
            Max                           : 638 bank(s)
            High                          : 2 bank(s)
            Partial                       : 0 bank(s)
            Low                           : 0 bank(s)
            None                          : 0 bank(s)
```

Failure example (RMA candidate):

```
    Remapped Rows
        Correctable Error                 : 12
        Uncorrectable Error               : 8
        Pending                           : No
        Remapping Failure Occurred        : Yes
        Bank Remap Availability Histogram
            Max                           : 0 bank(s)
            High                          : 0 bank(s)
            Partial                       : 0 bank(s)
            Low                           : 1 bank(s)
            None                          : 639 bank(s)
```

Confidence: **High** for the field schema (NVIDIA doc), **Medium** for the
exact bank histogram numbers in the failure case (composed from NVIDIA's
description; the extreme-failure histogram I have not seen pasted verbatim).

### 2.3 Field-by-field interpretation

| Field | Meaning | Threshold | Confidence |
|---|---|---|---|
| `Correctable Error` | Rows remapped because of *single-bit* (correctable) ECC accumulation. Each row reflects one already-substituted row. | Watch the *trend*; no single value is fail-stop. NVIDIA does not publish a hard threshold. Common cluster-ops practice: flag if increases by >2/day. | High (definition) / Medium (threshold) |
| `Uncorrectable Error` | Rows remapped because of *double-bit* (uncorrectable) ECC. Each one means a DBE happened and the GPU walled off the row. | Any non-zero is a real fault history. Combined with `Pending: Yes`, it's a current fault. | High |
| `Pending` | A remap has been queued by hardware but not applied (requires GPU reset / `nvidia-smi -r`). The bad row is *still in use*. | `Yes` ⇒ DRAIN+reset before next workload. | High |
| `Remapping Failure Occurred` | One of three triggers fired: (a) 9th uncorrectable remap on a bank, (b) duplicate-row remap attempt, (c) 512 lifetime uncorrectable remaps. | `Yes` ⇒ DRAIN permanent. NVIDIA Field Diagnostic confirms RMA. | High |
| `Bank Remap Availability Histogram: Max` | Banks with all spares intact. A100 has 640 banks per chip, so healthy = `Max: 640` (or 639 with one spare consumed). H100 differs; treat trend, not absolute. | Drop in `Max` over time = depletion. | High (mechanism) / Low (per-SKU absolutes) |
| `... High / Partial / Low / None` | Banks at progressively lower spare-row availability. | `None > 0` = at least one bank fully depleted; very close to RMA. | High |

### 2.4 CSV-pull integration — can we get this in `--query-gpu`?

**Yes.** The `nvidia-smi --query-gpu=...` interface exposes:

| CSV field | Maps to ROW_REMAPPER field | Confidence |
|---|---|---|
| `remapped_rows.correctable` | `Correctable Error` | High (`man nvidia-smi`) |
| `remapped_rows.uncorrectable` | `Uncorrectable Error` | High |
| `remapped_rows.pending` | `Pending` (returns "Yes"/"No") | High |
| `remapped_rows.failure` | `Remapping Failure Occurred` (returns "Yes"/"No") | High |

This means scanprobe can fold remapper state into the **same** single
`nvidia-smi --query-gpu=...` CSV call it already uses in `nvidia_smi.py` —
no extra subprocess invocation needed.

The bank histogram is **not** exposed via `--query-gpu`. If the histogram is
needed, fall back to `nvidia-smi -q -d ROW_REMAPPER` and parse text. For
pre-flight scoring, the four scalar fields are sufficient.

### 2.5 Scoring weights

Confidence: **High** on category boundaries; **Medium** on numeric weights
(those are scanprobe-specific tuning).

| Condition | Class | Rationale |
|---|---|---|
| `remapped_rows.failure == "Yes"` | **DRAIN** (terminal) | NVIDIA-defined RMA flag. Per [RMA Policy](https://docs.nvidia.com/deploy/a100-gpu-mem-error-mgmt/rma-policy-thresholds-for-row-remapping.html), this means hardware can no longer self-heal. Field Diagnostic will confirm. Do not assign work; mark for return. |
| `remapped_rows.pending == "Yes"` | **DRAIN + reset** (recoverable) | The bad row is currently in the address space; any allocation that lands on it gets a DBE. Reset (`nvidia-smi -r -i N`) applies the queued remap. After reset, recheck — if `Pending` flips to `No` and `Failure` stays `No`, GPU is healthy. |
| `remapped_rows.uncorrectable >= 1` and `pending == No` and `failure == No` | **WATCH** | Lifetime DBE history exists, but hardware compensated. Worth tracking trend. NVIDIA does not flag this as RMA. |
| `remapped_rows.correctable` rising fast (rate, not absolute) | **WATCH / monitor** | SBE-driven remaps are normal aging; only the *rate* matters. scanprobe is one-shot, so this is best surfaced as INFO with the count. |
| Bank histogram `None > 0` | **WATCH** (precursor) | At least one bank fully out of spares. Next DBE in that bank trips `Failure`. | 

### 2.6 NVIDIA's own threshold language

The only NVIDIA-published numeric thresholds are for the *failure flag*, not
for action on the count fields. Verbatim from
[RMA Policy thresholds](https://docs.nvidia.com/deploy/a100-gpu-mem-error-mgmt/rma-policy-thresholds-for-row-remapping.html):

> The row-remapping failure flag is set when:
> - a remapping attempt for an uncorrectable memory error on a bank that
>   already has eight uncorrectable error rows remapped, **or**
> - a remapping attempt for an uncorrectable memory error on a row that was
>   already remapped (can occur with fewer than 8 total remaps to the bank),
>   **or**
> - after 512 total remappings for an uncorrectable memory error have
>   occurred.

Implication for scanprobe: **don't second-guess NVIDIA's flag.** When
`remapped_rows.failure == "Yes"`, treat it as terminal. Don't try to recompute
the threshold from the count fields — the duplicate-row trigger fires below
the 8-per-bank limit and you can't see it from the counts.

### 2.7 Recommended addition to `ashiba_scanprobe/checks/nvidia_smi.py`

Confidence: **High** for the code shape (it's a mechanical extension of the
existing CSV pull).

1. **Add four fields to `_FIELDS`:**
   ```python
   _FIELDS = ",".join([
       ...existing...,
       "remapped_rows.correctable",
       "remapped_rows.uncorrectable",
       "remapped_rows.pending",
       "remapped_rows.failure",
   ])
   ```
   This stays a single subprocess call.

2. **Add four fields to `NvidiaSmiResult`:**
   ```python
   remap_correctable: int = 0
   remap_uncorrectable: int = 0
   remap_pending: bool = False        # "Yes" / "No"
   remap_failure: bool = False        # "Yes" / "No" -- RMA flag
   ```

3. **Parse in `_parse_line()`:**
   ```python
   def _parse_yesno(s: str) -> bool:
       return s.strip().lower() == "yes"
   ...
   result.remap_correctable    = _parse_int(parts[19])
   result.remap_uncorrectable  = _parse_int(parts[20])
   result.remap_pending        = _parse_yesno(parts[21])
   result.remap_failure        = _parse_yesno(parts[22])
   ```
   (Update the `< 19` column-count guard to `< 23`.)

4. **Add scoring stanzas after the existing ECC block:**
   ```python
   if result.remap_failure:
       result.passed = False
       result.warnings.append(
           "HBM row-remap FAILURE flag set — RMA candidate "
           "(NVIDIA: 8 remaps per bank, or 512 total, or duplicate-row remap)."
       )
   if result.remap_pending:
       result.passed = False
       result.warnings.append(
           "HBM row-remap PENDING — bad row still in use; "
           "GPU reset required (nvidia-smi -r -i N) before scheduling work."
       )
   if result.remap_uncorrectable > 0 and not (result.remap_failure or result.remap_pending):
       result.warnings.append(
           f"HBM row-remap history: {result.remap_uncorrectable} uncorrectable, "
           f"{result.remap_correctable} correctable — hardware compensated."
       )
   ```

5. **Tier-1 promotion.** This is a free signal — same subprocess, four extra
   columns, well-documented thresholds. Add to the default tier and surface
   `remap_failure` / `remap_pending` in the per-GPU report alongside DBE
   counts.

6. **Pre-Ampere graceful handling.** `remapped_rows.*` returns `[N/A]` on
   pre-A100 GPUs (V100, T4, etc.). The existing `_parse_int` already returns
   `0` for `[N/A]` and `_parse_yesno` returns `False`, so old-GPU hosts will
   silently report no remapping state — correct.

---

## 3. Gaps and follow-ups

| Gap | Why it matters | Confidence on gap |
|---|---|---|
| Real verbatim `dcgmi diag --json` output for a **failure** case is hard to find. I composed the failure example from the schema in NVIDIA docs + issue #139. Worth grabbing a real dump on first H100 run and pinning it in `tests/fixtures/`. | Tests should validate the parser against real output, not synthesized. | High |
| H100/B100 bank-histogram absolute counts (the A100 number is 640). NVIDIA does not publish per-SKU bank totals. | Affects how to interpret "Max: N" — but the scoring above only reads it as a trend, not absolute, so safe. | Medium |
| The `remapped_rows.failure` CSV field name — confirmed in `man nvidia-smi` and corroborated by Hexmos and ManKier mirrors, but I've not run it on a live H100 to verify the exact spelling. Possibility it's `remapped_rows.failure_occurred` on some driver versions. **Action: probe with `nvidia-smi --help-query-gpu | grep -i remap` on the first deploy host.** | Wrong field name → CSV breaks silently (column count off). | Medium |
| Whether DCGM 4.x changes the JSON envelope key from `"DCGM GPU Diagnostic"`. NVIDIA has churned this between major versions before. | Parser stability across DCGM upgrades. **Action: support both legacy and new envelope keys; fall back to text if JSON unparsable.** | Medium |
| ByteRobust paper §4.1 reference in the original task brief — I did not pull this paper. If it has cluster-scale remapping incidence data, worth folding into the WATCH-vs-DRAIN tuning. | Better empirical thresholds than NVIDIA's binary flag. | Low |

---

## 4. Citations

DCGM:
- [NVIDIA DCGM Diagnostics (latest)](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/dcgm-diagnostics.html)
- [NVIDIA DCGM Diagnostics (3.1)](https://docs.nvidia.com/datacenter/dcgm/3.1/user-guide/dcgm-diagnostics.html)
- [DCGM repo — Diag.cpp on master](https://github.com/NVIDIA/DCGM/blob/master/dcgmi/Diag.cpp)
- [DCGM issue #70 — `-i` vs `-g`](https://github.com/NVIDIA/DCGM/issues/70)
- [DCGM issue #139 — skipped tests (real JSON output)](https://github.com/NVIDIA/DCGM/issues/139)
- [DCGM issue #158 — exit code 226](https://github.com/NVIDIA/DCGM/issues/158)
- [Microsoft VirtualClient DCGMI workload doc](https://microsoft.github.io/VirtualClient/docs/workloads/dcgmi/)
- [Dell PowerEdge DCGM install + diag KB](https://www.dell.com/support/kbdoc/en-us/000219485/nvidia-dcgm-datacenter-gpu-manager-install)
- [Dell A2 GPU Targeted Power test KB](https://www.dell.com/support/kbdoc/en-my/000223776/gpu-nvidia-data-center-gpu-manager-dcgm-may-fail-power-tests-when-running-against)

Row remapping / memory error management:
- [NVIDIA GPU Memory Error Management — index](https://docs.nvidia.com/deploy/a100-gpu-mem-error-mgmt/index.html)
- [Row Remapping](https://docs.nvidia.com/deploy/a100-gpu-mem-error-mgmt/row-remapping.html)
- [RMA Policy: Thresholds for Row Remapping](https://docs.nvidia.com/deploy/a100-gpu-mem-error-mgmt/rma-policy-thresholds-for-row-remapping.html)
- [User Visible Statistics](https://docs.nvidia.com/deploy/a100-gpu-mem-error-mgmt/user-visible-statistics.html)
- [Error Recovery and Response Flags](https://docs.nvidia.com/deploy/a100-gpu-mem-error-mgmt/error-recovery-and-response-flags.html)
- [NVIDIA GPU Debug Guidelines](https://docs.nvidia.com/deploy/gpu-debug-guidelines/index.html)
- [nvidia-smi man page (Arch)](https://man.archlinux.org/man/nvidia-smi.1.en)
- [stas00/ml-engineering — NVIDIA debug guide](https://github.com/stas00/ml-engineering/blob/master/compute/accelerator/nvidia/debug.md)
- [saforem2/ml-engineering — Troubleshooting NVIDIA GPUs](https://saforem2.github.io/ml-engineering/qmd/compute/accelerator/nvidia/debug.html)
- [AWS re:Post — Xid error troubleshooting](https://repost.aws/knowledge-center/ec2-linux-troubleshoot-xid-errors)
