"""
Tests for the synthetic GPU capacity acceptance receipt claim pack.
"""

import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ashiba_scanprobe.claims import (
    CONTRADICTED,
    SUPPORTED,
    UNKNOWN,
    evaluate_gpu_capacity_delivery_match,
)


ROOT = os.path.dirname(os.path.dirname(__file__))


def _load_demo(name: str) -> dict:
    path = os.path.join(ROOT, "demo_packets", name)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _pass_statuses(evaluation):
    return {p.name: p.status for p in evaluation.passes}


def test_supported_demo_packet_is_supported():
    packet = _load_demo("gpu_capacity_delivery_match_supported.json")
    evaluation = evaluate_gpu_capacity_delivery_match(packet)
    assert evaluation.verdict == SUPPORTED
    assert all(p.status == SUPPORTED for p in evaluation.passes)


def test_unknown_demo_packet_is_unknown_not_contradicted():
    packet = _load_demo("gpu_capacity_delivery_match_unknown.json")
    evaluation = evaluate_gpu_capacity_delivery_match(packet)
    statuses = _pass_statuses(evaluation)

    assert evaluation.verdict == UNKNOWN
    assert statuses["reservation_order_id_present"] == SUPPORTED
    assert CONTRADICTED not in statuses.values()


def test_contradicted_demo_packet_is_contradicted():
    packet = _load_demo("gpu_capacity_delivery_match_contradicted.json")
    evaluation = evaluate_gpu_capacity_delivery_match(packet)
    statuses = _pass_statuses(evaluation)

    assert evaluation.verdict == CONTRADICTED
    assert statuses["gpu_count_match"] == CONTRADICTED
    assert statuses["gpu_sku_class_match"] == CONTRADICTED
    assert statuses["region_zone_match"] == CONTRADICTED
    assert statuses["dcgm_diagnostic_pass"] == CONTRADICTED
    assert statuses["probe_manifest_present_and_bound"] == CONTRADICTED


def test_missing_probe_manifest_is_unknown():
    packet = _load_demo("gpu_capacity_delivery_match_supported.json")
    packet = copy.deepcopy(packet)
    del packet["probe_manifest"]

    evaluation = evaluate_gpu_capacity_delivery_match(packet)
    statuses = _pass_statuses(evaluation)

    assert evaluation.verdict == UNKNOWN
    assert statuses["probe_manifest_present_and_bound"] == UNKNOWN
    assert CONTRADICTED not in statuses.values()


def test_probe_manifest_wrong_allocation_is_contradicted():
    packet = _load_demo("gpu_capacity_delivery_match_supported.json")
    packet = copy.deepcopy(packet)
    packet["probe_manifest"]["allocation_id"] = "alloc-demo-wrong"

    evaluation = evaluate_gpu_capacity_delivery_match(packet)
    statuses = _pass_statuses(evaluation)

    assert evaluation.verdict == CONTRADICTED
    assert statuses["probe_manifest_present_and_bound"] == CONTRADICTED


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
