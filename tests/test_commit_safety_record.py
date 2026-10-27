"""The safety-record commit procedure against a fake console (no hardware)."""

import json
from types import SimpleNamespace

import pytest

from omotion.boot_mode import BootInfo
from omotion.fpga_attest import (
    ATTEST_FAIL,
    ATTEST_PASS,
    ST_DIGEST,
    ST_NOT_CHECKED,
    ST_UFM_INVALID,
    ST_UFM_VALID,
    FpgaAttestDevice,
    FpgaAttestReport,
)
from procedures import wi15_commit_safety_record as commit
from procedures.safety_record import DeviceLimits, build_record
from test_safety_record import CALIBRATED, _write_run

SERIAL = "C-1"
FACTORY = BootInfo(2, 0x08020400, 0x08020000, interlocks=0, fpga_attest=ATTEST_PASS, build=1)
RELEASE = BootInfo(2, 0x08020400, 0x08020000, interlocks=0, fpga_attest=ATTEST_PASS, build=0)


def _report(ee_status=ST_UFM_VALID, opt_status=ST_UFM_VALID, state=ATTEST_PASS):
    def dev(i, status):
        return FpgaAttestDevice(i, status, 0, 0, 3198, 900, 0x12B, bytes(32), bytes(32))
    return FpgaAttestReport(1, state, 100, state != ATTEST_PASS, 1, 3198, (
        dev(0, ST_NOT_CHECKED), dev(1, ST_NOT_CHECKED), dev(2, ee_status), dev(3, opt_status)))


def _cycle(off_s=3.0, serial_after=SERIAL, proven=True):
    return SimpleNamespace(restart_proven=proven, off_duration_s=off_s,
                           console_serial_after=serial_after)


class FakeBench:
    def __init__(self, *, boot=FACTORY, config=CALIBRATED, report=None, cycle=None,
                 fail_write=None):
        self.boot = boot
        self.config = dict(config)
        self.report = report or _report()
        self.cycle = cycle or _cycle()
        self.fail_write = fail_write
        self.calls = []
        self.written = {}

    def connect(self):
        return True

    def console_serial(self):
        return SERIAL

    def firmware_version(self):
        return "1.8.2-factory"

    def boot_info(self):
        return self.boot

    def read_configuration(self):
        return dict(self.config)

    def write_record(self, device, record):
        self.calls.append(f"write:{device}")
        if device == self.fail_write:
            raise commit.CommitError(f"{device}: UFM read-back does not match the record")
        self.written[device] = record
        return {"device": device, "record": record.hex()}

    def power_cycle(self, serial):
        self.calls.append("power_cycle")
        return self.cycle

    def attestation(self):
        return self.report


def _run(tmp_path, bench, answers=("yes",)):
    answers = list(answers)
    lines = []
    code = commit.run_commit(
        bench, output_dir=tmp_path, operator="op", run_id="20261027T100000Z",
        input_func=lambda prompt: (lines.append(prompt), answers.pop(0))[1],
        output_func=lines.append, sleep=lambda s: None)
    return code, lines


def _evidence(tmp_path):
    (path,) = tmp_path.glob("*safety-record*/*run.json")
    return path, json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def calibrated(tmp_path):
    _write_run(tmp_path, "C-1-safety-cal-20261027T090000Z")
    return tmp_path


def test_a_passed_calibration_is_written_to_both_devices_and_attested(calibrated):
    bench = FakeBench()

    code, lines = _run(calibrated, bench)

    assert code == commit.EXIT_PASS
    assert bench.calls == ["write:EE", "write:OPT", "power_cycle"]
    assert bench.written["EE"] == build_record(DeviceLimits("EE", 4287, 550, 23125))
    assert bench.written["OPT"] == build_record(DeviceLimits("OPT", 6123, 550, 23125))
    path, data = _evidence(calibrated)
    assert path.name == "C-1-safety-record-run.json"
    assert path.parent.name == "C-1-safety-record-20261027T100000Z"
    assert data["status"] == "passed"
    assert data["safety_calibration"]["path"].endswith("C-1-safety-cal-run.json")
    assert data["records"]["EE"]["decoded"]["status"] == "valid"
    assert any("Attestation PASS" in line for line in lines)


@pytest.mark.parametrize(("bench_kwargs", "reason"), [
    ({"boot": RELEASE}, "Release image"),
    ({"boot": BootInfo(1, 0x08020400, 0x08020000)}, "1.8.2 or later"),
    ({"boot": None}, "1.8.2 or later"),
    ({"config": dict(CALIBRATED, OPT_DRIVE_CL=6200)}, "OPT_DRIVE_CL"),
])
def test_refusals_write_nothing(calibrated, bench_kwargs, reason):
    bench = FakeBench(**bench_kwargs)

    code, lines = _run(calibrated, bench)

    assert code == commit.EXIT_FAIL
    assert not bench.written
    _, data = _evidence(calibrated)
    assert data["status"] == "failed" and reason in data["failure_reason"]
    assert not any("may be incomplete" in line for line in lines)


def test_no_passed_safety_calibration_refuses(tmp_path):
    _write_run(tmp_path, "C-1-safety-cal-x", status="failed")
    bench = FakeBench()

    code, _ = _run(tmp_path, bench)

    assert code == commit.EXIT_FAIL and not bench.written
    _, data = _evidence(tmp_path)
    assert "run Safety Calibration first" in data["failure_reason"]


def test_evidence_outside_the_envelope_is_refused_before_writing(tmp_path):
    _write_run(tmp_path, "C-1-safety-cal-x", configuration=dict(CALIBRATED, EE_DRIVE_CL=9999))
    bench = FakeBench(config=dict(CALIBRATED, EE_DRIVE_CL=9999))

    code, _ = _run(tmp_path, bench)

    assert code == commit.EXIT_FAIL and not bench.written
    _, data = _evidence(tmp_path)
    assert "envelope" in data["failure_reason"]


@pytest.mark.parametrize("answer", ["no", "", "maybe"])
def test_anything_but_yes_cancels_without_writing(calibrated, answer):
    bench = FakeBench()

    code, lines = _run(calibrated, bench, answers=[answer])

    assert code == commit.EXIT_FAIL and not bench.written
    _, data = _evidence(calibrated)
    assert data["status"] == "canceled"
    assert "Nothing was written" in lines[-1]


def test_a_failed_write_stops_and_warns_the_record_may_be_incomplete(calibrated):
    bench = FakeBench(fail_write="OPT")

    code, lines = _run(calibrated, bench)

    assert code == commit.EXIT_FAIL
    assert bench.calls == ["write:EE", "write:OPT"]
    assert any("may be incomplete" in line for line in lines)


@pytest.mark.parametrize(("bench_kwargs", "reason"), [
    ({"report": _report(opt_status=ST_UFM_INVALID | ST_DIGEST, state=ATTEST_FAIL)},
     "did not accept"),
    ({"report": _report(ee_status=0)}, "did not accept"),
    ({"cycle": _cycle(off_s=0.5)}, "at least"),
    ({"cycle": _cycle(proven=False)}, "not observed"),
    ({"cycle": _cycle(serial_after="C-9")}, "different console"),
])
def test_after_the_write_the_restart_must_prove_the_records(calibrated, bench_kwargs, reason):
    bench = FakeBench(**bench_kwargs)

    code, lines = _run(calibrated, bench)

    assert code == commit.EXIT_FAIL
    _, data = _evidence(calibrated)
    assert reason in data["failure_reason"]
    assert any("may be incomplete" in line for line in lines)


def test_attestation_that_never_finishes_fails(calibrated):
    bench = FakeBench()
    bench.boot_info = lambda: BootInfo(2, 0x08020400, 0x08020000, 0, 1, 1)  # stays running
    ticks = iter(range(0, 1000, 5))

    code = commit.run_commit(
        bench, output_dir=calibrated, operator="op", run_id="r",
        input_func=lambda p: "yes", output_func=lambda s: None,
        clock=lambda: next(ticks), sleep=lambda s: None)

    assert code == commit.EXIT_FAIL


def test_main_prints_the_final_verdict(calibrated):
    lines = []
    code = commit.main(["--operator", "op", "--output-dir", str(calibrated)],
                       input_func=lambda p: "yes", output_func=lines.append,
                       bench_factory=FakeBench)
    assert code == 0 and lines[-1] == "Final result: PASS"
