"""
Collective latency check: measures per-rank allreduce latency across message sizes.
Flags ranks with p50 latency > threshold_sigma standard deviations above cluster median.

Runs via torchrun subprocess — each process writes its own result JSON.
Single GPU: runs a degenerate no-op allreduce just to confirm torch.distributed works.
"""

import os
import time
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Optional

_np = None
def _get_np():
    global _np
    if _np is None:
        import numpy
        _np = numpy
    return _np


@dataclass
class CollectiveResult:
    world_size: int = 1
    passed: bool = True
    error: Optional[str] = None
    warnings: list = field(default_factory=list)
    per_rank_latency_ms: dict = field(default_factory=dict)   # rank -> list[float]
    rank_p50_ms: dict = field(default_factory=dict)           # rank -> float
    rank_p99_ms: dict = field(default_factory=dict)           # rank -> float
    outlier_ranks: list = field(default_factory=list)         # [{rank, p50_ms, sigma_above_median}]
    cluster_median_ms: Optional[float] = None
    cluster_p99_ms: Optional[float] = None                    # p99 across rank p50s
    threshold_sigma: float = 3.0
    message_sizes_bytes: list = field(default_factory=list)
    bus_bandwidth_gib_s: dict = field(default_factory=dict)   # str(bytes) -> float


# Worker script executed inside each torchrun process.
# Writes per-rank JSON to a path derived from PREFLIGHT_COLLECTIVE_OUT
# by substituting {rank} with the actual rank integer.
_WORKER_SCRIPT = '''\
import os, sys, json, time
import torch
import torch.distributed as dist
import numpy as np

def main():
    backend = "nccl" if torch.cuda.is_available() else "gloo"
    dist.init_process_group(backend)
    rank = dist.get_rank()
    world_size = dist.get_world_size()
    device = torch.device(f"cuda:{rank}" if torch.cuda.is_available() else "cpu")

    # Message sizes in number of float32 elements
    # 1K / 64K / 1M / 16M  →  4KB / 256KB / 4MB / 64MB
    message_sizes = [1024, 65536, 1048576, 16777216]
    repeats = 20
    warmup = 5

    results = {"rank": rank, "world_size": world_size, "latencies": {}}

    for nelems in message_sizes:
        t = torch.ones(nelems, dtype=torch.float32, device=device)
        key = str(nelems * 4)  # bytes

        # Warmup (not timed)
        for _ in range(warmup):
            dist.all_reduce(t, op=dist.ReduceOp.SUM)
        if torch.cuda.is_available():
            torch.cuda.synchronize()

        # Timed runs
        latencies = []
        for _ in range(repeats):
            if torch.cuda.is_available():
                torch.cuda.synchronize()
            t0 = time.perf_counter()
            dist.all_reduce(t, op=dist.ReduceOp.SUM)
            if torch.cuda.is_available():
                torch.cuda.synchronize()
            latencies.append((time.perf_counter() - t0) * 1000)  # ms

        results["latencies"][key] = latencies

    # Write result — substitute {rank} in the output path template
    path_template = os.environ.get(
        "PREFLIGHT_COLLECTIVE_OUT",
        "/tmp/preflight_rank_{rank}.json"
    )
    out_path = path_template.replace("{rank}", str(rank))
    with open(out_path, "w") as f:
        json.dump(results, f)

    dist.destroy_process_group()

if __name__ == "__main__":
    main()
'''


def _run_torchrun_collective(n_gpus: int, timeout: int = 120) -> CollectiveResult:
    """Launch torchrun subprocess, collect per-rank JSON results, compute statistics."""
    result = CollectiveResult(world_size=n_gpus, threshold_sigma=3.0)

    with tempfile.TemporaryDirectory() as tmpdir:
        # Write worker script to temp file
        script_path = os.path.join(tmpdir, "collective_worker.py")
        with open(script_path, "w") as f:
            f.write(_WORKER_SCRIPT)

        # Output path template — each worker substitutes its own rank
        out_template = os.path.join(tmpdir, "rank_{rank}.json")
        env = os.environ.copy()
        env["PREFLIGHT_COLLECTIVE_OUT"] = out_template

        cmd = [
            sys.executable, "-m", "torch.distributed.run",
            f"--nproc_per_node={n_gpus}",
            "--standalone",
            script_path,
        ]

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, env=env
            )
            if proc.returncode != 0:
                result.error = (
                    f"torchrun failed (exit {proc.returncode}): "
                    f"{proc.stderr[-600:]}"
                )
                result.passed = False
                return result

            # Collect results from each rank's JSON file
            all_p50s = []
            message_sizes = None

            for rank in range(n_gpus):
                out_path = out_template.replace("{rank}", str(rank))
                if not os.path.exists(out_path):
                    result.warnings.append(f"Missing output for rank {rank}")
                    continue

                with open(out_path) as f:
                    data = json.load(f)

                latencies_by_size = data.get("latencies", {})
                if message_sizes is None:
                    message_sizes = sorted(int(k) for k in latencies_by_size)
                    result.message_sizes_bytes = message_sizes

                # Use largest message size as primary latency signal
                if message_sizes:
                    key = str(max(message_sizes))
                    lats = latencies_by_size.get(key, [])
                    if lats:
                        np = _get_np()
                        p50 = float(np.percentile(lats, 50))
                        p99 = float(np.percentile(lats, 99))
                        result.per_rank_latency_ms[rank] = lats
                        result.rank_p50_ms[rank] = p50
                        result.rank_p99_ms[rank] = p99
                        all_p50s.append(p50)

            if not all_p50s:
                result.error = "No rank output collected"
                result.passed = False
                return result

            # Cluster statistics across rank p50s
            np = _get_np()
            median = float(np.median(all_p50s))
            std = float(np.std(all_p50s)) if len(all_p50s) > 1 else 0.0
            result.cluster_median_ms = median
            result.cluster_p99_ms = float(np.percentile(all_p50s, 99))

            # Flag outliers: ranks whose p50 > median + threshold_sigma * std
            if std > 0:
                threshold = median + result.threshold_sigma * std
                for rank, p50 in result.rank_p50_ms.items():
                    if p50 > threshold:
                        sigma_above = (p50 - median) / std
                        result.outlier_ranks.append({
                            "rank": rank,
                            "p50_ms": p50,
                            "sigma_above_median": round(sigma_above, 2),
                        })

            if result.outlier_ranks:
                ranks_str = ", ".join(str(o["rank"]) for o in result.outlier_ranks)
                result.warnings.append(
                    f"Collective latency outliers (>{result.threshold_sigma}σ): rank(s) {ranks_str}"
                )
                # >25% outliers suggests a fabric issue, not an isolated GPU problem
                if len(result.outlier_ranks) > n_gpus // 4:
                    result.passed = False

            # Bus bandwidth estimate at the largest message size
            if message_sizes and median > 0:
                largest_bytes = max(message_sizes)
                # Ring-allreduce bus bandwidth formula: 2*(N-1)/N * bytes / latency
                bw_factor = 2 * (n_gpus - 1) / n_gpus
                bw_gib_s = (bw_factor * largest_bytes) / (median / 1000.0) / (1024 ** 3)
                result.bus_bandwidth_gib_s[str(largest_bytes)] = round(bw_gib_s, 2)

        except subprocess.TimeoutExpired:
            result.error = f"Collective test timed out after {timeout}s"
            result.passed = False
        except FileNotFoundError:
            result.error = "torchrun not found — install PyTorch: pip install torch"
            result.passed = False
        except Exception as e:
            result.error = str(e)
            result.passed = False

    return result


def check_collective(n_gpus: Optional[int] = None, timeout: int = 120) -> CollectiveResult:
    """
    Run collective latency test across all GPUs on this node.
    Single GPU: degenerate no-op allreduce (verifies torch.distributed works).
    Multi-GPU: full torchrun sweep, per-rank latency statistics.
    """
    if n_gpus is None:
        try:
            import torch
            n_gpus = torch.cuda.device_count()
        except Exception:
            n_gpus = 0

    if n_gpus == 0:
        r = CollectiveResult(world_size=0, passed=False)
        r.error = "No CUDA GPUs available"
        return r

    if n_gpus == 1:
        r = CollectiveResult(world_size=1)
        try:
            import torch
            import torch.distributed as dist
            os.environ.setdefault("MASTER_ADDR", "localhost")
            os.environ.setdefault("MASTER_PORT", "29500")
            if not dist.is_initialized():
                dist.init_process_group("gloo", rank=0, world_size=1)
            t = torch.ones(1024, dtype=torch.float32)
            dist.all_reduce(t)
            r.cluster_median_ms = 0.0
            r.warnings.append("Single GPU: collective test is a no-op (need ≥2 GPUs for meaningful results)")
        except Exception as e:
            r.error = str(e)
        finally:
            try:
                import torch.distributed as dist
                if dist.is_initialized():
                    dist.destroy_process_group()
            except Exception:
                pass
        return r

    return _run_torchrun_collective(n_gpus, timeout=timeout)


# Usable as a standalone torchrun worker:
#   torchrun --nproc_per_node=N ashiba_scanprobe/checks/collective.py
if __name__ == "__main__":
    exec(_WORKER_SCRIPT)
