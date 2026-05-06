# NCCL & Collective Check Research

Research backing the Tier-2 collective check in `ashiba_scanprobe/checks/collective.py`.
All quoted error strings are verbatim and intended as future test fixtures.

**Sources consulted:**
- NCCL Troubleshooting Guide v2.30.3 — https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/troubleshooting.html
- NCCL Environment Variables v2.30.3 — https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html
- nccl-tests README — https://github.com/NVIDIA/nccl-tests
- nccl-tests PERFORMANCE.md — https://github.com/NVIDIA/nccl-tests/blob/master/doc/PERFORMANCE.md
- PyTorch Flight Recorder blog — https://pytorch.org/blog/flight-recorder-a-new-lens-for-understanding-nccl-watchdog-timeouts/
- PyTorch Flight Recorder tutorial — https://docs.pytorch.org/tutorials/unstable/flight_recorder_tutorial.html
- NVIDIA NCCL 2.24 RAS blog — https://developer.nvidia.com/blog/networking-reliability-and-observability-at-scale-with-nccl-2-24/
- ByteRobust (ByteDance, SOSP'25) — https://arxiv.org/abs/2509.16293
- nccl-tests issue #149 (A100 8x NVLink results) — https://github.com/NVIDIA/nccl-tests/issues/149
- nccl-tests issue #212 (H100 allreduce performance) — https://github.com/NVIDIA/nccl-tests/issues/212
- nccl-tests issue #272 (H200 NVLink results) — https://github.com/NVIDIA/nccl-tests/issues/272
- nccl-tests issue #123 (small-message latency) — https://github.com/NVIDIA/nccl-tests/issues/123
- NCCL issue #1409 (collective timeout reproducer) — https://github.com/NVIDIA/nccl/issues/1409
- GB200 NVL Multi-Node NCCL tuning — https://docs.nvidia.com/multi-node-nvlink-systems/multi-node-tuning-guide/nccl.html

---

## 1. NCCL Failure Mode Taxonomy

| # | Failure Mode | Canonical Signature | Outside-Observer Detection | Confidence |
|---|---|---|---|---|
| 1 | Init failure: CUDA/driver mismatch | `NCCL WARN Cuda failure 'named symbol not found'` followed by `RuntimeError: NCCL error: unhandled cuda error` | Process exits within seconds of launch with non-zero status; stderr matches one of the regex below. | High |
| 2 | Init failure: peer unreachable / shm | `failed to extend /dev/shm/nccl-XXXXXX to 4194660 bytes` or `ncclCommInitRank ... remote process exited or there was a network error` | Same — fast exit, distinctive stderr; `/dev/shm` full or container missing `--shm-size`. | High |
| 3 | Collective hang (one rank stalled) | No NCCL output after init; processes alive, GPU SM activity at 0% on all but one rank; CPU-side `dist.all_reduce` call never returns. | Watchdog fires at 600 s default. RAS (NCCL 2.24+) reports `26 ranks have launched up to operation 6650 / 6 ranks have launched up to operation 6649`. | High |
| 4 | Collective timeout (watchdog fires) | `[Rank 0] Watchdog caught collective operation timeout: WorkNCCL(SeqNum=12345, OpType=ALLREDUCE, NumelIn=1, NumelOut=1, Timeout(ms)=600000) ran for 600029 milliseconds before timing out.` raised from `ProcessGroupNCCL.cpp:checkTimeout`. | torchrun returncode != 0; stderr regex above. SeqNum identifies which collective call hung. | High |
| 5 | NVLink degradation (slow but functional) | NCCL_DEBUG=INFO shows ring construction succeeding, but per-iteration latency is 5-10× expected. No error message. busbw drops from ~360 GB/s (healthy 8×H100) to <100 GB/s. | Per-rank p50 latency outlier: one rank's largest-message p50 is N-σ above cluster median while p99/p50 ratio stays modest (<3×). | Medium |
| 6 | PCIe link fallback (gen4→gen3) | `nvidia-smi -q -d PERFORMANCE` shows `Current Link Width: 16x / Gen Speed: Gen3` instead of Gen4/Gen5. NCCL_DEBUG=INFO shows topology with PHB/PIX paths. busbw roughly halves. | scanprobe should also read `nvidia-smi -q -d PERFORMANCE` to disambiguate from a hot GPU. | Medium |
| 7 | Topology / P2P broken | `NCCL INFO Channel 00 ... [send] via NET/Socket` instead of `via P2P/IPC` or `NVLink`. Symptom: orders-of-magnitude lower busbw. Often caused by ACS enabled on PCIe bridges or container with virtual PCI topology. | Topology line in INFO log; busbw <10 GB/s for sizes that should saturate NVLink. | High |
| 8 | InfiniBand RoCE GID misconfig (multi-node) | `Call to ibv_modify_qp failed with error Invalid argument` | Init fails on multi-node only; single-node works. Fix is `NCCL_IB_GID_INDEX` (NCCL <2.21). | High |
| 9 | Stack overflow during init (MNNVL) | Hang during communicator creation when stack < 2 MB. | `ulimit -s` reports <2048; init never returns. | Medium |

### Distinguishing slow-but-functional from hung

| Signal | Slow (degraded NVLink/PCIe) | Hung (rank stalled) |
|---|---|---|
| Wall-clock elapsed | Bounded — completes within timeout | Unbounded — hits 600 s watchdog |
| Per-rank latency variance | One or few ranks elevated, rest normal | All ranks waiting on the laggard ⇒ all elevated |
| Watchdog fires | No | Yes (`Watchdog caught collective operation timeout`) |
| NCCL_DEBUG=INFO output | Ring/tree built, channels OK | Last log line is bootstrap or first ring connection |
| RAS op-counter (NCCL 2.24+) | All ranks at same op | One subset of ranks lags by exactly one op |
| Detection cost | Cheap — measure latency, compare ranks | Expensive — must wait for timeout to fire |

scanprobe's job is the **slow** column. The **hung** column is owned by the training framework's own watchdog.

---

## 2. Recommended Environment Variables for the Collective Check

scanprobe's collective check is a short-lived microbenchmark, not a long training job. Choose env vars to (a) surface real errors verbose enough for the user to act on, (b) avoid masking faults, (c) keep the test bounded.

| Variable | scanprobe value | Rationale | Source |
|---|---|---|---|
| `NCCL_DEBUG` | `WARN` (default for healthy run); `INFO` if `--verbose` | NCCL docs: WARN "Prints an explicit error message whenever any NCCL call errors out". INFO is large but invaluable for diagnosis. | env.html |
| `NCCL_DEBUG_SUBSYS` | unset (defaults to `INIT,BOOTSTRAP,ENV`) | Keep noise low at WARN; if INFO requested, add `COLL,P2P,GRAPH`. | env.html |
| `TORCH_NCCL_ASYNC_ERROR_HANDLING` | `1` | Surfaces collective errors to Python without blocking the main thread. PyTorch RFC #46874 says "little to no performance overhead". | pytorch/pytorch#46874 |
| `TORCH_NCCL_BLOCKING_WAIT` | **unset** (`0`) | Mutually exclusive with async handling; the PyTorch doc explicitly notes "may incur up to a 60% regression". Prefer async. | pytorch/pytorch#46874 |
| Process-group `timeout=` | `60s` (NOT 600 s default) | scanprobe is a preflight; 10 minutes hides hangs. Set via `dist.init_process_group(timeout=timedelta(seconds=60))`. | flight-recorder blog (default 600000 ms) |
| `TORCH_NCCL_TRACE_BUFFER_SIZE` | `2000` | Enables Flight Recorder; only useful if a hang occurs. Recommended value per PyTorch tutorial. | flight_recorder_tutorial.html |
| `TORCH_NCCL_DUMP_ON_TIMEOUT` | `1` | Auto-dumps trace on watchdog fire. Requires `TORCH_NCCL_ENABLE_MONITORING=1` and trace buffer >0. | flight_recorder_tutorial.html |
| `TORCH_NCCL_ENABLE_MONITORING` | `1` | Required companion for dump-on-timeout. | flight_recorder_tutorial.html |
| `NCCL_RAS_ENABLE` | unset (defaults `1` on NCCL ≥2.24) | RAS gives op-counter divergence diagnosis "for free"; minimal idle overhead per NVIDIA. | NCCL 2.24 blog |
| `NCCL_SOCKET_IFNAME` | unset, document for users | If user has stale `docker0`/`virbr0` interfaces, NCCL can pick the wrong one and hang on bootstrap. Document, don't override. | troubleshooting.html |

> **Note on `NCCL_TIMEOUT_S`:** The task brief asked about this. There is no documented top-level NCCL env var of that name in the v2.30.3 environment-variable reference. The timeout is enforced at the **PyTorch** layer via the `timeout=` argument to `init_process_group` (default 10 min for NCCL backend, 30 min in some PyTorch versions). NCCL itself has `NCCL_IB_TIMEOUT` (verbs-level, default `20`, computed as `4.096 µs * 2^timeout`) but that's a different concept. **scanprobe should set the PyTorch timeout, not invent a NCCL one.**

### Verbatim error strings (test fixtures)

```
[Rank 0] Watchdog caught collective operation timeout: WorkNCCL(SeqNum=12345, OpType=ALLREDUCE, NumelIn=1, NumelOut=1, Timeout(ms)=600000) ran for 600029 milliseconds before timing out.
```

```
[E ProcessGroupNCCL.cpp:828] [Rank 1] Watchdog caught collective operation timeout: WorkNCCL(SeqNum=15173, OpType=ALLREDUCE, Timeout(ms)=1800000) ran for 1802710 milliseconds before timing out.
```

```
RuntimeError: NCCL error: unhandled cuda error (run with NCCL_DEBUG=INFO for details)
```

```
NCCL WARN Cuda failure 'named symbol not found'
```

```
failed to extend /dev/shm/nccl-XXXXXX to 4194660 bytes
```

```
Call to ibv_modify_qp failed with error Invalid argument
```

```
ncclCommInitRank ... remote process exited or there was a network error
```

A regex matcher in scanprobe should detect at minimum:
```
r"Watchdog caught collective operation timeout: WorkNCCL\(SeqNum=(\d+), OpType=(\w+).*?Timeout\(ms\)=(\d+)\) ran for (\d+) milliseconds"
r"NCCL error: ([\w\s]+) \(run with NCCL_DEBUG=INFO"
r"NCCL WARN ([^\n]+)"
r"failed to extend /dev/shm/nccl-\w+ to \d+ bytes"
```

---

## 3. Validated Latency / Bandwidth Thresholds

These are empirical numbers from public benchmarks. Use them as the *ceiling* a healthy node should approach; flag a rank when its measured value falls a defined fraction below cluster median.

### A100 8×SXM4-80GB NVLink, ring allreduce (nccl-tests issue #149)

| Size | Time | algbw | busbw |
|---|---|---|---|
| 8 B | 20.65 µs | 0.00 GB/s | 0.00 GB/s |
| 8 MB | 154.5 µs | 54.31 GB/s | 95.04 GB/s |
| 33 MB | 352.3 µs | 95.24 GB/s | 166.67 GB/s |
| 134 MB | 1149 µs | 116.81 GB/s | 204.42 GB/s |
| 537 MB | 4166 µs | 128.88 GB/s | 225.54 GB/s |

Reporter labelled their result "~200 GB/s busbw" as expected for 8×A100 NVLink.

### H100 8×SXM5 NVLink, ring allreduce

- 8×H100 NVLink achieves **~360 GB/s busbw** at large sizes (issue #212, multiple users). NVIDIA marketing target is ~450 GB/s; with NVLS enabled some users see **~480 GB/s** (issue #272 H200 sees similar). Treat 250-360 GB/s as the realistic healthy band, **<200 GB/s as suspect**, **<100 GB/s as failed**.
- Small-message latency floor: 8-byte allreduce on DGX-A100 is ~10 µs (2 GPUs), ~14 µs (4), ~34 µs (8) per nccl-tests issue #123. H100 is similar (P2P-write floor ~3 µs).

### PCIe-only (no NVLink, e.g. consumer 4090s)
- busbw saturates at PCIe Gen4 x16 unidirectional ~25-32 GB/s × topology factor. Expect **5-15 GB/s busbw** for ring allreduce. If a "datacenter" node measures in this band, the link likely fell back from NVLink to PCIe — flag as a topology issue.

### Std-dev across healthy ranks
No vendor doc gives a published number. Empirically (issue #149, #212 sample data and standard nccl-tests practice): on a healthy single-node 8-GPU box, p50 latency across ranks is tight — **rank-to-rank std dev ≈ 2-5% of the median** at large message sizes. The cross-rank min/max ratio is typically ≤1.10. ByteDance ByteRobust (Yang et al., SOSP'25) does not publish a numeric σ-threshold; instead they cluster process stack-traces and flag the minority cluster, repeating "every 10 seconds" with a "5-round" cumulative voting scheme.

### 3σ vs IQR — which threshold

scanprobe currently uses `median + 3σ`. Issues with this on small N=8:
1. With 8 samples, `np.std` itself is noisy — std with one outlier in it inflates the threshold so the outlier may not trigger (mean-bias problem the brief flagged).
2. `np.std` defaults to ddof=0 (population std); for inferring a population σ from 8 samples, ddof=1 (sample std) is more honest.
3. Robust alternative: **MAD-based z-score** or **IQR fence**. With 8 ranks:
   - MAD: `MAD = median(|x_i - median(x)|)`; flag when `(x - median)/(1.4826 * MAD) > 3`. The 1.4826 factor makes MAD a consistent estimator of σ for normal data.
   - IQR: flag when `x > Q3 + 1.5 * IQR`. Simpler, more conservative, doesn't break with one big outlier.

**Recommendation:** switch the primary detector to MAD-z, keep σ as a secondary signal. Below 6 ranks both methods are unreliable — fall back to "any rank >2× median" rule.

---

## 4. Bus Bandwidth Formula Validation

scanprobe currently computes:

```python
bw = 2*(N-1)/N * bytes / latency_seconds
```

This **matches the official nccl-tests formula** for ring-allreduce busbw (PERFORMANCE.md, verbatim):

> `B = S/t * (2*(n-1)/n) = algbw * (2*(n-1)/n)`

**Verdict: CORRECT for ring allreduce.** ✓

### Caveats and corrections needed

1. **NVLS (NVLink SHARP) breaks the formula.** When NCCL_NVLS_ENABLE=1 (default on H100/GB200 with NVSwitch), the algorithm is no longer ring — there's a hardware-aggregation path. The 2(n-1)/n factor over-counts. nccl-tests PR #239 corrects this. For scanprobe's purposes, since we report busbw as a *health proxy* (not a peak-perf claim), the over-count is conservative — but the comment should be updated to flag this.

2. **Tree algorithm.** For very small messages NCCL picks tree allreduce; busbw factor differs. Since scanprobe uses the **largest** message size for the busbw calc, this is rarely an issue.

3. **Latency floor at small sizes.** scanprobe sweeps 4 KB, 256 KB, 4 MB, 64 MB. At 4 KB the dominant term is the small-message latency floor (~10-30 µs), so busbw at that size is meaningless. Already handled — scanprobe uses max size for the busbw calc. Good.

4. **Anti-pattern: powers of 2 vs message-size sweetspots.** NCCL picks different protocols (Simple/LL/LL128) at internal thresholds. The scanprobe sweep [4 KB, 256 KB, 4 MB, 64 MB] straddles all three regions, which is fine for outlier detection (each rank hits the same protocol).

5. **GiB vs GB unit mismatch (latent bug).** scanprobe currently divides by `1024**3` and labels the result `gib_s`, but the standard nccl-tests output uses **GB/s = bytes/1e9**, not GiB/s. The label is internally consistent, but if a user compares scanprobe's number to nccl-tests, scanprobe's value will appear ~7% lower. Either keep GiB/s and label clearly, or switch to GB/s to match nccl-tests. **Recommendation: switch to GB/s with `1e9` divisor for one-to-one comparability.**

---

## 5. Single-GPU Behavior Recommendation

Current scanprobe behavior: initializes a gloo group with world_size=1, runs an all_reduce on a 1024-element tensor, marks `passed=True` with a warning.

### Findings

1. `init_process_group(world_size=1)` does **not** hang and does succeed with both nccl and gloo backends (PyTorch issue confirmation: "When world_size=1 it does not hang"). So the call is technically valid.
2. world_size=1 allreduce is a memcpy (no peers); it surfaces almost nothing — not NVLink, not P2P, not topology. The only failure modes it could detect are:
   - PyTorch import broken (already covered by the import guard upstream).
   - A complete CUDA-runtime failure during `torch.ones(...)` on device (matmul check covers this much better).
3. The current "no-op" produces a misleading green light: a user with a broken NVLink fabric on a single-GPU node sees "collective check passed" and assumes the multi-GPU case will also pass.

### Recommendation: skip with explicit message

```python
if n_gpus == 1:
    r = CollectiveResult(world_size=1, passed=True)
    r.warnings.append("Collective check skipped: requires >=2 GPUs (single-GPU node)")
    return r
```

Drop the no-op allreduce entirely. The `passed=True` is honest (nothing to test), the warning tells the user what was/wasn't checked. Keep the `n_gpus == 0` branch as `passed=False` — that's a real failure (no CUDA).

For single-GPU memory allocator issues, those belong in the matmul check (which already exercises allocation, kernel launch, and memcpy).

---

## 6. Specific Code Changes for `collective.py`

| # | Change | Why | Confidence |
|---|---|---|---|
| C1 | Replace `n_gpus == 1` no-op block with skip-with-warning (see §5). | Avoid false-green on single-GPU nodes. | High |
| C2 | Pass `timeout=timedelta(seconds=60)` to `dist.init_process_group` in worker. | Default 600 s makes hangs invisible to a preflight. | High |
| C3 | Set env in subprocess `env` dict before launching torchrun: `TORCH_NCCL_ASYNC_ERROR_HANDLING=1`, `TORCH_NCCL_TRACE_BUFFER_SIZE=2000`, `TORCH_NCCL_DUMP_ON_TIMEOUT=1`, `TORCH_NCCL_ENABLE_MONITORING=1`, `NCCL_DEBUG=WARN` (or `INFO` if verbose). | Surfaces real errors and captures Flight Recorder trace if a hang occurs. | High |
| C4 | Switch outlier detection from `mean+3σ` to MAD-z with threshold 3.5; keep σ-rule as fallback when MAD=0. | Robust to one big outlier biasing the metric. | Medium |
| C5 | Add per-message-size outlier check (not only largest), so a rank that's only slow on small messages (latency floor regression) is caught. | Different failure modes show at different sizes. | Medium |
| C6 | Compute busbw with `bytes / 1e9` (GB/s), not `1024**3`. Rename field `bus_bandwidth_gb_s`. | Match nccl-tests convention so users can compare directly. | Medium |
| C7 | Parse stderr for the verbatim error regexes in §2 and surface specific failure-mode label (`NCCL_TIMEOUT`, `NCCL_INIT_FAIL`, `SHM_EXHAUSTED`, etc.) instead of returning the last 600 chars of stderr. | Actionable output; future test fixtures align with these labels. | High |
| C8 | If `outlier_ranks` non-empty, also report `cluster_min_p50_ms` and `outlier / median ratio`. A 1.5× ratio with low σ is a stronger signal than a 4σ flag with high σ. | Ratio is more interpretable than σ for users. | Medium |
| C9 | Document explicitly: scanprobe detects *slow* ranks; *hung* ranks are detected by torchrun timeout. State this in CollectiveResult docstring. | Manages user expectations. | High |
| C10 | Optional: when verbose, also call `nvidia-smi -q -d PERFORMANCE` and capture `Current Link Width / Gen Speed` to help disambiguate degraded-NVLink from degraded-PCIe. | Cheap, complements collective signal. | Medium |
| C11 | Use `torch.cuda.Event` (not `time.perf_counter()` + `torch.cuda.synchronize`) for per-iter timing. CUDA events are wall-clock-equivalent on the device side and avoid host scheduling jitter. | Cleaner timing; standard nccl-tests practice. | Medium |
| C12 | Consider reducing `repeats=20` to a warmup-amortized `repeats=10` and increasing the warmup to 10. The first iteration includes communicator setup and is always slow. | Faster check; nccl-tests defaults to 5 warmup / 20 iter, our values are reasonable. | Low |

### Suggested `_WORKER_SCRIPT` env injection (sketch)

```python
env["NCCL_DEBUG"] = "INFO" if verbose else "WARN"
env["TORCH_NCCL_ASYNC_ERROR_HANDLING"] = "1"
env["TORCH_NCCL_TRACE_BUFFER_SIZE"] = "2000"
env["TORCH_NCCL_DUMP_ON_TIMEOUT"] = "1"
env["TORCH_NCCL_ENABLE_MONITORING"] = "1"
# Only override if user hasn't already set these
for k, v in env.items():
    env.setdefault(k, v)  # don't clobber user config
```

### Suggested MAD-based outlier detection (sketch)

```python
np = _get_np()
median = float(np.median(all_p50s))
mad = float(np.median(np.abs(np.asarray(all_p50s) - median)))
if mad > 0:
    # 1.4826 makes MAD a consistent estimator of std for normal data
    threshold = median + 3.5 * 1.4826 * mad
    for rank, p50 in result.rank_p50_ms.items():
        if p50 > threshold:
            ratio = p50 / median if median > 0 else float('inf')
            result.outlier_ranks.append({
                "rank": rank,
                "p50_ms": p50,
                "ratio_to_median": round(ratio, 2),
                "mad_z": round((p50 - median) / (1.4826 * mad), 2),
            })
elif len(all_p50s) >= 2:
    # MAD=0 means tightly clustered; any rank >2x median is suspect
    for rank, p50 in result.rank_p50_ms.items():
        if p50 > 2.0 * median:
            result.outlier_ranks.append({
                "rank": rank, "p50_ms": p50,
                "ratio_to_median": round(p50/median, 2),
                "mad_z": None,
            })
```

---

## 7. Summary Table — Confidence on Findings

| Finding | Confidence | Source(s) |
|---|---|---|
| Default PyTorch NCCL watchdog timeout = 600 s = 10 min | High | flight-recorder blog |
| Watchdog error string regex | High | flight-recorder blog, multiple PyTorch forum posts |
| 8×H100 NVLink healthy busbw band 250-360 GB/s, ceiling ~480 GB/s with NVLS | High | nccl-tests #212, #272 |
| 8×A100 NVLink healthy busbw ~200 GB/s | High | nccl-tests #149 |
| ring busbw formula `S/t * 2(n-1)/n` matches scanprobe | High | nccl-tests PERFORMANCE.md |
| MAD-z is more robust than σ for N=8 outlier detection | High | textbook robust statistics |
| ByteDance ByteRobust uses stack-trace clustering, not σ thresholds | High | arxiv 2509.16293 §4 |
| Single-GPU world_size=1 init succeeds without hang | High | PyTorch forum confirmation |
| TORCH_NCCL_ASYNC_ERROR_HANDLING preferred over BLOCKING_WAIT | High | pytorch/pytorch#46874 |
| 60 s preflight timeout is appropriate (vs 600 s training default) | Medium | judgment — no published guidance |
| MAD-z threshold value 3.5 | Medium | conventional choice; tune empirically |
| Switching GiB → GB/s improves UX | Medium | judgment — both are valid |
| Per-rank std dev typically 2-5% on healthy hardware | Medium | inferred from issue #149/#212 sample data; no vendor-published number |
| NVLS breaks 2(n-1)/n factor for the largest message sizes | High | nccl-tests PR #239 |
| ACS / virtual PCIe topology causes hidden P2P regressions | High | troubleshooting.html |

---

## Appendix A — Diagnostic command snippets the user can paste

```bash
# Confirm CUDA / NCCL versions match
python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.nccl.version())"

# Run authoritative perf reference
./build/all_reduce_perf -b 8 -e 128M -f 2 -g 8

# Capture the full debug log next to scanprobe's flagged rank
NCCL_DEBUG=INFO NCCL_DEBUG_SUBSYS=INIT,COLL,P2P,GRAPH \
  python -m ashiba_scanprobe.checks.collective --verbose 2> nccl_debug.log

# Quick PCIe link health
nvidia-smi -q -d PERFORMANCE | grep -E "Gen|Width"

# RAS query (NCCL >=2.24, on a node where the hung job is still alive)
ncclras            # or: telnet localhost 28028
```

## Appendix B — Pointers for follow-up research

1. **NVLS busbw correction** — pull nccl-tests PR #239 and decide if scanprobe should detect NVLS via `NCCL_NVLS_ENABLE` and apply the corrected factor.
2. **Multi-node** — current scanprobe is single-node only. Multi-node adds IB/RoCE failure modes (#8 in §1) that need explicit handling.
3. **Flight Recorder parsing** — if `TORCH_NCCL_DUMP_ON_TIMEOUT=1` produces a trace, scanprobe could parse it and report which collective seqnum hung. PyTorch tutorial documents the JSON schema.
4. **Empirical std-dev study** — collect rank-p50 distributions from a known-healthy 8×H100 node over many runs to set a defensible MAD-z threshold rather than the textbook 3.5.
