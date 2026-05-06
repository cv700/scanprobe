"""
Terminal output for ashiba-scanprobe.
Uses rich for pretty display if available; falls back to plain text otherwise.
"""

import json
import sys
from typing import List, Optional

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich import box
    _RICH = True
    console = Console()
except ImportError:
    _RICH = False
    console = None

try:
    from dataclasses import asdict
    _DATACLASSES = True
except ImportError:
    _DATACLASSES = False

from .scoring import RiskScore

_TIER_COLOR = {"HEALTHY": "green", "WATCH": "yellow", "DRAIN": "red"}
_TIER_ICON  = {"HEALTHY": "✓", "WATCH": "⚠", "DRAIN": "✗"}


# ── Plain-text fallbacks ─────────────────────────────────────────────────────

def _plain_print(msg: str):
    # Strip rich markup tags for plain output
    import re
    sys.stdout.write(re.sub(r"\[/?[^\]]*\]", "", msg) + "\n")


def _fmt_tier_plain(tier: str) -> str:
    return f"{_TIER_ICON.get(tier,'?')} {tier}"


# ── Inline formatters (rich markup strings) ──────────────────────────────────

def _fmt_tier(tier: str) -> str:
    c = _TIER_COLOR.get(tier, "white")
    i = _TIER_ICON.get(tier, "?")
    return f"[bold {c}]{i} {tier}[/bold {c}]"

def _fmt_score(score: float) -> str:
    c = "red" if score >= 0.50 else "yellow" if score >= 0.20 else "green"
    return f"[{c}]{score:.2f}[/{c}]"

def _fmt_ecc(nv) -> str:
    if nv is None: return "[dim]n/a[/dim]"
    dbe = nv.ecc_dbe_volatile + nv.ecc_dbe_aggregate
    sbe = nv.ecc_sbe_volatile
    if dbe > 0:  return f"[bold red]DBE:{dbe}[/bold red]"
    if sbe > 10: return f"[yellow]SBE:{sbe}[/yellow]"
    return "[green]clean[/green]"

def _fmt_temp(nv) -> str:
    if nv is None or nv.temperature_gpu is None: return "[dim]n/a[/dim]"
    t = nv.temperature_gpu
    if t > 88:  return f"[bold red]{t:.0f}°C[/bold red]"
    if t > 83:  return f"[yellow]{t:.0f}°C[/yellow]"
    return f"[green]{t:.0f}°C[/green]"

def _fmt_matmul(mm) -> str:
    if mm is None: return "[dim]n/a[/dim]"
    if mm.error: return "[yellow]error[/yellow]"
    n_bad = mm.num_shapes_anomalous
    n_tot = mm.num_shapes_tested
    if n_bad > 0: return f"[red]{n_bad}/{n_tot} anomalous[/red]"
    tf = f" {mm.tflops_fp16:.1f}TF" if mm.tflops_fp16 else ""
    return f"[green]clean{tf}[/green]"

def _fmt_dcgm(dc) -> str:
    if dc is None: return "[dim]n/a[/dim]"
    if not dc.available: return "[dim]n/a[/dim]"
    if not dc.passed: return f"[red]FAIL ({len(dc.failed_tests)})[/red]"
    return f"[green]pass r{dc.level}[/green]"

def _fmt_xid(xr) -> str:
    if xr is None: return "[dim]n/a[/dim]"
    if not xr.available: return "[dim]n/a[/dim]"
    if xr.drain_xids_found:
        codes = ",".join(str(x) for x in xr.drain_xids_found)
        return f"[bold red]Xid {codes}[/bold red]"
    if xr.watch_xids_found:
        codes = ",".join(str(x) for x in xr.watch_xids_found)
        return f"[yellow]Xid {codes}[/yellow]"
    return "[green]clean[/green]"

def _fmt_collective(cr, gpu_index: int) -> str:
    if cr is None: return "[dim]n/a[/dim]"
    if cr.error: return "[yellow]error[/yellow]"
    if cr.world_size <= 1: return "[dim]single[/dim]"
    p50 = cr.rank_p50_ms.get(gpu_index)
    if p50 is None: return "[dim]n/a[/dim]"
    outlier_ids = {o["rank"] for o in (cr.outlier_ranks or [])}
    if gpu_index in outlier_ids:
        match = next(o for o in cr.outlier_ranks if o["rank"] == gpu_index)
        sigma = match.get("sigma_above_median", 0)
        return f"[red]{p50:.0f}ms +{sigma:.1f}σ[/red]"
    return f"[green]{p50:.0f}ms[/green]"


# ── Public print functions ────────────────────────────────────────────────────

def print_run_header(n_gpus: int, tier: int):
    tier_times = {1: "~30s", 2: "~10 min", 3: "~30 min"}
    duration = tier_times.get(tier, "")
    if _RICH:
        console.print()
        console.rule(
            f"[bold cyan]ASHIBA PRE-FLIGHT[/bold cyan]  "
            f"[white]{n_gpus} GPU{'s' if n_gpus != 1 else ''}[/white]  "
            f"[dim]Tier {tier} {duration}[/dim]"
        )
        console.print()
    else:
        print(f"\n── ASHIBA PRE-FLIGHT  {n_gpus} GPU{'s' if n_gpus != 1 else ''}  Tier {tier} {duration} ──\n")


def print_results_table(
    risk_scores: List[RiskScore],
    nvidia_results: dict,
    dcgm_result=None,
    matmul_results: dict = None,
    collective_result=None,
    xid_result=None,
):
    matmul_results = matmul_results or {}

    if _RICH:
        table = Table(box=box.SIMPLE_HEAD, show_header=True,
                      header_style="bold dim", padding=(0, 1))
        table.add_column("GPU",        width=5,  style="bold")
        table.add_column("Status",     width=14)
        table.add_column("Score",      width=7)
        table.add_column("ECC/Xid",    width=14)
        table.add_column("Temp",       width=9)
        table.add_column("Matmul",     width=20)
        table.add_column("DCGM",       width=14)
        table.add_column("Collective", width=18)

        for rs in sorted(risk_scores, key=lambda r: r.gpu_index):
            idx = rs.gpu_index
            # Xid is node-level — show on every GPU row if present
            ecc_xid = _fmt_ecc(nvidia_results.get(idx))
            if xid_result and (xid_result.drain_xids_found or xid_result.watch_xids_found):
                ecc_xid = _fmt_xid(xid_result)

            table.add_row(
                str(idx),
                Text.from_markup(_fmt_tier(rs.tier)),
                Text.from_markup(_fmt_score(rs.score)),
                Text.from_markup(ecc_xid),
                Text.from_markup(_fmt_temp(nvidia_results.get(idx))),
                Text.from_markup(_fmt_matmul(matmul_results.get(idx))),
                Text.from_markup(_fmt_dcgm(dcgm_result)),
                Text.from_markup(_fmt_collective(collective_result, idx)),
            )
        console.print(table)
    else:
        # Plain-text table
        header = f"{'GPU':<5} {'Status':<12} {'Score':<6} {'Temp':<8} {'ECC':<10}"
        print(header)
        print("-" * len(header))
        for rs in sorted(risk_scores, key=lambda r: r.gpu_index):
            nv = nvidia_results.get(rs.gpu_index)
            temp = f"{nv.temperature_gpu:.0f}°C" if nv and nv.temperature_gpu else "n/a"
            dbe = (nv.ecc_dbe_volatile + nv.ecc_dbe_aggregate) if nv else 0
            ecc = f"DBE:{dbe}" if dbe else "clean"
            print(f"{rs.gpu_index:<5} {_fmt_tier_plain(rs.tier):<12} {rs.score:<6.2f} {temp:<8} {ecc:<10}")
        print()


def print_collective_summary(collective_result):
    if collective_result is None or collective_result.world_size <= 1:
        return
    if collective_result.error:
        msg = f"Collective error: {collective_result.error}"
        if _RICH: console.print(f"[yellow]{msg}[/yellow]")
        else: print(msg)
        return

    parts = []
    if collective_result.cluster_median_ms is not None:
        parts.append(f"p50 {collective_result.cluster_median_ms:.0f}ms")
    if collective_result.cluster_p99_ms is not None:
        parts.append(f"p99 {collective_result.cluster_p99_ms:.0f}ms")
    bw = next(iter(collective_result.bus_bandwidth_gib_s.values()), None)
    if bw:
        parts.append(f"bus BW {bw:.1f} GiB/s")
    if parts:
        line = "Collective (allreduce 16M×f32): " + "  ".join(parts)
        if _RICH: console.print(f"[dim]{line}[/dim]")
        else: print(line)
        if _RICH: console.print()


def print_xid_summary(xid_result):
    if xid_result is None or not xid_result.available:
        return
    if not xid_result.events:
        return
    drain = [e for e in xid_result.events if e["severity"] == "DRAIN"]
    watch = [e for e in xid_result.events if e["severity"] == "WATCH"]
    if drain:
        lines = [f"[bold red]Xid hardware errors in dmesg:[/bold red]"]
        for e in drain[:5]:
            lines.append(f"  Xid {e['xid']} — {e['description']}  [{e['pci']}]")
        if _RICH: console.print(Panel("\n".join(lines), border_style="red", padding=(0,1)))
        else: print("\n".join(l.replace("[bold red]","").replace("[/bold red]","") for l in lines))
    elif watch:
        if _RICH: console.print(f"[yellow]Xid watch events: {', '.join(str(e['xid']) for e in watch[:5])}[/yellow]")
        else: print(f"Xid watch events: {', '.join(str(e['xid']) for e in watch[:5])}")


def print_recommendations(risk_scores: List[RiskScore]):
    drain = [rs for rs in risk_scores if rs.tier == "DRAIN"]
    watch = [rs for rs in risk_scores if rs.tier == "WATCH"]

    if _RICH:
        if drain:
            lines = ["[bold red]DRAIN before launch[/bold red]"]
            for rs in drain:
                lines.append(f"  GPU {rs.gpu_index} (score {rs.score:.2f})")
                for rec in rs.recommendations:
                    lines.append(f"    · {rec}")
            console.print(Panel("\n".join(lines), border_style="red", padding=(0, 1)))
        if watch:
            lines = ["[bold yellow]WATCH — monitor closely[/bold yellow]"]
            for rs in watch:
                lines.append(f"  GPU {rs.gpu_index} (score {rs.score:.2f})")
                for rec in rs.recommendations:
                    lines.append(f"    · {rec}")
            console.print(Panel("\n".join(lines), border_style="yellow", padding=(0, 1)))
        if not drain and not watch:
            console.print(Panel(
                "[bold green]All GPUs HEALTHY — safe to launch.[/bold green]",
                border_style="green", padding=(0, 1)
            ))
        console.print()
    else:
        if drain:
            print("DRAIN before launch:")
            for rs in drain:
                print(f"  GPU {rs.gpu_index} ({rs.score:.2f})")
                for rec in rs.recommendations:
                    print(f"    - {rec}")
        if watch:
            print("WATCH:")
            for rs in watch:
                print(f"  GPU {rs.gpu_index} ({rs.score:.2f})")
                for rec in rs.recommendations:
                    print(f"    - {rec}")
        if not drain and not watch:
            print("All GPUs HEALTHY — safe to launch.")
        print()


def print_json_output(risk_scores, nvidia_results, dcgm_result, matmul_results, collective_result, xid_result=None):
    def _safe_asdict(obj):
        if obj is None:
            return None
        if _DATACLASSES:
            try:
                return asdict(obj)
            except Exception:
                pass
        return str(obj)

    out = {
        "risk_scores": [_safe_asdict(rs) for rs in risk_scores],
        "collective": _safe_asdict(collective_result),
        "xid": _safe_asdict(xid_result),
    }
    print(json.dumps(out, indent=2))
