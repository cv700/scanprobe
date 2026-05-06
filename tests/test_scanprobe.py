import importlib.util
import io
import subprocess
import sys
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("scanprobe_script", ROOT / "scanprobe.py")
scanprobe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scanprobe)


def fake_proc(stdout="", stderr="", returncode=0):
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


def sample_smi_line(index=0, dbe=0, sbe=0, temp=72, throttle="0x0000000000000000"):
    return (
        f"{index}, NVIDIA H100 80GB HBM3, {sbe}, {dbe}, 0, {temp}, {throttle}"
    )


def test_parse_int_handles_na_and_commas():
    assert scanprobe._parse_int("N/A") == 0
    assert scanprobe._parse_int("[Not Supported]") == 0
    assert scanprobe._parse_int("1,234") == 1234


def test_parse_float_strips_units():
    assert scanprobe._parse_float("72 C") == 72.0
    assert scanprobe._parse_float("250.5 W") == 250.5
    assert scanprobe._parse_float("[N/A]") is None


def test_decode_throttle_reasons():
    assert scanprobe._decode_throttle("0x0000000000000000") == []
    assert "HwThermalSlowdown" in scanprobe._decode_throttle("0x0000000000000040")
    assert "SwPowerCap" in scanprobe._decode_throttle("0x0000000000000005")
    assert scanprobe._decode_throttle("not_hex") == []


def test_parse_smi_line_clear():
    gpu = scanprobe._parse_smi_line(sample_smi_line(), 0)
    assert gpu.index == 0
    assert gpu.name == "NVIDIA H100 80GB HBM3"
    assert gpu.passed
    assert gpu.temperature_gpu == 72.0


def test_parse_smi_line_dbe_marks_failed():
    gpu = scanprobe._parse_smi_line(sample_smi_line(dbe=1), 0)
    assert not gpu.passed
    assert gpu.ecc_dbe_volatile == 1


def test_parse_smi_line_bad_column_count():
    gpu = scanprobe._parse_smi_line("0, too, short", 0)
    assert not gpu.passed
    assert "column count" in gpu.error


def test_query_gpus_parses_requested_indices():
    proc = fake_proc(stdout=sample_smi_line(0) + "\n" + sample_smi_line(1) + "\n")
    with patch("subprocess.run", return_value=proc):
        results = scanprobe.query_gpus([0, 1])
    assert results[0].passed
    assert results[1].name == "NVIDIA H100 80GB HBM3"


def test_query_gpus_handles_missing_nvidia_smi():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        results = scanprobe.query_gpus([0])
    assert not results[0].passed
    assert "not found" in results[0].error


def test_query_gpus_handles_missing_requested_gpu():
    proc = fake_proc(stdout=sample_smi_line(0) + "\n")
    with patch("subprocess.run", return_value=proc):
        results = scanprobe.query_gpus([0, 7])
    assert results[0].passed
    assert not results[7].passed
    assert "not found" in results[7].error


def test_query_gpus_names_nvml_unknown_error():
    proc = fake_proc(stderr="Failed to initialize NVML: Unknown Error", returncode=1)
    with patch("subprocess.run", return_value=proc):
        results = scanprobe.query_gpus([0])
    assert not results[0].passed
    assert "Failed to initialize NVML" in results[0].error


def test_query_gpus_names_device_handle_error():
    stderr = "Unable to determine the device handle for GPU0000:B3:00.0: Unknown Error"
    proc = fake_proc(stderr=stderr, returncode=1)
    with patch("subprocess.run", return_value=proc):
        results = scanprobe.query_gpus([0])
    assert not results[0].passed
    assert "Unable to determine the device handle" in results[0].error


def test_count_gpus_handles_failure():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        assert scanprobe.count_gpus() == 0


def test_discover_gpus_names_missing_nvidia_smi():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        discovery = scanprobe.discover_gpus()
    assert discovery.count == 0
    assert discovery.status == "unavailable"
    assert discovery.error == "nvidia-smi not found"


def test_discover_gpus_names_no_visible_gpus():
    proc = fake_proc(stderr="No devices were found", returncode=1)
    with patch("subprocess.run", return_value=proc):
        discovery = scanprobe.discover_gpus()
    assert discovery.count == 0
    assert discovery.status == "none"
    assert discovery.error == "nvidia-smi found no GPUs"


def test_xid_drain_detected():
    line = "[1.0] NVRM: Xid (PCI:0000:3b:00): 95, pid='<unknown>'"
    with patch("subprocess.run", return_value=fake_proc(stdout=line)):
        result = scanprobe.check_xid()
    assert result.drain_xids_found == [95]
    assert not result.passed


def test_xid_drain_detected_without_pci_prefix():
    line = "[1.0] NVRM: Xid (0000:03:00): 79, GPU has fallen off the bus."
    with patch("subprocess.run", return_value=fake_proc(stdout=line)):
        result = scanprobe.check_xid()
    assert result.drain_xids_found == [79]
    assert result.events[0]["pci"] == "0000:03:00"


def test_xid_fallen_off_bus_line_without_xid_code():
    line = "NVRM: GPU 0000:01:00.0: GPU has fallen off the bus."
    with patch("subprocess.run", return_value=fake_proc(stdout=line)):
        result = scanprobe.check_xid()
    assert result.drain_xids_found == [79]
    assert result.events[0]["xid"] == 79


def test_xid_154_recovery_action_is_drain():
    line = (
        "NVRM: Xid (PCI:0000:01:00): 154, GPU recovery action changed "
        "from 0x0 (None) to 0x1 (GPU Reset Required)"
    )
    with patch("subprocess.run", return_value=fake_proc(stdout=line)):
        result = scanprobe.check_xid()
    assert result.drain_xids_found == [154]
    assert result.events[0]["recovery_action"] == "GPU Reset Required"
    assert not result.passed


def test_xid_watch_detected():
    line = "kernel: NVRM: Xid (PCI:0000:3b:00.0): 94, Ch 00000008"
    with patch("subprocess.run", return_value=fake_proc(stdout=line)):
        result = scanprobe.check_xid()
    assert result.watch_xids_found == [94]
    assert result.passed


def test_xid_unavailable_is_not_failure():
    with patch("subprocess.run", return_value=fake_proc(returncode=1, stderr="denied")):
        result = scanprobe.check_xid()
    assert not result.available
    assert result.passed
    assert "kernel logs unavailable" in result.error
    assert "sudo" not in result.error


def test_xid_falls_back_to_journalctl_when_dmesg_is_restricted():
    line = "kernel: NVRM: Xid (PCI:0000:3b:00): 79, GPU has fallen off the bus."
    procs = [
        fake_proc(returncode=1, stderr="dmesg: read kernel buffer failed: Operation not permitted"),
        fake_proc(returncode=1, stderr="dmesg: read kernel buffer failed: Operation not permitted"),
        fake_proc(stdout=line),
    ]
    with patch("subprocess.run", side_effect=procs):
        result = scanprobe.check_xid()
    assert result.log_source == "journalctl-k"
    assert result.drain_xids_found == [79]
    assert not result.passed


def test_score_clear_gpu():
    gpu = scanprobe._parse_smi_line(sample_smi_line(), 0)
    score = scanprobe.score_gpu(gpu, scanprobe.XidResult(), 0)
    assert score.tier == "CLEAR"
    assert score.score == 0.0


def test_score_dbe_is_drain():
    gpu = scanprobe._parse_smi_line(sample_smi_line(dbe=1), 0)
    score = scanprobe.score_gpu(gpu, scanprobe.XidResult(), 0)
    assert score.tier == "DRAIN"
    assert "ecc_dbe_volatile" in score.signals
    assert "DBE ECC volatile" in score.evidence[0]


def test_score_thermal_throttle_is_watch():
    gpu = scanprobe._parse_smi_line(
        sample_smi_line(temp=70, throttle="0x0000000000000040"),
        0,
    )
    score = scanprobe.score_gpu(gpu, scanprobe.XidResult(), 0)
    assert score.tier == "WATCH"


def test_score_watch_signals_can_combine_to_drain():
    gpu = scanprobe._parse_smi_line(
        sample_smi_line(temp=91, throttle="0x0000000000000040"),
        0,
    )
    score = scanprobe.score_gpu(gpu, scanprobe.XidResult(), 0)
    assert score.tier == "DRAIN"


def test_score_xid_unavailable_stays_clear():
    gpu = scanprobe._parse_smi_line(sample_smi_line(), 0)
    xid = scanprobe.XidResult(available=False, error="dmesg failed")
    score = scanprobe.score_gpu(gpu, xid, 0)
    assert score.tier == "CLEAR"
    assert "xid_log_unavailable" in score.signals


def test_score_nvml_unknown_is_unknown():
    gpu = scanprobe.GpuInfo(
        0,
        passed=False,
        error="nvidia-smi failed: Failed to initialize NVML: Unknown Error",
    )
    score = scanprobe.score_gpu(gpu, scanprobe.XidResult(), 0)
    assert score.tier == "UNKNOWN"
    assert "nvidia_smi_unavailable" in score.signals


def test_score_device_handle_unknown_is_drain():
    gpu = scanprobe.GpuInfo(
        0,
        passed=False,
        error="nvidia-smi failed: Unable to determine the device handle for GPU0000:B3:00.0: Unknown Error",
    )
    score = scanprobe.score_gpu(gpu, scanprobe.XidResult(), 0)
    assert score.tier == "DRAIN"
    assert "nvidia_smi_device_lost" in score.signals


def test_parse_gpu_list():
    assert scanprobe.parse_gpu_list("all", 3) == [0, 1, 2]
    assert scanprobe.parse_gpu_list("0,2", 4) == [0, 2]
    assert scanprobe.parse_gpu_list("0-2", 4) == [0, 1, 2]


def test_next_actions_are_tier_specific():
    assert "Do not launch new work" in scanprobe.next_actions("DRAIN")[0]
    assert "Inspect the listed evidence" in scanprobe.next_actions("WATCH")[0]
    assert "could not observe enough" in scanprobe.next_actions("UNKNOWN")[0]
    assert "No local drain/watch evidence" in scanprobe.next_actions("CLEAR")[0]


def test_node_tier_priority():
    assert scanprobe.node_tier([scanprobe.RiskScore(0, tier="CLEAR")]) == "CLEAR"
    assert scanprobe.node_tier([scanprobe.RiskScore(0, tier="UNKNOWN")]) == "UNKNOWN"
    assert scanprobe.node_tier([scanprobe.RiskScore(0, tier="WATCH")]) == "WATCH"
    assert scanprobe.node_tier([
        scanprobe.RiskScore(0, tier="UNKNOWN"),
        scanprobe.RiskScore(1, tier="WATCH"),
    ]) == "WATCH"
    assert scanprobe.node_tier([
        scanprobe.RiskScore(0, tier="WATCH"),
        scanprobe.RiskScore(1, tier="DRAIN"),
    ]) == "DRAIN"


def test_drain_and_watch_xid_sets_are_documented():
    for code in scanprobe.DRAIN_XIDS | scanprobe.WATCH_XIDS:
        assert code in scanprobe.XID_DESC


def test_print_text_is_evidence_first_without_score():
    gpu = scanprobe._parse_smi_line(sample_smi_line(dbe=1), 0)
    score = scanprobe.score_gpu(gpu, scanprobe.XidResult(), 0)
    out = io.StringIO()
    with redirect_stdout(out):
        scanprobe.print_text({0: gpu}, [score], scanprobe.XidResult(), 1.2)
    text = out.getvalue()
    assert "Node: DRAIN" in text
    assert "Visible evidence:" in text
    assert "Next action:" in text
    assert "Do not launch new work" in text
    assert "score=" not in text


def test_print_discovery_failure_is_evidence_first():
    discovery = scanprobe.GpuDiscovery(status="unavailable", error="nvidia-smi not found")
    out = io.StringIO()
    with redirect_stdout(out):
        scanprobe.print_discovery_failure(discovery, 0.1, False)
    text = out.getvalue()
    assert "Node: UNKNOWN" in text
    assert "Visible evidence:" in text
    assert "nvidia-smi not found" in text
    assert "Next action:" in text


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    passed = failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {test.__name__}: {exc}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
