import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("scanprobe_script", ROOT / "scanprobe.py")
scanprobe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scanprobe)
GOLDEN = ROOT / "tests" / "fixtures" / "golden"


def fake_proc(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def sample_smi_line(index=0, dbe=0, sbe=0, temp=72, throttle="0x0000000000000000"):
    return (
        f"{index}, NVIDIA H100 80GB HBM3, {sbe}, {dbe}, 0, {temp}, {throttle}"
    )


def golden(name):
    return (GOLDEN / name).read_text()


def assert_read_only_call(call, cmd, timeout):
    assert call.args[0] == cmd
    assert call.kwargs == {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
    }


def render_scan(gpus, xid):
    scores = [scanprobe.score_gpu(gpu, index) for index, gpu in sorted(gpus.items())]
    report = scanprobe.build_node_report(scores, xid)
    out = io.StringIO()
    with redirect_stdout(out):
        scanprobe.print_text(gpus, scores, report, 1.2)
    return out.getvalue()


def test_parse_int_handles_na_and_commas():
    assert scanprobe._parse_int("N/A") == 0
    assert scanprobe._parse_int("[Not Supported]") == 0
    assert scanprobe._parse_int("1,234") == 1234


def test_parse_float_strips_units():
    assert scanprobe._parse_float("72 C") == 72.0
    assert scanprobe._parse_float("250.5 W") == 250.5
    assert scanprobe._parse_float("[N/A]") is None


def test_redact_text_removes_common_host_identifiers():
    text = (
        "May 06 10:00:00 trainer-01 kernel: NVRM: Xid "
        "(PCI:0000:3b:00.0): 94, GPU-12345678-1234-1234-1234-123456789abc "
        "10.1.2.3 /Users/alice/job"
    )
    redacted = scanprobe._redact_text(text)
    assert "trainer-01" not in redacted
    assert "GPU-12345678" not in redacted
    assert "10.1.2.3" not in redacted
    assert "/Users/alice" not in redacted
    assert "PCI:0000:3b:00.0" in redacted


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


def test_parse_smi_line_uses_csv_parser_for_quoted_fields():
    line = '0, "NVIDIA H100, 80GB HBM3", 0, 0, 0, 72, 0x0000000000000000'
    gpu = scanprobe._parse_smi_line(line, 0)
    assert gpu.passed
    assert gpu.name == "NVIDIA H100, 80GB HBM3"
    assert gpu.temperature_gpu == 72.0


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


def test_discover_gpus_preserves_non_contiguous_indices():
    proc = fake_proc(stdout="0\n2\n")
    with patch("subprocess.run", return_value=proc):
        discovery = scanprobe.discover_gpus()
    assert discovery.count == 2
    assert discovery.indices == [0, 2]


def test_discover_gpus_command_is_read_only_and_timed():
    proc = fake_proc(stdout="0\n")
    with patch("subprocess.run", return_value=proc) as run:
        scanprobe.discover_gpus()
    assert_read_only_call(
        run.call_args,
        ["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"],
        10,
    )


def test_query_gpus_command_is_read_only_and_timed():
    proc = fake_proc(stdout=sample_smi_line(0) + "\n")
    with patch("subprocess.run", return_value=proc) as run:
        scanprobe.query_gpus([0])
    assert_read_only_call(
        run.call_args,
        ["nvidia-smi", "--query-gpu=" + scanprobe.SMI_FIELDS, "--format=csv,noheader,nounits"],
        30,
    )


def test_check_xid_command_is_read_only_and_timed():
    line = "kernel: NVRM: Xid (PCI:0000:3b:00.0): 94, Ch 00000008"
    with patch("subprocess.run", return_value=fake_proc(stdout=line)) as run:
        scanprobe.check_xid()
    assert_read_only_call(
        run.call_args,
        ["dmesg", "--level=err,warn,crit,alert,emerg"],
        10,
    )


def test_check_xid_fallback_commands_are_read_only_and_timed():
    procs = [
        fake_proc(returncode=1, stderr="dmesg: read kernel buffer failed: Operation not permitted"),
        fake_proc(returncode=1, stderr="dmesg: read kernel buffer failed: Operation not permitted"),
        fake_proc(stdout="kernel: NVRM: Xid (PCI:0000:3b:00.0): 94, Ch 00000008"),
    ]
    with patch("subprocess.run", side_effect=procs) as run:
        scanprobe.check_xid()
    assert_read_only_call(
        run.call_args_list[0],
        ["dmesg", "--level=err,warn,crit,alert,emerg"],
        10,
    )
    assert_read_only_call(run.call_args_list[1], ["dmesg"], 10)
    assert_read_only_call(
        run.call_args_list[2],
        ["journalctl", "-k", "-b", "--no-pager"],
        10,
    )


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


def test_reset_action_xids_are_drain():
    for code in (46, 62, 109, 110, 119, 120, 136, 155, 156, 158):
        assert scanprobe._xid_severity(code) == "DRAIN"


def test_xid_raw_line_is_redacted():
    line = (
        "May 06 10:00:00 trainer-01 kernel: NVRM: Xid "
        "(PCI:0000:3b:00.0): 94, pid=/home/bob/train.py"
    )
    with patch("subprocess.run", return_value=fake_proc(stdout=line)):
        result = scanprobe.check_xid()
    assert result.watch_xids_found == [94]
    assert "trainer-01" not in result.events[0]["raw"]
    assert "/home/bob" not in result.events[0]["raw"]
    assert "PCI:0000:3b:00.0" in result.events[0]["raw"]


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
    score = scanprobe.score_gpu(gpu, 0)
    assert score.tier == "CLEAR"
    assert score.score == 0.0


def test_score_dbe_is_drain():
    gpu = scanprobe._parse_smi_line(sample_smi_line(dbe=1), 0)
    score = scanprobe.score_gpu(gpu, 0)
    assert score.tier == "DRAIN"
    assert "ecc_dbe_volatile" in score.signals
    assert "DBE ECC volatile" in score.evidence[0]


def test_score_thermal_throttle_is_watch():
    gpu = scanprobe._parse_smi_line(
        sample_smi_line(temp=70, throttle="0x0000000000000040"),
        0,
    )
    score = scanprobe.score_gpu(gpu, 0)
    assert score.tier == "WATCH"
    assert "HW throttle active" in score.evidence[0]


def test_score_watch_signals_can_combine_to_drain():
    gpu = scanprobe._parse_smi_line(
        sample_smi_line(temp=91, throttle="0x0000000000000040"),
        0,
    )
    score = scanprobe.score_gpu(gpu, 0)
    assert score.tier == "DRAIN"


def test_node_xid_unavailable_stays_clear():
    gpu = scanprobe._parse_smi_line(sample_smi_line(), 0)
    xid = scanprobe.XidResult(available=False, error="dmesg failed")
    score = scanprobe.score_gpu(gpu, 0)
    report = scanprobe.build_node_report([score], xid)
    assert score.tier == "CLEAR"
    assert report.tier == "CLEAR"
    assert "xid_log_unavailable" in report.signals


def test_score_nvml_unknown_is_unknown():
    gpu = scanprobe.GpuInfo(
        0,
        passed=False,
        error="nvidia-smi failed: Failed to initialize NVML: Unknown Error",
    )
    score = scanprobe.score_gpu(gpu, 0)
    assert score.tier == "UNKNOWN"
    assert "nvidia_smi_unavailable" in score.signals


def test_score_device_handle_unknown_is_drain():
    gpu = scanprobe.GpuInfo(
        0,
        passed=False,
        error="nvidia-smi failed: Unable to determine the device handle for GPU0000:B3:00.0: Unknown Error",
    )
    score = scanprobe.score_gpu(gpu, 0)
    assert score.tier == "DRAIN"
    assert "nvidia_smi_device_lost" in score.signals


def test_xid_drain_is_node_level_not_per_gpu():
    gpus = [
        scanprobe._parse_smi_line(sample_smi_line(0), 0),
        scanprobe._parse_smi_line(sample_smi_line(1), 1),
    ]
    scores = [scanprobe.score_gpu(gpu, gpu.index) for gpu in gpus]
    xid = scanprobe.XidResult(
        events=[{
            "xid": 79,
            "pci": "0000:3b:00.0",
            "description": scanprobe.XID_DESC[79],
            "severity": "DRAIN",
            "message": "GPU has fallen off the bus",
            "raw": "NVRM: Xid (PCI:0000:3b:00.0): 79",
        }],
        drain_xids_found=[79],
        passed=False,
        log_source="dmesg-cmd",
    )
    report = scanprobe.build_node_report(scores, xid)

    assert report.tier == "DRAIN"
    assert all(score.tier == "CLEAR" for score in scores)
    assert all("xid_drain" not in score.signals for score in scores)
    assert "xid_drain" in report.signals
    assert "Xid" in report.evidence[0]


def test_parse_gpu_list():
    assert scanprobe.parse_gpu_list("all", 3) == [0, 1, 2]
    assert scanprobe.parse_gpu_list("all", [0, 2]) == [0, 2]
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
    assert not (scanprobe.DRAIN_XIDS & scanprobe.WATCH_XIDS)
    for code in scanprobe.DRAIN_XIDS | scanprobe.WATCH_XIDS:
        assert code in scanprobe.XID_DESC


def test_print_text_is_evidence_first_without_score():
    gpu = scanprobe._parse_smi_line(sample_smi_line(dbe=1), 0)
    score = scanprobe.score_gpu(gpu, 0)
    report = scanprobe.build_node_report([score], scanprobe.XidResult())
    out = io.StringIO()
    with redirect_stdout(out):
        scanprobe.print_text({0: gpu}, [score], report, 1.2)
    text = out.getvalue()
    assert "Node: DRAIN" in text
    assert "Node-level evidence:" in text
    assert "GPU evidence:" in text
    assert "Next action:" in text
    assert "Do not launch new work" in text
    assert "score=" not in text


def test_print_text_separates_node_and_gpu_evidence():
    gpu = scanprobe._parse_smi_line(sample_smi_line(0), 0)
    score = scanprobe.score_gpu(gpu, 0)
    xid = scanprobe.XidResult(drain_xids_found=[79], passed=False, log_source="dmesg-cmd")
    report = scanprobe.build_node_report([score], xid)
    out = io.StringIO()
    with redirect_stdout(out):
        scanprobe.print_text({0: gpu}, [score], report, 1.2)
    text = out.getvalue()

    assert "Node: DRAIN" in text
    assert "Node-level evidence:\n  - Critical Xid events" in text
    assert "GPU 0: CLEAR" in text
    assert "no local GPU drain/watch evidence observed" in text
    assert text.count("Critical Xid events") == 1


def test_node_report_preserves_xid_warning_detail():
    gpu = scanprobe._parse_smi_line(sample_smi_line(0), 0)
    score = scanprobe.score_gpu(gpu, 0)
    xid = scanprobe.XidResult(
        drain_xids_found=[79],
        warnings=["Xid 79 (GPU has fallen off the bus) on 0000:3b:00.0"],
        passed=False,
        log_source="dmesg-cmd",
    )
    report = scanprobe.build_node_report([score], xid)
    assert "GPU has fallen off the bus" in report.evidence[0]
    assert "0000:3b:00.0" in report.evidence[0]


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


def test_json_output_includes_context_and_next_action():
    gpu = scanprobe._parse_smi_line(sample_smi_line(0), 0)
    score = scanprobe.score_gpu(gpu, 0)
    report = scanprobe.build_node_report([score], scanprobe.XidResult())
    out = io.StringIO()
    with redirect_stdout(out):
        scanprobe.print_json({0: gpu}, [score], report, scanprobe.XidResult(), 1.2)
    payload = json.loads(out.getvalue())
    assert payload["claim_context"] == scanprobe.CLAIM_CONTEXT_TEXT
    assert payload["mode"] == scanprobe.MODE_CONTEXT_TEXT
    assert payload["not_checked"] == scanprobe.NOT_CHECKED_TEXT
    assert payload["next_action"] == scanprobe.next_actions("CLEAR")


def test_json_discovery_failure_includes_context_and_next_action():
    discovery = scanprobe.GpuDiscovery(status="unavailable", error="nvidia-smi not found")
    out = io.StringIO()
    with redirect_stdout(out):
        scanprobe.print_discovery_failure(discovery, 0.1, True)
    payload = json.loads(out.getvalue())
    assert payload["claim_context"] == scanprobe.CLAIM_CONTEXT_TEXT
    assert payload["mode"] == scanprobe.MODE_CONTEXT_TEXT
    assert payload["not_checked"] == scanprobe.NOT_CHECKED_TEXT
    assert payload["next_action"] == scanprobe.next_actions("UNKNOWN")


def test_golden_clear_output():
    gpu = scanprobe._parse_smi_line(sample_smi_line(0), 0)
    assert render_scan({0: gpu}, scanprobe.XidResult()) == golden("clear.txt")


def test_golden_watch_output():
    gpu = scanprobe._parse_smi_line(sample_smi_line(0), 0)
    xid = scanprobe.XidResult(watch_xids_found=[94], log_source="dmesg-cmd")
    assert render_scan({0: gpu}, xid) == golden("watch.txt")


def test_golden_drain_output():
    gpu = scanprobe._parse_smi_line(sample_smi_line(0), 0)
    xid = scanprobe.XidResult(
        drain_xids_found=[79],
        passed=False,
        log_source="dmesg-cmd",
    )
    assert render_scan({0: gpu}, xid) == golden("drain.txt")


def test_golden_unknown_output():
    discovery = scanprobe.GpuDiscovery(status="unavailable", error="nvidia-smi not found")
    out = io.StringIO()
    with redirect_stdout(out):
        scanprobe.print_discovery_failure(discovery, 0.1, False)
    assert out.getvalue() == golden("unknown.txt")


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
