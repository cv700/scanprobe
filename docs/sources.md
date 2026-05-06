# Alpha sources

High-signal references beyond the official NVIDIA docs in `validation-guide.md`.
Each one earns its place by either (a) sharpening technical accuracy, (b) adding
scenarios we don't currently handle, (c) providing citation credibility, or (d)
giving us real-world output to use as test fixtures.

When citing any of these in code, use the format:
`# Source: <author> "<title>" §<section> (<year>) — <one-line claim>`

---

## Papers (citation credibility)

### ByteRobust — the canonical paper on GPU cluster failure at scale
- arXiv:2509.16293
- ByteDance's analysis of failures across their 16,384-GPU production fleet
- Section 4.1: proactive checks (NIC, packet loss, switch health, DCGM,
  PCIe BW, memory remapping, temperature, OS kernel events)
- Section 4.3: NVIDIA EUD has ~70% recall on SDC events
- Section 5.2: dormant faults — some GPUs pass all pre-flight checks and fail
  at training step 450+. **This is the honest caveat we cite in our README.**
- **What to extract**: failure mode taxonomy, weights for proactive checks
- **Where to cite**: scoring.py (signal weights), README "What it doesn't do"

### Meta Llama 3 paper — the cluster reliability numbers
- arXiv:2407.21783 ("The Llama 3 Herd of Models")
- Section 4: 16,384 H100 cluster, one job failure every ~3 hours
- Section 4: 466 failures over 54-day training run
- Detailed breakdown: GPU failures, NVLink failures, network failures
- Six distinct silent data corruption events documented
- **What to extract**: real failure rates that justify the existence of the tool
- **Where to cite**: README "Why" section

### "Cores that don't count" — Google's SDC paper
- Hochschild et al., HotOS 2021
- Document silent data corruption in production CPUs (similar phenomenon
  to mercurial cores in GPUs)
- Establishes that SDC is real, frequent, and impossible to detect from
  software without specific probes
- **What to extract**: theoretical grounding for why matmul correctness
  probe is necessary
- **Where to cite**: matmul.py (header docstring justifying the check)

### "Silent Data Corruptions at Scale" — Meta SDC paper
- Dixit et al., 2021 (Meta engineering blog and arXiv:2102.11245)
- Describes Meta's experience with SDC at scale, the "Mercurial Core"
  phenomenon, and detection via differential checking
- **What to extract**: the differential checking pattern (compute on two
  paths, compare) is exactly what scanprobe's matmul check does vs FP64
- **Where to cite**: matmul.py header

### XPUTimer / Flare — kernel-level fault detection
- arXiv:2502.02670 (XPUTimer) and related Flare papers from Ant Group
- CUDA-GDB intra-kernel tracing to read register values from halted
  ring-allreduce kernels, O(1) fault isolation
- Establishes that the most expensive failure mode (job hang) is detectable
  at the kernel register level — orthogonal to what scanprobe does, but
  cited as adjacent prior art in the README's "see also" section

### LLMPrism — network-flow change-point detection
- Bayesian Online Change-point Detection on network flows during training
- Blind to tensor parallelism (intra-machine), cannot see GPU internals
- **What to extract**: justifies why we need GPU-internal probes, not
  just network-level monitoring

### "Characterizing Large Language Model Development in the Datacenter"
- NSDI 2024 (Hu et al.) — Shanghai AI Lab + Alibaba
- Detailed taxonomy of training failures across 6 months, 16K GPUs
- 22% of jobs fail; 73% of those due to hardware (not user error)
- **What to extract**: the 73%-hardware-failure stat is a strong README
  opening line

---

## NVIDIA primary sources beyond `validation-guide.md`

### NVIDIA Driver Release Notes
- https://docs.nvidia.com/datacenter/tesla/drivers/index.html
- Track field name changes across driver versions
- 535.x, 550.x, 560.x are the current relevant series
- **Why**: a tool that breaks on driver upgrade dies. Subscribing to release
  notes (or pinning Codex to read them) is how we stay correct over time.

### DCGM Field IDs reference
- https://docs.nvidia.com/datacenter/dcgm/latest/dcgm-api/dcgm-api-field-ids.html
- The complete list of metrics DCGM can report, with field IDs
- **Why**: DCGM has fields scanprobe doesn't currently surface
  (DCGM_FI_DEV_GPU_TEMP, DCGM_FI_DEV_NVLINK_BANDWIDTH_TOTAL, etc.).
  Could enrich the DCGM check without reinventing nvidia-smi parsing.
- **Risk**: only useful if DCGM is installed — keep the zero-dep tier
  unaffected.

### NCCL Troubleshooting Guide
- https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/troubleshooting.html
- Specific environment variables that surface NCCL errors
  (NCCL_DEBUG=INFO, NCCL_DEBUG_SUBSYS=ALL)
- Common NCCL failure modes: peer-to-peer disabled, NVLink topology
  unreachable, CUDA mismatch
- **What to extract**: when collective.py fails, the error message should
  hint at NCCL_DEBUG=INFO if it's an NCCL error. The *fix-naming* principle.

### Compute Sanitizer Tool
- https://docs.nvidia.com/cuda/compute-sanitizer/
- NVIDIA's official tool for detecting memory errors in CUDA kernels
- `compute-sanitizer --tool=memcheck` catches uninitialized memory
- **Why**: the matmul probe could optionally invoke compute-sanitizer to
  catch memory issues a numerical comparison alone won't surface.
  **Future enhancement, not v0.1.**

### NVIDIA Developer Forums — Xid threads
- https://forums.developer.nvidia.com/c/general/xid-issues
- Real debug logs from real users with real failures
- **What to extract**: actual dmesg output from real Xid events.
  These are the highest-quality test fixtures we can get without owning
  hardware. Codex should sample a dozen threads and add fixtures.

---

## Adjacent open-source tools (study and learn)

### gpustat — minimal nvidia-smi wrapper
- https://github.com/wookayin/gpustat
- Same simplicity philosophy as scanprobe Tier 1
- **What to study**: how it handles nvidia-smi parsing across driver versions,
  what fields it queries (compare against ours)

### nvitop — interactive GPU process viewer
- https://github.com/XuehaiPan/nvitop
- More feature-rich, uses NVML directly via pynvml
- **What to study**: how they handle NVML errors, ECC field handling,
  MIG and vGPU support
- **Don't copy**: their dependency footprint is larger than ours

### nvidia-gpu-exporter — Prometheus exporter
- https://github.com/utkuozdemir/nvidia_gpu_exporter
- Production-grade nvidia-smi parsing for monitoring
- **What to study**: their list of metrics tracks what enterprise users
  actually monitor
- Field list is a good check against ours

### gpu-burn — stress test
- https://github.com/wilicc/gpu-burn
- The standard "is this GPU broken" stress test
- Single-purpose, runs CUBLAS matmul in a loop, checks for errors
- **What to study**: the matmul probe shape choices in matmul.py should
  match what gpu-burn does for a known-good baseline. If we differ,
  document why.

### DCGM Python bindings
- https://github.com/NVIDIA/DCGM/tree/master/dcgm_bindings
- Use these instead of subprocess parsing if performance/reliability matters
- **Future**: replace `subprocess.run(["dcgmi", ...])` with direct DCGM
  Python API calls when DCGM is installed. Better error handling.

---

## Real-world output sources (for test fixtures)

### NVIDIA Developer Forums — Xid threads
(See above) — direct dmesg snippets from real failures.

### GitHub Issues on major ML projects
- PyTorch repository — search for "Xid" or "NVRM" or "ECC"
- DeepSpeed repository — same search
- Hugging Face Transformers — training failure issues
- **Pattern**: when someone pastes their training crash log, the dmesg
  context often follows. Real test fixtures hidden in plain sight.

### Cloud provider status pages
- Lambda Labs status: https://status.lambdalabs.com/
- CoreWeave status pages
- AWS Service Health Dashboard (filter to EC2 GPU instances)
- **What to extract**: incident narratives reveal which Xid codes are
  most common in production cloud environments. Calibrates our scoring.

### r/MachineLearning incident threads
- People post training crash logs with full nvidia-smi + dmesg context
- Filter for "training crashed" or "GPU died" or "NaN loss"
- **Risk**: low signal-to-noise. Codex should treat these as anecdotes,
  not authoritative.

---

## Cloud provider documentation

### AWS p4d/p5 known issues
- AWS support docs on p4d.24xlarge known hardware issues
- Specifies typical failure rates, RMA patterns
- **Why**: AWS-specific behaviors (EFA networking, GPUDirect interactions)
  may produce edge cases.

### Lambda Labs / RunPod / CoreWeave docs
- Each has documentation on GPU access patterns, driver versions,
  containerization quirks
- **What to extract**: which platforms have full dmesg access, which
  restrict it. Calibrates the "try: sudo scanprobe" hint.

### MLCommons / MLPerf training submissions
- https://mlcommons.org/benchmarks/training/
- Reference implementations from the major labs
- **What to extract**: what does a known-good cluster configuration look
  like? The submission READMEs document hardware setup carefully.

---

## Standards (long game)

### IEEE 754 floating point
- For the matmul correctness probe, the threshold for "anomalous" must
  reference IEEE 754's relative error bounds for fp16/bf16 multiplication
- **Why**: when a reviewer asks "why is your fp16 threshold 5e-3?" the
  answer is grounded in IEEE 754, not picked arbitrarily

### IEC 61508 functional safety
- Establishes the language of "safety integrity levels" used in industrial
  computing
- Eventually relevant for the regulatory positioning

### EU AI Act
- Articles on training data and infrastructure documentation requirements
- **Why**: the long-tail thesis — when "did you verify your training
  substrate?" becomes a compliance question, this is the regulatory
  framework that will require it

### MLCommons reliability working group (if/when they form one)
- Watch for industry-wide standardization of training reliability metrics

---

## How to use these sources

When Codex picks up tomorrow, the order matters:

**First (foundation)**:
1. Read NVIDIA Xid Errors page — confirm DRAIN_XIDS / WATCH_XIDS classifications
2. Read NVML throttle reasons — confirm bit values and check for decimal-vs-hex
3. Skim gpustat and nvitop source — check for fields we missed

**Second (citations)**:
4. Add ByteRobust §4.1 citation to nvidia_smi.py header
5. Add ByteRobust §5.2 citation to README "What it doesn't do"
6. Add Meta Llama 3 stat to README "Why" section
7. Add Meta SDC paper citation to matmul.py header

**Third (test fixtures)**:
8. Sample 5-10 Xid threads from NVIDIA Developer Forums
9. Convert each into a test fixture in test_xid_parsing.py
10. Note the dmesg format variants you see — extend the regex if needed

**Fourth (scenarios)**:
11. Write tests for ECC disabled (cloud VM scenario)
12. Write tests for [Not Supported] field returns (consumer GPU)
13. Document MIG and vGPU as known-unsupported in README

This sequencing means citations and tests come before any code changes.
A tool that reads the right papers and tests against real output is more
credible than a tool with more features.
