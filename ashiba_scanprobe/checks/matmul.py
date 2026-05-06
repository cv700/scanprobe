"""
Matmul validator: checks GEMM numerical correctness on a single GPU.
Compares GPU output against a CPU FP64 reference using the cast-then-reference rule:
cast inputs to target dtype first, then compute the reference in FP64.

Uses adversarial inputs (subnormal boundary, catastrophic cancellation)
in addition to standard Llama-shaped workloads.
"""

import time
from dataclasses import dataclass, field
from typing import Optional

# numpy imported lazily so the module loads without it
_np = None
def _get_np():
    global _np
    if _np is None:
        import numpy
        _np = numpy
    return _np

_torch = None

def _get_torch():
    global _torch
    if _torch is None:
        import torch
        _torch = torch
    return _torch


@dataclass
class MatmulResult:
    gpu_index: int
    passed: bool = True
    error: Optional[str] = None
    warnings: list = field(default_factory=list)
    shape_results: list = field(default_factory=list)
    max_relative_l2: float = 0.0
    num_shapes_tested: int = 0
    num_shapes_anomalous: int = 0
    tflops_fp16: Optional[float] = None


# Shapes: (name, (M, K, N))
# Llama-2 7B inference projections + adversarial shapes
SHAPES = [
    ("llama_qkv",      (1,   4096, 12288)),  # QKV projection, decode
    ("llama_ffn_gate", (1,   4096, 11008)),  # FFN gate
    ("llama_ffn_down", (1,  11008,  4096)),  # FFN down projection
    ("synth_sq",       (256,  256,   256)),  # small square
    ("adv_subnormal",  (64,   128,   128)),  # near FP16 subnormal boundary (~6e-5)
    ("adv_cancel",     (128,  512,   128)),  # catastrophic cancellation pattern
]

DTYPES = ["fp16", "bf16", "fp32_tf32_disabled"]

# Relative L2 error threshold vs FP64 reference.
# Healthy GPUs should be well below 1e-3 for fp16/bf16.
# We use a per-dtype threshold because bf16 has fewer mantissa bits.
ANOMALY_THRESHOLDS = {
    "fp16":                5e-3,
    "bf16":                1e-2,   # bf16 has 7 mantissa bits vs 10 for fp16
    "fp32_tf32_disabled":  1e-4,   # full fp32 should be tight
}
DEFAULT_ANOMALY_THRESHOLD = 5e-3


def _make_input(shape_name: str, shape: tuple, dtype_str: str, seed: int):
    """
    Generate input matrices for the given shape and dtype.
    Adversarial shapes get specially constructed inputs.
    """
    torch = _get_torch()
    M, K, N = shape
    rng = torch.Generator()
    rng.manual_seed(seed)

    if shape_name == "adv_subnormal":
        # Values near FP16 subnormal boundary (~6.1e-5 to 1e-3)
        # Exercises GPU handling of denormal arithmetic
        A = torch.rand(M, K, generator=rng) * 9.5e-4 + 5.5e-5
        B = torch.rand(K, N, generator=rng) * 9.5e-4 + 5.5e-5
    elif shape_name == "adv_cancel":
        # Catastrophic cancellation: pairs of rows sum to near zero
        # Amplifies any floating-point non-associativity
        A = torch.randn(M, K, generator=rng)
        B = torch.randn(K, N, generator=rng)
        # Mirror top half of B onto bottom half with negation
        half = K // 2
        B[half:] = -B[:half]
    else:
        A = torch.randn(M, K, generator=rng)
        B = torch.randn(K, N, generator=rng)

    dtype_map = {
        "fp16":                 torch.float16,
        "bf16":                 torch.bfloat16,
        "fp32_tf32_disabled":   torch.float32,
    }
    dt = dtype_map.get(dtype_str, torch.float16)
    return A.to(dt), B.to(dt)


def _run_matmul(A, B, dtype_str: str):
    """Run matmul with appropriate TF32 policy for the dtype."""
    torch = _get_torch()
    old_tf32 = torch.backends.cuda.matmul.allow_tf32
    if "disabled" in dtype_str:
        torch.backends.cuda.matmul.allow_tf32 = False
    try:
        C = torch.matmul(A, B)
        torch.cuda.synchronize()
    finally:
        torch.backends.cuda.matmul.allow_tf32 = old_tf32
    return C


def _reference(A, B):
    """
    CPU FP64 reference. Cast-then-reference rule:
    cast to float() first (matches what the GPU received), then upcast to double.
    """
    torch = _get_torch()
    A64 = A.float().double().cpu()
    B64 = B.float().double().cpu()
    return torch.matmul(A64, B64)


def _relative_l2(C_gpu, C_ref):
    """Relative L2 error: ||C_gpu - C_ref||_2 / ||C_ref||_2."""
    np = _get_np()
    diff = (C_gpu.float().cpu() - C_ref.float()).flatten().numpy()
    ref_flat = C_ref.float().flatten().numpy()
    norm_ref = float(np.linalg.norm(ref_flat)) + 1e-12
    return float(np.linalg.norm(diff)) / norm_ref


def _feature_vector(C_gpu, C_ref):
    """Return a dict of numerical distance metrics between GPU output and reference."""
    np = _get_np()
    diff = (C_gpu.float().cpu() - C_ref.float()).abs()
    ref_abs = C_ref.float().abs().clamp(min=1e-12)
    return {
        "relative_l2":   _relative_l2(C_gpu, C_ref),
        "max_rel_diff":  float((diff / ref_abs).max()),
        "mean_abs_diff": float(diff.mean()),
        "p99_abs_diff":  float(np.percentile(diff.flatten().numpy(), 99)),
        "max_abs_diff":  float(diff.max()),
    }


def check_matmul(gpu_index: int = 0, seed: int = 42, quick: bool = False) -> MatmulResult:
    """
    Run GEMM numerical correctness probe on a single GPU.
    quick=True tests fewer shapes/dtypes for Tier 1 fast path.
    """
    result = MatmulResult(gpu_index=gpu_index)

    try:
        torch = _get_torch()
        if not torch.cuda.is_available():
            result.error = "CUDA not available"
            result.passed = False
            return result

        device = torch.device(f"cuda:{gpu_index}")
        torch.cuda.set_device(device)

        shapes_to_test = SHAPES[:3] if quick else SHAPES
        dtypes_to_test = ["fp16"] if quick else DTYPES
        anomalous = 0

        for shape_name, shape in shapes_to_test:
            for dtype_str in dtypes_to_test:
                threshold = ANOMALY_THRESHOLDS.get(dtype_str, DEFAULT_ANOMALY_THRESHOLD)
                A, B = _make_input(shape_name, shape, dtype_str, seed)
                A_gpu = A.to(device)
                B_gpu = B.to(device)

                try:
                    C_gpu = _run_matmul(A_gpu, B_gpu, dtype_str)
                    C_ref = _reference(A, B)
                    feats = _feature_vector(C_gpu, C_ref)
                    is_anomalous = feats["relative_l2"] > threshold

                    if is_anomalous:
                        anomalous += 1

                    result.shape_results.append({
                        "shape": shape_name,
                        "dtype": dtype_str,
                        "adversarial": shape_name.startswith("adv_"),
                        "features": feats,
                        "threshold": threshold,
                        "anomalous": is_anomalous,
                    })
                    result.max_relative_l2 = max(result.max_relative_l2, feats["relative_l2"])

                except Exception as e:
                    anomalous += 1
                    result.shape_results.append({
                        "shape": shape_name,
                        "dtype": dtype_str,
                        "error": str(e),
                        "anomalous": True,
                    })

        result.num_shapes_tested = len(result.shape_results)
        result.num_shapes_anomalous = anomalous

        if anomalous > 0:
            result.warnings.append(
                f"{anomalous}/{result.num_shapes_tested} shape/dtype configs exceed "
                f"relative L2 threshold (max observed: {result.max_relative_l2:.2e})"
            )
            # Hard failure if majority of shapes are anomalous
            if anomalous >= len(shapes_to_test) // 2:
                result.passed = False

        # FP16 throughput estimate on a large square
        try:
            M = K = N = 4096
            A_t = torch.randn(M, K, dtype=torch.float16, device=device)
            B_t = torch.randn(K, N, dtype=torch.float16, device=device)
            torch.cuda.synchronize()
            t0 = time.perf_counter()
            for _ in range(10):
                torch.matmul(A_t, B_t)
            torch.cuda.synchronize()
            elapsed = (time.perf_counter() - t0) / 10
            result.tflops_fp16 = (2 * M * K * N) / elapsed / 1e12
        except Exception:
            pass  # throughput is informational only

    except ImportError:
        result.error = "torch not installed — run: pip install torch"
        result.passed = False
    except Exception as e:
        result.error = str(e)
        result.passed = False

    return result
