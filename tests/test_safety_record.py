"""Safety-record builder and Safety Calibration evidence lookup (procedures.safety_record)."""

import json

import pytest

from procedures.safety_record import (
    DeviceLimits,
    SafetyRecordError,
    build_record,
    configuration_mismatches,
    decode_record,
    find_passed_safety_calibration,
    limits_from_configuration,
)

# Records produced by openmotion-console-fw scripts/safety_ufm_record.py for
# the same inputs (pulse_ll 0, PWM/CW 140 mA). The first two are the values
# provisioned on bench console 338435643333 (2026-10-22).
FW_TOOL_VECTORS = [
    (("EE", 5000, 660, 23125),
     "4f570100000000000e0800004a1a0100800a6b036b0300000000000000008802"),
    (("OPT", 7143, 660, 23125),
     "4f570100000000000e0800004a1a0100000f6b036b0300000000000000000d02"),
    (("EE", 4287, 550, 23125),
     "4f57010000000000b60600004a1a010000096b036b030000000000000000ad02"),
    (("OPT", 6123.4, 605, 23500.1),
     "4f5701000000000062070000de1e0100dc0c6b036b030000000000000000d103"),
]

CALIBRATED = {
    "TA_PULSE_WIDTH": 500, "TA_CURRENT_DRV": 4950, "SEED_CW_GAIN": 140,
    "EE_PULSE_WIDTH_UL": 550, "EE_RATE_LL": 23125, "EE_DRIVE_CL": 4287,
    "OPT_PULSE_WIDTH_UL": 550, "OPT_RATE_LL": 23125, "OPT_DRIVE_CL": 6123,
    "TEC_TRIP": 40,
}


@pytest.mark.parametrize(("args", "expected"), FW_TOOL_VECTORS)
def test_records_match_the_firmware_repository_tool(args, expected):
    device, drive, pulse, rate = args
    record = build_record(DeviceLimits(device, drive, pulse, rate))
    assert record.hex() == expected
    assert decode_record(device, record)["status"] == "valid"


def test_rounding_is_conservative():
    d = decode_record("EE", build_record(DeviceLimits("EE", 4287, 550, 23125.1)))
    assert d["drive_cl"] <= 4287          # upper limits round down
    assert d["pulse_width_ul_us"] <= 550
    assert d["rate_ll_us"] >= 23125.1     # the rate lower limit rounds up


@pytest.mark.parametrize("limits", [
    DeviceLimits("EE", 5001, 550, 23125),     # above the EE drive ceiling
    DeviceLimits("OPT", 7300, 550, 23125),    # above the OPT ceiling
    DeviceLimits("EE", 0, 550, 23125),        # zero drive limit
    DeviceLimits("EE", 4000, 700, 23125),     # pulse width above 660 us
    DeviceLimits("EE", 4000, 550, 20000),     # rate lower limit below 23125 us
    DeviceLimits("EE", 4000, 550, 23125, pwm_ma=150),
])
def test_records_outside_the_envelope_are_refused(limits):
    with pytest.raises(SafetyRecordError):
        build_record(limits)


def test_decode_reports_blank_and_corrupt_records():
    assert decode_record("EE", bytes(32))["status"] == "blank"
    assert decode_record("EE", b"\xff" * 32)["status"] == "blank"
    record = bytearray(build_record(DeviceLimits("EE", 4287, 550, 23125)))
    record[12] ^= 1
    assert decode_record("EE", bytes(record))["status"] == "bad checksum"


def test_limits_come_from_the_wi15_configuration_keys():
    limits = limits_from_configuration(CALIBRATED)
    assert limits["EE"] == DeviceLimits("EE", 4287.0, 550.0, 23125.0)
    assert limits["OPT"] == DeviceLimits("OPT", 6123.0, 550.0, 23125.0)


@pytest.mark.parametrize("bad", [None, "4287", True, float("nan")])
def test_a_missing_or_non_numeric_value_is_refused(bad):
    config = dict(CALIBRATED)
    if bad is None:
        del config["EE_DRIVE_CL"]
    else:
        config["EE_DRIVE_CL"] = bad
    with pytest.raises(SafetyRecordError, match="EE_DRIVE_CL"):
        limits_from_configuration(config)


def test_mismatches_cover_only_the_record_keys():
    console = dict(CALIBRATED, TA_CURRENT_DRV=4000, OPT_DRIVE_CL=6200)
    lines = configuration_mismatches(CALIBRATED, console)
    assert len(lines) == 1 and lines[0].startswith("OPT_DRIVE_CL")
    assert configuration_mismatches(CALIBRATED, dict(CALIBRATED)) == []


def _write_run(root, name, *, serial="C-1", status="passed", ended="2026-10-27T10:00:00+00:00",
               configuration=CALIBRATED):
    run_dir = root / name
    run_dir.mkdir(parents=True)
    payload = {
        "status": status,
        "console_identity": {"role": "console", "serial": serial},
        "ee_calculation": {},
        "post_restart_configuration": configuration,
        "ended_at": ended,
    }
    path = run_dir / f"{serial}-safety-cal-run.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_evidence_lookup_takes_the_latest_passed_run_for_the_serial(tmp_path):
    _write_run(tmp_path, "C-1-safety-cal-a", ended="2026-10-26T09:00:00+00:00",
               configuration=dict(CALIBRATED, EE_DRIVE_CL=4000))
    latest = _write_run(tmp_path, "C-1-safety-cal-b", ended="2026-10-27T09:00:00+00:00")
    _write_run(tmp_path, "C-1-safety-cal-c", status="failed", ended="2026-10-28T09:00:00+00:00")
    _write_run(tmp_path, "C-2-safety-cal-d", serial="C-2", ended="2026-10-29T09:00:00+00:00")
    (tmp_path / "laser").mkdir()
    (tmp_path / "laser" / "C-1-single-laser-run.json").write_text(
        json.dumps({"status": "passed"}), encoding="utf-8")

    found = find_passed_safety_calibration(tmp_path, "C-1")

    assert found.path == latest
    assert found.configuration["EE_DRIVE_CL"] == 4287
    assert len(found.sha256) == 64


def test_evidence_lookup_finds_nothing_without_a_passed_run(tmp_path):
    _write_run(tmp_path, "C-1-safety-cal-a", status="failed")
    assert find_passed_safety_calibration(tmp_path, "C-1") is None
    assert find_passed_safety_calibration(tmp_path / "missing", "C-1") is None
