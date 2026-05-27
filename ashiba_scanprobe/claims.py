"""
Synthetic capacity-acceptance claim evaluation.

This module intentionally avoids provider APIs. It evaluates a packet that binds
an order, allocation, machine evidence, health evidence, and probe manifest into
a capacity delivery receipt.
"""

from dataclasses import dataclass, field, asdict
from typing import Any, Optional
import json
import sys


SUPPORTED = "SUPPORTED"
UNKNOWN = "UNKNOWN"
CONTRADICTED = "CONTRADICTED"

_STATUS_RANK = {
    SUPPORTED: 0,
    UNKNOWN: 1,
    CONTRADICTED: 2,
}


@dataclass
class PassResult:
    name: str
    status: str
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class ClaimEvaluation:
    claim_pack: str
    verdict: str
    passes: list

    def asdict(self) -> dict:
        return {
            "claim_pack": self.claim_pack,
            "verdict": self.verdict,
            "passes": [asdict(p) for p in self.passes],
        }


def _get(packet: dict, path: str, default: Any = None) -> Any:
    cur = packet
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _present(value: Any) -> bool:
    return value is not None and value != ""


def _same(expected: Any, observed: Any) -> bool:
    if isinstance(expected, str) and isinstance(observed, str):
        return expected.strip().lower() == observed.strip().lower()
    return expected == observed


def _unknown(name: str, message: str, details: Optional[dict] = None) -> PassResult:
    return PassResult(name=name, status=UNKNOWN, message=message, details=details or {})


def _supported(name: str, message: str, details: Optional[dict] = None) -> PassResult:
    return PassResult(name=name, status=SUPPORTED, message=message, details=details or {})


def _contradicted(name: str, message: str, details: Optional[dict] = None) -> PassResult:
    return PassResult(name=name, status=CONTRADICTED, message=message, details=details or {})


def _pass_order_id_present(packet: dict) -> PassResult:
    reservation_id = _get(packet, "order.reservation_id")
    order_id = _get(packet, "order.order_id")
    if _present(reservation_id) or _present(order_id):
        return _supported(
            "reservation_order_id_present",
            "reservation/order identifier is present",
            {"reservation_id": reservation_id, "order_id": order_id},
        )
    return _unknown(
        "reservation_order_id_present",
        "no reservation/order identifier was provided",
    )


def _pass_gpu_count_match(packet: dict) -> PassResult:
    expected = _get(packet, "order.gpu_count")
    observed = _get(packet, "allocation.gpu_count")
    if observed is None:
        observed = _get(packet, "machine_evidence.nvidia_smi.gpu_count")

    if expected is None or observed is None:
        return _unknown(
            "gpu_count_match",
            "missing order or machine/allocation GPU count",
            {"expected": expected, "observed": observed},
        )
    if expected != observed:
        return _contradicted(
            "gpu_count_match",
            "delivered GPU count conflicts with order",
            {"expected": expected, "observed": observed},
        )
    return _supported(
        "gpu_count_match",
        "delivered GPU count matches order",
        {"expected": expected, "observed": observed},
    )


def _pass_gpu_sku_match(packet: dict) -> PassResult:
    expected_sku = _get(packet, "order.gpu_sku")
    expected_class = _get(packet, "order.gpu_class")
    observed_sku = _get(packet, "allocation.gpu_sku")
    observed_class = _get(packet, "allocation.gpu_class")

    if observed_sku is None:
        observed_sku = _get(packet, "machine_evidence.nvidia_smi.gpu_sku")
    if observed_class is None:
        observed_class = _get(packet, "machine_evidence.nvidia_smi.gpu_class")

    if expected_sku is None and expected_class is None:
        return _unknown(
            "gpu_sku_class_match",
            "order does not declare a GPU SKU or class",
        )
    if observed_sku is None and observed_class is None:
        return _unknown(
            "gpu_sku_class_match",
            "missing allocation or nvidia-smi GPU SKU/class evidence",
            {
                "expected_sku": expected_sku,
                "expected_class": expected_class,
            },
        )

    sku_matches = expected_sku is not None and observed_sku is not None and _same(expected_sku, observed_sku)
    class_matches = expected_class is not None and observed_class is not None and _same(expected_class, observed_class)

    if sku_matches or class_matches:
        return _supported(
            "gpu_sku_class_match",
            "delivered GPU SKU/class matches order",
            {
                "expected_sku": expected_sku,
                "observed_sku": observed_sku,
                "expected_class": expected_class,
                "observed_class": observed_class,
            },
        )

    return _contradicted(
        "gpu_sku_class_match",
        "delivered GPU SKU/class conflicts with order",
        {
            "expected_sku": expected_sku,
            "observed_sku": observed_sku,
            "expected_class": expected_class,
            "observed_class": observed_class,
        },
    )


def _pass_region_zone_match(packet: dict) -> PassResult:
    expected_region = _get(packet, "order.region")
    expected_zone = _get(packet, "order.zone")
    observed_region = _get(packet, "allocation.region")
    observed_zone = _get(packet, "allocation.zone")

    if observed_region is None:
        observed_region = _get(packet, "machine_evidence.nvidia_smi.region")
    if observed_zone is None:
        observed_zone = _get(packet, "machine_evidence.nvidia_smi.zone")

    if expected_region is None and expected_zone is None:
        return _unknown("region_zone_match", "order does not declare a region or zone")
    if observed_region is None and observed_zone is None:
        return _unknown(
            "region_zone_match",
            "missing allocation or machine region/zone evidence",
            {"expected_region": expected_region, "expected_zone": expected_zone},
        )

    if expected_region is not None and observed_region is not None and not _same(expected_region, observed_region):
        return _contradicted(
            "region_zone_match",
            "delivered region conflicts with order",
            {
                "expected_region": expected_region,
                "observed_region": observed_region,
                "expected_zone": expected_zone,
                "observed_zone": observed_zone,
            },
        )
    if expected_zone is not None and observed_zone is not None and not _same(expected_zone, observed_zone):
        return _contradicted(
            "region_zone_match",
            "delivered zone conflicts with order",
            {
                "expected_region": expected_region,
                "observed_region": observed_region,
                "expected_zone": expected_zone,
                "observed_zone": observed_zone,
            },
        )

    return _supported(
        "region_zone_match",
        "delivered region/zone matches order",
        {
            "expected_region": expected_region,
            "observed_region": observed_region,
            "expected_zone": expected_zone,
            "observed_zone": observed_zone,
        },
    )


def _pass_dcgm_diagnostic(packet: dict) -> PassResult:
    dcgm = _get(packet, "health_evidence.dcgm")
    if not isinstance(dcgm, dict):
        return _unknown("dcgm_diagnostic_pass", "missing DCGM diagnostic evidence")

    level = dcgm.get("diagnostic_level")
    result = dcgm.get("result")
    if not _present(level) or not _present(result):
        return _unknown(
            "dcgm_diagnostic_pass",
            "DCGM evidence is missing declared diagnostic level or result",
            {"diagnostic_level": level, "result": result},
        )

    if str(result).strip().upper() != "PASS":
        return _contradicted(
            "dcgm_diagnostic_pass",
            "DCGM diagnostic did not pass",
            {"diagnostic_level": level, "result": result},
        )

    return _supported(
        "dcgm_diagnostic_pass",
        "declared DCGM diagnostic passed",
        {"diagnostic_level": level, "result": result},
    )


def _pass_probe_manifest_bound(packet: dict) -> PassResult:
    manifest = _get(packet, "probe_manifest")
    if not isinstance(manifest, dict):
        return _unknown(
            "probe_manifest_present_and_bound",
            "missing probe manifest binding artifact",
        )

    reservation_id = _get(packet, "order.reservation_id")
    allocation_id = _get(packet, "allocation.allocation_id")
    machine_id = _get(packet, "allocation.machine_id")
    manifest_reservation = manifest.get("reservation_id")
    manifest_allocation = manifest.get("allocation_id")
    manifest_machine = manifest.get("machine_id")

    missing = []
    if not _present(manifest.get("manifest_id")):
        missing.append("manifest_id")
    if reservation_id is not None and not _present(manifest_reservation):
        missing.append("reservation_id")
    if allocation_id is not None and not _present(manifest_allocation):
        missing.append("allocation_id")
    if machine_id is not None and not _present(manifest_machine):
        missing.append("machine_id")
    if missing:
        return _unknown(
            "probe_manifest_present_and_bound",
            "probe manifest is present but missing binding fields",
            {"missing": missing},
        )

    conflicts = {}
    if reservation_id is not None and not _same(reservation_id, manifest_reservation):
        conflicts["reservation_id"] = {"expected": reservation_id, "observed": manifest_reservation}
    if allocation_id is not None and not _same(allocation_id, manifest_allocation):
        conflicts["allocation_id"] = {"expected": allocation_id, "observed": manifest_allocation}
    if machine_id is not None and not _same(machine_id, manifest_machine):
        conflicts["machine_id"] = {"expected": machine_id, "observed": manifest_machine}

    if conflicts:
        return _contradicted(
            "probe_manifest_present_and_bound",
            "probe manifest is bound to the wrong reservation/allocation/machine",
            conflicts,
        )

    return _supported(
        "probe_manifest_present_and_bound",
        "probe manifest is present and bound to this order/allocation",
        {
            "manifest_id": manifest.get("manifest_id"),
            "reservation_id": manifest_reservation,
            "allocation_id": manifest_allocation,
            "machine_id": manifest_machine,
        },
    )


PASSES = [
    _pass_order_id_present,
    _pass_gpu_count_match,
    _pass_gpu_sku_match,
    _pass_region_zone_match,
    _pass_dcgm_diagnostic,
    _pass_probe_manifest_bound,
]


def evaluate_gpu_capacity_delivery_match(packet: dict) -> ClaimEvaluation:
    """Evaluate gpu_capacity_delivery_match_v0 against a synthetic packet."""
    pass_results = [fn(packet) for fn in PASSES]
    verdict = max((p.status for p in pass_results), key=lambda s: _STATUS_RANK[s])
    return ClaimEvaluation(
        claim_pack="gpu_capacity_delivery_match_v0",
        verdict=verdict,
        passes=pass_results,
    )


def main(argv=None) -> int:
    """Evaluate one or more synthetic packet JSON files."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m ashiba_scanprobe.claims",
        description="Evaluate synthetic GPU capacity acceptance receipt packets.",
    )
    parser.add_argument("packets", nargs="+", help="packet JSON file(s) to evaluate")
    args = parser.parse_args(argv)

    for path in args.packets:
        with open(path, "r", encoding="utf-8") as f:
            packet = json.load(f)
        evaluation = evaluate_gpu_capacity_delivery_match(packet)
        print(json.dumps({
            "packet_id": packet.get("packet_id"),
            **evaluation.asdict(),
        }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
