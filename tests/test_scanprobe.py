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


def test_parse_smi_line_rejects_extra_columns():
    line = "0, NVIDIA H100, 80GB HBM3, 0, 0, 0, 72, 0x0"
    gpu = scanprobe._parse_smi_line(line, 0)
    assert not gpu.passed
    assert "column count" in gpu.error


def test_parse_smi_line_unsupported_fields_are_not_clear():
    line = (
        "0, NVIDIA A100 MIG 1g.5gb, [Not Supported], [Not Supported], "
        "[Not Supported], [Not Supported], [Not Supported]"
    )
    gpu = scanprobe._parse_smi_line(line, 0)
    score = scanprobe.score_gpu(gpu, 0)
    assert not gpu.passed
    assert "unsupported" in gpu.error
    assert score.tier == "UNKNOWN"


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


def test_query_gpus_classifies_stdout_error_text():
    stdout = "Unable to determine the device handle for GPU0000:B3:00.0: Unknown Error"
    proc = fake_proc(stdout=stdout, returncode=1)
    with patch("subprocess.run", return_value=proc):
        results = scanprobe.query_gpus([0])
    assert "Unable to determine the device handle" in results[0].error


def test_smi_error_classifies_before_truncation():
    stderr = ("noise " * 60) + "No devices were found"
    assert scanprobe._format_smi_error(1, stderr) == "nvidia-smi found no GPUs"


def test_device_handle_classification_survives_truncation():
    stderr = (
        ("preamble " * 30)
        + "Unable to determine the device handle for GPU0000:B3:00.0"
    )
    formatted = scanprobe._format_smi_error(1, stderr)
    assert scanprobe._smi_error_is_device_lost(formatted)


def test_device_handle_dominates_nvml_init_error():
    stderr = (
        "Failed to initialize NVML: Unknown Error\n"
        + ("preamble " * 30)
        + "Unable to determine the device handle for GPU0000:B3:00.0"
    )
    formatted = scanprobe._format_smi_error(1, stderr)
    assert scanprobe._smi_error_is_device_lost(formatted)

    gpu = scanprobe.GpuInfo(0, passed=False, error=formatted)
    score = scanprobe.score_gpu(gpu, 0)
    report = scanprobe.build_node_report(
        [score],
        scanprobe.XidResult(),
        nvidia_smi_error=scanprobe._node_nvidia_smi_error({0: gpu}),
    )
    assert score.tier == "UNKNOWN"
    assert report.tier == "DRAIN"


def test_nvml_driver_library_mismatch_survives_truncation():
    stderr = (
        "Failed to initialize NVML: Unknown Error\n"
        + ("preamble " * 30)
        + "Driver/library version mismatch"
    )
    formatted = scanprobe._format_smi_error(1, stderr)
    assert scanprobe._smi_error_is_driver_library_mismatch(formatted)


def test_nvidia_smi_driver_unreachable_survives_truncation():
    stderr = (
        ("preamble " * 30)
        + "NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver"
    )
    formatted = scanprobe._format_smi_error(1, stderr)
    assert scanprobe._smi_error_is_driver_unreachable(formatted)


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
        run.call_args_list[0],
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


def test_xid_fallen_off_bus_at_line_without_xid_code():
    line = "NVRM: GPU at 0000:01:00.0 has fallen off the bus."
    with patch("subprocess.run", return_value=fake_proc(stdout=line)):
        result = scanprobe.check_xid()
    assert result.drain_xids_found == [79]
    assert result.events[0]["pci"] == "0000:01:00.0"


def test_xid_79_severity_consistent_across_parsers():
    standard = scanprobe._parse_xid_line("NVRM: Xid (PCI:0000:01:00.0): 79")
    alternate = scanprobe._parse_xid_line(
        "NVRM: GPU 0000:01:00.0: GPU has fallen off the bus"
    )
    assert standard["severity"] == alternate["severity"]


def test_parse_xid_rejects_non_pci_address_format():
    event = scanprobe._parse_xid_line("NVRM: Xid (random text): 79, fallen off")
    assert event is None


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


def test_xid_154_reset_not_required_is_not_drain():
    line = (
        "NVRM: Xid (PCI:0000:01:00): 154, GPU recovery action changed "
        "from 0x0 (None) to 0x2 (Reset Not Required)"
    )
    with patch("subprocess.run", return_value=fake_proc(stdout=line)):
        result = scanprobe.check_xid()
    assert result.drain_xids_found == []
    assert result.events[0]["recovery_action"] == "Reset Not Required"
    assert result.events[0]["severity"] == "INFO"


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
    proc = fake_proc(
        returncode=1,
        stderr="dmesg: read kernel buffer failed: Operation not permitted",
    )
    with patch("subprocess.run", return_value=proc):
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


def test_xid_scans_full_dmesg_when_filtered_has_no_xids():
    procs = [
        fake_proc(stdout="kernel: unrelated warning"),
        fake_proc(
            stdout="kernel: NVRM: Xid (PCI:0000:3b:00.0): 79, "
            "GPU has fallen off the bus."
        ),
    ]
    with patch("subprocess.run", side_effect=procs):
        result = scanprobe.check_xid()
    assert result.drain_xids_found == [79]


def test_xid_falls_back_to_journalctl_when_dmesg_missing():
    line = "kernel: NVRM: Xid (PCI:0000:3b:00): 79, GPU has fallen off the bus."
    outcomes = [FileNotFoundError(), fake_proc(stdout=line)]
    with patch("subprocess.run", side_effect=outcomes):
        result = scanprobe.check_xid()
    assert result.log_source == "journalctl-k"
    assert result.drain_xids_found == [79]


def test_xid_empty_dmesg_should_be_unavailable():
    procs = [
        fake_proc(stdout="", returncode=0),
        fake_proc(stdout="", returncode=0),
        fake_proc(stdout="", returncode=0),
    ]
    with patch("subprocess.run", side_effect=procs):
        result = scanprobe.check_xid()
    assert not result.available
    assert "unavailable" in (result.error or "").lower()


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
    assert score.evidence[0].startswith("nvidia-smi:")
    assert "DBE ECC volatile" in score.evidence[0]


def test_primary_issue_names_gpu_drain_evidence():
    gpu = scanprobe._parse_smi_line(sample_smi_line(dbe=1), 0)
    score = scanprobe.score_gpu(gpu, 0)
    report = scanprobe.build_node_report([score], scanprobe.XidResult())
    assert report.tier == "DRAIN"
    assert report.primary_issue == "GPU 0 has volatile DBE ECC evidence"


def test_score_thermal_throttle_is_watch():
    gpu = scanprobe._parse_smi_line(
        sample_smi_line(temp=70, throttle="0x0000000000000040"),
        0,
    )
    score = scanprobe.score_gpu(gpu, 0)
    assert score.tier == "WATCH"
    assert "HW throttle active" in score.evidence[0]


def test_score_thermal_throttle_combo_is_explicit_drain():
    gpu = scanprobe._parse_smi_line(
        sample_smi_line(temp=91, throttle="0x0000000000000040"),
        0,
    )
    score = scanprobe.score_gpu(gpu, 0)
    assert score.tier == "DRAIN"
    assert "thermal_hw_throttle_combo" in score.signals


def test_node_xid_unavailable_is_unknown():
    gpu = scanprobe._parse_smi_line(sample_smi_line(), 0)
    xid = scanprobe.XidResult(available=False, error="dmesg failed")
    score = scanprobe.score_gpu(gpu, 0)
    report = scanprobe.build_node_report([score], xid)
    assert score.tier == "CLEAR"
    assert report.tier == "UNKNOWN"
    assert "xid_log_unavailable" in report.signals
    assert "nvidia-smi GPU query visible on 1 selected GPU" in report.visibility
    assert "no local GPU drain/watch evidence visible" in report.visibility
    assert "Xid scan unavailable: dmesg failed" in report.visibility


def test_visibility_summarizes_unknown_gpu_query():
    gpu = scanprobe.GpuInfo(
        0,
        passed=False,
        error="nvidia-smi failed: Failed to initialize NVML: Unknown Error",
    )
    score = scanprobe.score_gpu(gpu, 0)
    report = scanprobe.build_node_report([score], scanprobe.XidResult())
    assert report.tier == "UNKNOWN"
    assert "nvidia-smi GPU query incomplete for 1 selected GPU" in report.visibility
    assert "Xid scan available" in report.visibility


def test_build_node_report_treats_none_xid_as_unknown():
    gpu = scanprobe._parse_smi_line(sample_smi_line(), 0)
    score = scanprobe.score_gpu(gpu, 0)
    report = scanprobe.build_node_report([score], None)
    assert report.tier == "UNKNOWN"
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


def test_score_nvml_driver_library_mismatch_names_primary_issue():
    gpu = scanprobe.GpuInfo(
        0,
        passed=False,
        error=(
            "nvidia-smi failed: Failed to initialize NVML: "
            "Driver/library version mismatch"
        ),
    )
    score = scanprobe.score_gpu(gpu, 0)
    report = scanprobe.build_node_report([score], scanprobe.XidResult())
    assert score.tier == "UNKNOWN"
    assert "nvidia_smi_driver_library_mismatch" in score.signals
    assert report.primary_issue == "NVIDIA driver/library mismatch prevents local GPU state"


def test_score_device_handle_error_is_unknown_per_gpu():
    gpu = scanprobe.GpuInfo(
        0,
        passed=False,
        error="nvidia-smi failed: Unable to determine the device handle for GPU0000:B3:00.0: Unknown Error",
    )
    score = scanprobe.score_gpu(gpu, 0)
    assert score.tier == "UNKNOWN"
    assert "nvidia_smi_unavailable" in score.signals
    assert "device handle error" in score.evidence[0]


def test_nvidia_smi_device_handle_error_is_node_level():
    error = (
        "nvidia-smi failed: Unable to determine the device handle "
        "for GPU0000:B3:00.0: Unknown Error"
    )
    gpus = {
        index: scanprobe.GpuInfo(index, passed=False, error=error)
        for index in [0, 1, 2, 3]
    }
    scores = [scanprobe.score_gpu(gpu, index) for index, gpu in gpus.items()]
    report = scanprobe.build_node_report(
        scores,
        scanprobe.XidResult(),
        nvidia_smi_error=scanprobe._node_nvidia_smi_error(gpus),
    )
    assert all(score.tier == "UNKNOWN" for score in scores)
    assert report.tier == "DRAIN"
    assert "nvidia_smi_device_lost" in report.signals
    assert report.primary_issue == "nvidia-smi cannot determine a GPU device handle"


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


def test_duplicate_xid_codes_preserve_per_device_warning_detail():
    text = "\n".join([
        "NVRM: Xid (PCI:0000:3b:00.0): 79, GPU has fallen off the bus.",
        "NVRM: Xid (PCI:0000:4c:00.0): 79, GPU has fallen off the bus.",
    ])
    result = scanprobe._record_xid_events(scanprobe.XidResult(), text)
    assert result.drain_xids_found == [79]
    assert len(result.warnings) == 2
    assert "0000:3b:00.0" in result.warnings[0]
    assert "0000:4c:00.0" in result.warnings[1]


def test_parse_gpu_list():
    assert scanprobe.parse_gpu_list("all", 3) == [0, 1, 2]
    assert scanprobe.parse_gpu_list("all", [0, 2]) == [0, 2]
    assert scanprobe.parse_gpu_list("0,2", 4) == [0, 2]
    assert scanprobe.parse_gpu_list("0-2", 4) == [0, 1, 2]


def test_parse_gpu_list_rejects_empty_selection():
    for value in ("3-1", ""):
        try:
            scanprobe.parse_gpu_list(value, [0, 1, 2, 3])
        except ValueError:
            pass
        else:
            raise AssertionError(f"{value!r} should be invalid")


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
    assert "Primary issue: GPU 0 has volatile DBE ECC evidence." in text
    assert "Visibility:" in text
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


def test_node_report_labels_mixed_xid_severities_separately():
    gpu = scanprobe._parse_smi_line(sample_smi_line(0), 0)
    score = scanprobe.score_gpu(gpu, 0)
    xid = scanprobe._record_xid_events(
        scanprobe.XidResult(log_source="dmesg-cmd"),
        "\n".join([
            "NVRM: Xid (PCI:0000:3b:00.0): 79, GPU has fallen off the bus.",
            "NVRM: Xid (PCI:0000:4c:00.0): 94, Ch 00000008",
        ]),
    )
    report = scanprobe.build_node_report([score], xid)
    assert len(report.evidence) == 2
    assert "Critical Xid events" in report.evidence[0]
    assert "Xid 79" in report.evidence[0]
    assert "Xid 94" not in report.evidence[0]
    assert "Xid events" in report.evidence[1]
    assert "Xid 94" in report.evidence[1]


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


def test_print_cli_error_text_includes_standard_header():
    out = io.StringIO()
    with redirect_stdout(out):
        scanprobe.print_cli_error("bad gpus", 0.1, as_json=False)
    text = out.getvalue()
    assert "scanprobe" in text
    assert scanprobe.CLAIM_CONTEXT_TEXT in text
    assert scanprobe.MODE_CONTEXT_TEXT in text
    assert "Node: UNKNOWN" in text
    assert "bad gpus" in text


def test_discovery_device_handle_failure_is_drain():
    discovery = scanprobe.GpuDiscovery(
        status="unavailable",
        error=(
            "nvidia-smi failed: Unable to determine the device handle "
            "for GPU0000:B3:00.0"
        ),
    )
    out = io.StringIO()
    with redirect_stdout(out):
        scanprobe.print_discovery_failure(discovery, 0.1, False)
    assert "Node: DRAIN" in out.getvalue()


def test_discovery_driver_library_mismatch_names_primary_issue():
    discovery = scanprobe.GpuDiscovery(
        status="unavailable",
        error=(
            "nvidia-smi failed: Failed to initialize NVML: "
            "Driver/library version mismatch"
        ),
    )
    report = scanprobe._discovery_failure_report(discovery)
    assert report.tier == "UNKNOWN"
    assert report.primary_issue == "NVIDIA driver/library mismatch prevents local GPU state"


def test_discovery_driver_unreachable_names_primary_issue():
    discovery = scanprobe.GpuDiscovery(
        status="unavailable",
        error=(
            "nvidia-smi failed: NVIDIA-SMI has failed because it couldn't "
            "communicate with the NVIDIA driver"
        ),
    )
    report = scanprobe._discovery_failure_report(discovery)
    assert report.tier == "UNKNOWN"
    assert report.primary_issue == "nvidia-smi cannot communicate with the NVIDIA driver"


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
    assert payload["kernel_log_scope"] == scanprobe.RECENCY_CONTEXT_TEXT
    assert payload["automation"] == scanprobe.AUTOMATION_CONTEXT
    assert not payload["automation"]["automatic_remediation"]
    assert payload["not_checked"] == scanprobe.NOT_CHECKED_TEXT
    assert payload["next_action"] == scanprobe.next_actions("CLEAR")
    assert payload["node_report"]["primary_issue"] == "none visible in this local scan"
    assert "visibility" in payload["node_report"]


def test_json_discovery_failure_includes_context_and_next_action():
    discovery = scanprobe.GpuDiscovery(status="unavailable", error="nvidia-smi not found")
    out = io.StringIO()
    with redirect_stdout(out):
        scanprobe.print_discovery_failure(discovery, 0.1, True)
    payload = json.loads(out.getvalue())
    assert payload["claim_context"] == scanprobe.CLAIM_CONTEXT_TEXT
    assert payload["mode"] == scanprobe.MODE_CONTEXT_TEXT
    assert payload["kernel_log_scope"] == scanprobe.RECENCY_CONTEXT_TEXT
    assert payload["automation"] == scanprobe.AUTOMATION_CONTEXT
    assert not payload["automation"]["automatic_remediation"]
    assert payload["not_checked"] == scanprobe.NOT_CHECKED_TEXT
    assert payload["next_action"] == scanprobe.next_actions("UNKNOWN")
    assert "node_report" in payload
    assert "risk_scores" in payload
    assert "nvidia_smi" in payload
    assert "xid" in payload


def test_main_json_scans_xid_when_discovery_finds_no_gpus():
    argv = ["scanprobe", "--json"]
    discovery = scanprobe.GpuDiscovery(
        status="none",
        error="nvidia-smi found no GPUs",
    )
    xid = scanprobe._record_xid_events(
        scanprobe.XidResult(log_source="dmesg-cmd"),
        "NVRM: Xid (PCI:0000:3b:00.0): 143, GPU init error",
    )
    out = io.StringIO()
    with patch.object(sys, "argv", argv), patch.object(
        scanprobe,
        "discover_gpus",
        return_value=discovery,
    ), patch.object(
        scanprobe,
        "check_xid",
        return_value=xid,
    ):
        with redirect_stdout(out):
            code = scanprobe.main()
    payload = json.loads(out.getvalue())
    assert code == 2
    assert payload["node_tier"] == "DRAIN"
    assert payload["xid"]["drain_xids_found"] == [143]
    assert "Critical Xid events" in payload["node_report"]["evidence"][1]


def test_main_json_does_not_scan_xid_when_nvidia_smi_is_missing():
    argv = ["scanprobe", "--json"]
    discovery = scanprobe.GpuDiscovery(
        status="unavailable",
        error="nvidia-smi not found",
    )
    out = io.StringIO()
    with patch.object(sys, "argv", argv), patch.object(
        scanprobe,
        "discover_gpus",
        return_value=discovery,
    ), patch.object(
        scanprobe,
        "check_xid",
        side_effect=AssertionError("Xid scan should not run when nvidia-smi is missing"),
    ):
        with redirect_stdout(out):
            code = scanprobe.main()
    payload = json.loads(out.getvalue())
    assert code == 3
    assert payload["xid"] is None
    assert payload["node_report"]["visibility"][-1] == "Xid scan not run"


def test_invalid_gpus_json_outputs_json():
    argv = ["scanprobe", "--json", "--gpus", "3-1"]
    discovery = scanprobe.GpuDiscovery(count=4, indices=[0, 1, 2, 3])
    out = io.StringIO()
    with patch.object(sys, "argv", argv), patch.object(
        scanprobe,
        "discover_gpus",
        return_value=discovery,
    ):
        with redirect_stdout(out):
            code = scanprobe.main()
    payload = json.loads(out.getvalue())
    assert code == 3
    assert payload["node_tier"] == "UNKNOWN"
    assert "Invalid --gpus argument" in payload["node_report"]["evidence"][0]
    assert "risk_scores" in payload
    assert "nvidia_smi" in payload
    assert "xid" in payload


def test_main_json_routes_device_handle_error_to_node_level():
    argv = ["scanprobe", "--json"]
    discovery = scanprobe.GpuDiscovery(count=2, indices=[0, 1])
    error = (
        "nvidia-smi failed: Unable to determine the device handle "
        "for GPU0000:B3:00.0: Unknown Error"
    )
    gpus = {
        0: scanprobe.GpuInfo(0, passed=False, error=error),
        1: scanprobe.GpuInfo(1, passed=False, error=error),
    }
    out = io.StringIO()
    with patch.object(sys, "argv", argv), patch.object(
        scanprobe,
        "discover_gpus",
        return_value=discovery,
    ), patch.object(
        scanprobe,
        "query_gpus",
        return_value=gpus,
    ), patch.object(
        scanprobe,
        "check_xid",
        return_value=scanprobe.XidResult(),
    ):
        with redirect_stdout(out):
            code = scanprobe.main()
    payload = json.loads(out.getvalue())
    assert code == 2
    assert payload["node_tier"] == "DRAIN"
    assert "nvidia_smi_device_lost" in payload["node_report"]["signals"]
    assert all(score["tier"] == "UNKNOWN" for score in payload["risk_scores"])


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
