"""
ashiba-scanprobe — GPU cluster health check.

Zero mandatory dependencies. Works on any GPU node with Python + nvidia-smi.
Install rich for pretty output: pip install ashiba-scanprobe[display]
Install torch for matmul/collective checks: pip install ashiba-scanprobe[full]

Usage:
  ashiba-scanprobe                 # Tier 1 on all GPUs (~30s)
  ashiba-scanprobe --tier 2        # Add matmul + collective (~10 min)
  ashiba-scanprobe --gpus 0,1      # Specific GPUs
  ashiba-scanprobe --json          # Machine-readable output
  uvx ashiba-scanprobe             # Run without installing

Exit codes:  0=HEALTHY  1=WATCH  2=DRAIN  3=error
"""

import sys
import time
import contextlib

# Typer is optional — fall back to argparse if not installed
try:
    import typer
    _TYPER = True
    app = typer.Typer(
        name="ashiba-scanprobe",
        help="GPU cluster health check — per-GPU risk scoring before launch.",
        add_completion=False,
    )
except ImportError:
    _TYPER = False
    app = None

from .checks.nvidia_smi import check_all_nvidia_smi, count_gpus
from .checks.xid import check_xid
from .checks.dcgm import check_dcgm
from .scoring import compute_risk_score, WATCH_THRESHOLD, DRAIN_THRESHOLD
from .report import (
    print_run_header,
    print_results_table,
    print_collective_summary,
    print_xid_summary,
    print_recommendations,
    print_json_output,
    _RICH,
    console,
)


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


def _make_progress(label: str):
    """Return a context manager that shows progress if rich is available."""
    if _RICH:
        from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
        return Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        )
    return contextlib.nullcontext()


def run(
    tier: int = 1,
    gpus: str = "all",
    threshold: float = DRAIN_THRESHOLD,
    json_output: bool = False,
    skip_matmul: bool = False,
    skip_collective: bool = False,
    collective_timeout: int = 120,
):
    """Core logic — callable programmatically or from CLI."""
    t_start = time.time()

    # ── Discover GPUs ────────────────────────────────────────────────────────
    n_available = count_gpus()
    if n_available == 0:
        msg = "No CUDA GPUs found via nvidia-smi."
        if not json_output:
            print(msg)
        else:
            import json; print(json.dumps({"error": msg}))
        return 3

    try:
        gpu_indices = _parse_gpu_list(gpus, n_available)
    except (ValueError, TypeError) as e:
        print(f"Invalid --gpus argument: {e}")
        return 3

    if not gpu_indices:
        print("No valid GPU indices.")
        return 3

    if not json_output:
        print_run_header(len(gpu_indices), tier)

    # ── Run checks ───────────────────────────────────────────────────────────
    nvidia_results = {}
    dcgm_result    = None
    matmul_results = {}
    collective_result = None
    xid_result     = None

    def _step(label):
        """Print a step label if no rich progress bar."""
        if not json_output and not _RICH:
            print(f"  {label}")

    # --- Tier 1: fast checks, zero dependencies ---

    # nvidia-smi: one subprocess for all GPUs
    _step("nvidia-smi...")
    nvidia_results = check_all_nvidia_smi(gpu_indices)

    # Xid: scan dmesg for hardware error codes
    _step("xid (dmesg)...")
    xid_result = check_xid()

    # DCGM tier 1 quick check (skipped if not available)
    if tier >= 1:
        _step(f"dcgm -r{tier}...")
        dcgm_result = check_dcgm(level=tier)

    # --- Tier 2+: matmul and collective (require torch) ---

    if tier >= 2 and not skip_matmul:
        try:
            from .checks.matmul import check_matmul
            _step(f"matmul ({len(gpu_indices)} GPU{'s' if len(gpu_indices)>1 else ''})...")
            quick = (tier == 2)
            for idx in gpu_indices:
                matmul_results[idx] = check_matmul(gpu_index=idx, quick=quick)
        except ImportError:
            if not json_output:
                msg = "  matmul skipped (pip install ashiba-scanprobe[matmul])"
                if _RICH: console.print(f"[dim]{msg}[/dim]")
                else: print(msg)

    if tier >= 2 and not skip_collective and len(gpu_indices) > 1:
        try:
            from .checks.collective import check_collective
            _step("collective latency...")
            collective_result = check_collective(
                n_gpus=len(gpu_indices),
                timeout=collective_timeout,
            )
        except ImportError:
            if not json_output:
                msg = "  collective skipped (pip install ashiba-scanprobe[collective])"
                if _RICH: console.print(f"[dim]{msg}[/dim]")
                else: print(msg)

    # ── Score each GPU ────────────────────────────────────────────────────────
    risk_scores = []
    for idx in gpu_indices:
        rs = compute_risk_score(
            nvidia_result=nvidia_results.get(idx),
            dcgm_result=dcgm_result,
            matmul_result=matmul_results.get(idx) or None,
            collective_result=collective_result,
            xid_result=xid_result,
            gpu_index=idx,
        )
        if threshold != DRAIN_THRESHOLD:
            if rs.score >= threshold:        rs.tier = "DRAIN"
            elif rs.score >= WATCH_THRESHOLD: rs.tier = "WATCH"
            else:                             rs.tier = "HEALTHY"
        risk_scores.append(rs)

    elapsed = time.time() - t_start

    # ── Output ────────────────────────────────────────────────────────────────
    if json_output:
        print_json_output(risk_scores, nvidia_results, dcgm_result,
                          matmul_results, collective_result, xid_result)
    else:
        print_results_table(risk_scores, nvidia_results, dcgm_result,
                            matmul_results, collective_result, xid_result)
        print_xid_summary(xid_result)
        print_collective_summary(collective_result)
        print_recommendations(risk_scores)
        msg = f"Completed in {elapsed:.1f}s"
        if _RICH: console.print(f"[dim]{msg}[/dim]\n")
        else: print(msg)

    # ── Exit code ─────────────────────────────────────────────────────────────
    tiers = {rs.tier for rs in risk_scores}
    if "DRAIN" in tiers:   return 2
    elif "WATCH" in tiers: return 1
    else:                  return 0


# ── Typer entry point ─────────────────────────────────────────────────────────

if _TYPER:
    @app.command()
    def main(
        tier: int = typer.Option(1, "--tier", "-t",
            help="1=fast/30s (default), 2=medium/10min, 3=full/30min", min=1, max=3),
        gpus: str = typer.Option("all", "--gpus", "-g",
            help="'all', '0', '0,1,2', or '0-3'"),
        threshold: float = typer.Option(DRAIN_THRESHOLD, "--threshold",
            help="DRAIN score threshold (default 0.50)"),
        json_output: bool = typer.Option(False, "--json",
            help="Machine-readable JSON output"),
        skip_matmul: bool = typer.Option(False, "--skip-matmul"),
        skip_collective: bool = typer.Option(False, "--skip-collective"),
        collective_timeout: int = typer.Option(120, "--collective-timeout"),
    ):
        code = run(tier, gpus, threshold, json_output,
                   skip_matmul, skip_collective, collective_timeout)
        raise typer.Exit(code=code)


# ── Argparse fallback (when typer not installed) ──────────────────────────────

def _argparse_main():
    import argparse
    p = argparse.ArgumentParser(
        prog="ashiba-scanprobe",
        description="GPU cluster health check. Exit: 0=HEALTHY 1=WATCH 2=DRAIN 3=error"
    )
    p.add_argument("--tier", type=int, default=1, choices=[1,2,3],
                   help="1=fast/30s (default), 2=medium/10min, 3=full/30min")
    p.add_argument("--gpus", default="all",
                   help="'all', '0', '0,1,2', or '0-3'")
    p.add_argument("--threshold", type=float, default=DRAIN_THRESHOLD)
    p.add_argument("--json", dest="json_output", action="store_true")
    p.add_argument("--skip-matmul", action="store_true")
    p.add_argument("--skip-collective", action="store_true")
    p.add_argument("--collective-timeout", type=int, default=120)
    args = p.parse_args()
    code = run(args.tier, args.gpus, args.threshold, args.json_output,
               args.skip_matmul, args.skip_collective, args.collective_timeout)
    sys.exit(code)


def main_entry():
    """Entry point that works with or without typer."""
    if _TYPER:
        app()
    else:
        _argparse_main()


if __name__ == "__main__":
    main_entry()
