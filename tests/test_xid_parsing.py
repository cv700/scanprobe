"""
Tests for Xid dmesg parsing.

These tests validate the regex and classification logic against known dmesg
line formats. When real dmesg output is available from a GPU node, add those
exact lines as additional fixtures here.

All tests are hardware-free.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ashiba_scanprobe.checks.xid import check_xid, DRAIN_XIDS, WATCH_XIDS, XID_DESCRIPTIONS as XID_DESC
import unittest
from unittest.mock import patch


# ── Known dmesg line formats ─────────────────────────────────────────────────
# Format observed in kernel documentation and reports.
# When real hardware output is available, add those exact lines here.

DRAIN_LINE_95 = (
    "NVRM: Xid (PCI:0000:01:00): 95, pid=7062, "
    "Uncontained: LTC TAG (0x2,0x0). RST: Yes, D-RST: No"
)
WATCH_LINE_94 = (
    "[12345.678] NVRM: Xid (PCI:0000:3b:00): 94, pid='<unknown>', "
    "name=<unknown>, Ch 00000008"
)
DRAIN_LINE_79 = (
    "[12345.678] NVRM: Xid (PCI:0000:3b:00): 79, pid=1234, "
    "name=python, Ch 00000001"
)
WATCH_LINE_43 = (
    "[12345.678] NVRM: Xid (PCI:0000:3b:00): 43, pid=1234, "
    "name=python, Ch 00000002"
)
INFO_LINE_UNKNOWN = (
    "[12345.678] NVRM: Xid (PCI:0000:3b:00): 999, misc info"
)
NON_XID_LINE = (
    "[12345.678] NVRM: GPU-0000:3b:00: RmInitAdapter failed!"
)
KERNEL_PREFIX_LINE = (
    "[12345.678] kernel: NVRM: Xid (PCI:0000:3b:00): 79, Ch 00000008"
)
RAW_LINE_13 = (
    "NVRM: Xid (PCI:0000:01:00): 13, Graphics SM Warp Exception on "
    "(GPC 1, TPC 0): Out Of Range Address"
)
THREAD_PREFIX_LINE_31 = (
    "[ 130.456250] [ T1946] NVRM: Xid (PCI:0000:2b:00): 31, "
    "pid=1942, name=llama-bench, channel 0x00000002, intr 00000000. "
    "MMU Fault: ENGINE GRAPHICS"
)
SYSLOG_LINE_38 = (
    "Feb 01 14:25:31 archbox kernel: NVRM: Xid (PCI:0000:01:00): 38, pid=1659,"
)
ARM_DOMAIN_LINE_119 = (
    "NVRM: Xid (PCI:0004:01:00): 119, pid=430, name=nv_queue, "
    "Timeout waiting for RPC from GSP!"
)
ROW_REMAP_SUCCESS_LINE_63 = (
    "NVRM: Xid (PCI:0000:10:1c): 63, pid=1896, "
    "Row Remapper: New row marked for remapping, reset gpu to activate."
)
ROW_REMAP_FAILURE_LINE_64 = (
    "[12345.678] NVRM: Xid (PCI:0000:3b:00): 64, "
    "Row Remapper: recording failure"
)
ECC_ESCAPE_LINE_140 = (
    "[12345.678] NVRM: Xid (PCI:0000:3b:00): 140, "
    "Unrecoverable ECC error escape"
)
GPU_INIT_LINE_143 = (
    "[12345.678] NVRM: Xid (PCI:0000:3b:00): 143, GPU init error"
)


def _run_with_dmesg(lines: list):
    """Run check_xid() with mocked dmesg output."""
    fake_output = "\n".join(lines)
    import subprocess
    fake_proc = unittest.mock.MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = fake_output

    with patch("subprocess.run", return_value=fake_proc):
        return check_xid()


# ── Classification tests ─────────────────────────────────────────────────────

def test_drain_xids_are_drain_class():
    """Every code in DRAIN_XIDS must be drain-class, not watch."""
    assert DRAIN_XIDS.isdisjoint(WATCH_XIDS), \
        "A code cannot be both DRAIN and WATCH"

def test_known_drain_codes_present():
    """The critical codes documented in NVIDIA literature must be drain-class."""
    # 48=DBE ECC, 64=row remap fail, 74=NVLink, 79=fallen off bus,
    # 95=uncontained, 140=ECC escape, 143=GPU init error.
    for code in [48, 64, 74, 79, 95, 140, 143]:
        assert code in DRAIN_XIDS, f"Xid {code} should be DRAIN class"

def test_known_watch_codes_present():
    """Watch-class codes from NVIDIA documentation."""
    for code in [13, 31, 43, 63, 92, 94, 109, 119, 120]:
        assert code in WATCH_XIDS, f"Xid {code} should be WATCH class"

def test_unused_legacy_codes_removed_from_watch():
    """NVIDIA marks 56/57/58/61 unused on current datacenter architectures."""
    for code in [56, 57, 58, 61]:
        assert code not in WATCH_XIDS

def test_all_drain_codes_have_descriptions():
    for code in DRAIN_XIDS:
        assert code in XID_DESC, f"Xid {code} (DRAIN) missing description"

def test_all_watch_codes_have_descriptions():
    for code in WATCH_XIDS:
        assert code in XID_DESC, f"Xid {code} (WATCH) missing description"


# ── Regex parsing tests ──────────────────────────────────────────────────────

def test_drain_xid_95_detected():
    result = _run_with_dmesg([DRAIN_LINE_95])
    assert result.available
    assert 95 in result.drain_xids_found
    assert not result.passed

def test_drain_xid_79_detected():
    result = _run_with_dmesg([DRAIN_LINE_79])
    assert 79 in result.drain_xids_found

def test_watch_xid_94_detected():
    result = _run_with_dmesg([WATCH_LINE_94])
    assert 94 in result.watch_xids_found
    assert 94 not in result.drain_xids_found
    assert result.passed

def test_watch_xid_43_detected():
    result = _run_with_dmesg([WATCH_LINE_43])
    assert 43 in result.watch_xids_found
    assert result.passed  # watch alone does not set passed=False

def test_unknown_xid_parsed_but_not_classified():
    result = _run_with_dmesg([INFO_LINE_UNKNOWN])
    assert len(result.events) == 1
    assert result.events[0]["xid"] == 999
    assert result.events[0]["severity"] == "INFO"
    assert not result.drain_xids_found
    assert not result.watch_xids_found

def test_non_xid_line_ignored():
    result = _run_with_dmesg([NON_XID_LINE])
    assert len(result.events) == 0

def test_kernel_prefix_format_parsed():
    """Some kernels prefix lines with 'kernel: NVRM: ...'"""
    result = _run_with_dmesg([KERNEL_PREFIX_LINE])
    assert 79 in result.drain_xids_found, \
        "Xid line with 'kernel:' prefix should be parsed"

def test_real_fixture_raw_line_without_timestamp_parsed():
    result = _run_with_dmesg([RAW_LINE_13])
    assert 13 in result.watch_xids_found

def test_real_fixture_thread_prefix_parsed():
    result = _run_with_dmesg([THREAD_PREFIX_LINE_31])
    assert 31 in result.watch_xids_found

def test_real_fixture_syslog_prefix_unknown_info_parsed():
    result = _run_with_dmesg([SYSLOG_LINE_38])
    assert len(result.events) == 1
    assert result.events[0]["xid"] == 38
    assert result.events[0]["severity"] == "INFO"

def test_real_fixture_arm_domain_gsp_timeout_parsed():
    result = _run_with_dmesg([ARM_DOMAIN_LINE_119])
    assert 119 in result.watch_xids_found
    assert result.events[0]["pci"] == "0004:01:00"

def test_xid_63_row_remap_success_is_watch_not_drain():
    result = _run_with_dmesg([ROW_REMAP_SUCCESS_LINE_63])
    assert 63 in result.watch_xids_found
    assert 63 not in result.drain_xids_found
    assert result.passed

def test_xid_64_row_remap_failure_is_drain():
    result = _run_with_dmesg([ROW_REMAP_FAILURE_LINE_64])
    assert 64 in result.drain_xids_found
    assert not result.passed

def test_xid_140_ecc_escape_is_drain():
    result = _run_with_dmesg([ECC_ESCAPE_LINE_140])
    assert 140 in result.drain_xids_found
    assert not result.passed

def test_xid_143_gpu_init_is_drain():
    result = _run_with_dmesg([GPU_INIT_LINE_143])
    assert 143 in result.drain_xids_found
    assert not result.passed

def test_multiple_drain_events_deduplicated():
    """Same Xid from same PCI should not appear twice in drain_xids_found."""
    result = _run_with_dmesg([DRAIN_LINE_95, DRAIN_LINE_95, DRAIN_LINE_95])
    assert result.drain_xids_found.count(95) == 1

def test_mixed_drain_and_watch():
    result = _run_with_dmesg([DRAIN_LINE_95, WATCH_LINE_43])
    assert 95 in result.drain_xids_found
    assert 43 in result.watch_xids_found
    assert not result.passed

def test_empty_dmesg_is_healthy():
    result = _run_with_dmesg([])
    assert result.available
    assert result.passed
    assert not result.drain_xids_found
    assert not result.watch_xids_found

def test_dmesg_unavailable_does_not_penalize():
    """If dmesg fails, available=False and passed=True (absence of data ≠ fault)."""
    import subprocess
    fake_proc = unittest.mock.MagicMock()
    fake_proc.returncode = 1
    fake_proc.stdout = ""
    fake_proc.stderr = "Operation not permitted"

    with patch("subprocess.run", return_value=fake_proc):
        result = check_xid()

    assert not result.available
    assert result.passed  # unavailable ≠ fault
    assert result.log_source == "unavailable-restricted"
    assert "try: sudo scanprobe" in result.error


# ── Placeholder for real hardware fixtures ───────────────────────────────────
# When real dmesg output is available, add fixtures here.
# Example:
#
# REAL_H100_CLEAN_DMESG = """
# [paste exact dmesg output here]
# """
#
# def test_real_h100_clean_parses_no_xids():
#     result = _run_with_dmesg(REAL_H100_CLEAN_DMESG.splitlines())
#     assert not result.drain_xids_found
#     assert not result.watch_xids_found


# ── Run ──────────────────────────────────────────────────────────────────────

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
