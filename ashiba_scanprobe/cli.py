"""Command line entry point for scanprobe."""

import argparse
import json
import sys
import time
from dataclasses import asdict

from .checks.nvidia_smi import check_all_nvidia_smi, count_gpus
from .checks.xid import check_xid
from .scoring import compute_risk_score


def _parse_gpu_list(gpus_str: str, n_available: int) -> list:
    if gpus_str.lower() == "all":
        return list(range(n_available))
    indices = []
    for part in gpus_str.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            indices.extend(range(int(lo), int(hi) + 1))
        else:
            indices.append(int(part))
    return sorted(set(indices))


def _node_tier(scores: list) -> str:
    tiers = {score.tier for score in scores}
    if "DRAIN" in tiers:
        return "DRAIN"
    if "WATCH" in tiers:
        return "WATCH"
    return "HEALTHY"


def _safe_asdict(obj):
    if obj is None:
        return None
    try:
        return asdict(obj)
    except TypeError:
        return str(obj)


def _print_json(scores: list, nvidia_results: dict, xid_result, elapsed: float):
    out = {
        "elapsed_s": round(elapsed, 2),
        "node_tier": _node_tier(scores),
        "risk_scores": [_safe_asdict(score) for score in scores],
        "nvidia_smi": {str(k): _safe_asdict(v) for k, v in sorted(nvidia_results.items())},
        "xid": _safe_asdict(xid_result),
    }
    print(json.dumps(out, indent=2))


def _print_text(scores: list, nvidia_results: dict, xid_result, elapsed: float):
    print("scanprobe")
    for score in sorted(scores, key=lambda item: item.gpu_index):
        gpu = nvidia_results.get(score.gpu_index)
        name = gpu.name if gpu and gpu.name else "unknown"
        temp = f"{gpu.temperature_gpu:.0f}C" if gpu and gpu.temperature_gpu is not None else "n/a"
        print(
            f"GPU {score.gpu_index}: {score.tier} "
            f"score={score.score:.2f} temp={temp} name={name}"
        )
        for rec in score.recommendations:
            print(f"  - {rec}")

    if xid_result is not None and not xid_result.available:
        print(f"Xid scan unavailable: {xid_result.error or 'kernel log access restricted'}")
    elif xid_result is not None and xid_result.events:
        codes = sorted({event["xid"] for event in xid_result.events})
        print("Xid events: " + ", ".join(str(code) for code in codes))

    print(f"Node: {_node_tier(scores)}")
    print(f"Completed in {elapsed:.1f}s")


def run(
    gpus: str = "all",
    json_output: bool = False,
):
    """Run the scan and return a process exit code."""
    t_start = time.time()

    n_available = count_gpus()
    if n_available == 0:
        msg = "No CUDA GPUs found via nvidia-smi."
        if json_output:
            print(json.dumps({"error": msg}))
        else:
            print(msg)
        return 3

    try:
        gpu_indices = _parse_gpu_list(gpus, n_available)
    except (ValueError, TypeError) as e:
        print(f"Invalid --gpus argument: {e}")
        return 3

    if not gpu_indices:
        print("No valid GPU indices.")
        return 3

    nvidia_results = check_all_nvidia_smi(gpu_indices)
    xid_result = check_xid()

    risk_scores = []
    for idx in gpu_indices:
        risk_scores.append(
            compute_risk_score(
                nvidia_result=nvidia_results.get(idx),
                xid_result=xid_result,
                gpu_index=idx,
            )
        )

    elapsed = time.time() - t_start

    if json_output:
        _print_json(risk_scores, nvidia_results, xid_result, elapsed)
    else:
        _print_text(risk_scores, nvidia_results, xid_result, elapsed)

    node_tier = _node_tier(risk_scores)
    if node_tier == "DRAIN":
        return 2
    if node_tier == "WATCH":
        return 1
    return 0


def main_entry():
    p = argparse.ArgumentParser(
        prog="scanprobe",
        description="Minimal GPU health scan: nvidia-smi + Xid logs.",
    )
    p.add_argument("--gpus", default="all", help="'all', '0', '0,1,2', or '0-3'")
    p.add_argument("--json", action="store_true", help="print JSON")
    args = p.parse_args()
    code = run(gpus=args.gpus, json_output=args.json)
    sys.exit(code)


if __name__ == "__main__":
    main_entry()
