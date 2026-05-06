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

DRAIN_LINE_94 = (
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
    "[12345.678] kernel: NVRM: Xid (PCI:0000:3b:00): 94, Ch 00000008"
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
    # 48=DBE ECC, 63=row remap fail, 74=NVLink, 79=engine hang, 94=GPC fault, 95=uncontained
    for code in [48, 63, 74, 79, 94, 95]:
        assert code in DRAIN_XIDS, f"Xid {code} should be DRAIN class"

def test_known_watch_codes_present():
    """Watch-class codes from NVIDIA documentation."""
    for code in [43, 31, 92]:
        assert code in WATCH_XIDS, f"Xid {code} should be WATCH class"

def test_all_drain_codes_have_descriptions():
    for code in DRAIN_XIDS:
        assert code in XID_DESC, f"Xid {code} (DRAIN) missing description"

def test_all_watch_codes_have_descriptions():
    for code in WATCH_XIDS:
        assert code in XID_DESC, f"Xid {code} (WATCH) missing description"


# ── Regex parsing tests ──────────────────────────────────────────────────────

def test_drain_xid_94_detected():
    result = _run_with_dmesg([DRAIN_LINE_94])
    assert result.available
    assert 94 in result.drain_xids_found
    assert not result.passed

def test_drain_xid_79_detected():
    result = _run_with_dmesg([DRAIN_LINE_79])
    assert 79 in result.drain_xids_found

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
    assert 94 in result.drain_xids_found, \
        "Xid line with 'kernel:' prefix should be parsed"

def test_multiple_drain_events_deduplicated():
    """Same Xid from same PCI should not appear twice in drain_xids_found."""
    result = _run_with_dmesg([DRAIN_LINE_94, DRAIN_LINE_94, DRAIN_LINE_94])
    assert result.drain_xids_found.count(94) == 1

def test_mixed_drain_and_watch():
    result = _run_with_dmesg([DRAIN_LINE_94, WATCH_LINE_43])
    assert 94 in result.drain_xids_found
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
