"""The Console page's Safety Limits view against fake replies (no hardware)."""

import pytest

from omotion.boot_mode import BootInfo
from omotion.fpga_attest import (
    ATTEST_FAIL,
    ATTEST_PASS,
    ST_DIGEST,
    ST_NOT_CHECKED,
    ST_UFM_BLANK,
    ST_UFM_INVALID,
    ST_UFM_VALID,
    FpgaAttestDevice,
    FpgaAttestReport,
)
from safety_limits import RECORD_FIELDS, build_view

RELEASE = BootInfo(2, 0x08020400, 0x08020000, interlocks=0, fpga_attest=ATTEST_PASS, build=0)
FACTORY = BootInfo(2, 0x08020400, 0x08020000, interlocks=0, fpga_attest=ATTEST_PASS, build=1)

# QWW04Q10018's record as the bench console reads it back.
REGISTERS = {
    "EE_PULSE_WIDTH_LL": (0, 0.32, "uS"), "EE_PULSE_WIDTH_UL": (1718, 0.32, "uS"),
    "EE_RATE_LL": (72266, 0.32, "uS"), "EE_DRIVE_CL": (2106, 1.86, "mA"),
    "EE_PWM_CURRENT": (0, 0.16, "mA"), "EE_CW_CURRENT": (0, 0.16, "mA"),
    "OPT_PULSE_WIDTH_LL": (0, 0.32, "uS"), "OPT_PULSE_WIDTH_UL": (1718, 0.32, "uS"),
    "OPT_RATE_LL": (72266, 0.32, "uS"), "OPT_DRIVE_CL": (572, 1.86, "mA"),
    "OPT_PWM_CURRENT": (0, 0.16, "mA"), "OPT_CW_CURRENT": (0, 0.16, "mA"),
    "TA_CURRENT_DRV": (25000, 0.16, "mA"), "TA_PULSE_WIDTH": (1718, 0.32, "us"),
}


def _report(ee=ST_UFM_VALID, opt=ST_UFM_VALID, state=ATTEST_PASS):
    def dev(i, status):
        return FpgaAttestDevice(i, status, 0, 0, 3198, 900, 0x12B, bytes(32), bytes(32))
    return FpgaAttestReport(1, state, 100, state != ATTEST_PASS, 1, 3198, (
        dev(0, ST_NOT_CHECKED), dev(1, ST_NOT_CHECKED), dev(2, ee), dev(3, opt)))


def _device(view, name):
    return next(d for d in view["devices"] if d["name"] == name)


def _value(view, register):
    rows = [r for d in view["devices"] for r in d["rows"]] + view["setpoints"]
    return next(r["value"] for r in rows if r["register"] == register)


def test_valid_records_on_release_show_the_record_values():
    view = build_view(REGISTERS.get, RELEASE, _report())

    assert view["verdictOk"]
    assert "hold the UFM records" in view["verdict"]
    assert view["attestation"] == "PASS" and view["interlocks"] == "none"
    assert _device(view, "EE")["record"] == "VALID"
    assert [r["register"] for r in _device(view, "OPT")["rows"]] == [f"OPT_{f}" for f in RECORD_FIELDS]
    assert _value(view, "EE_DRIVE_CL") == "3917.16 mA"
    assert _value(view, "OPT_DRIVE_CL") == "1063.92 mA"
    assert _value(view, "EE_PULSE_WIDTH_UL") == "549.76 uS"
    assert _value(view, "EE_RATE_LL") == "23125.12 uS"
    assert _value(view, "TA_CURRENT_DRV") == "4000.00 mA"


@pytest.mark.parametrize(("report", "device", "text"), [
    (_report(opt=ST_UFM_INVALID, state=ATTEST_FAIL), "OPT", "NOT VALID: safety record invalid"),
    (_report(ee=ST_UFM_BLANK, state=ATTEST_FAIL), "EE", "NOT VALID: no safety record"),
    (_report(ee=ST_UFM_VALID | ST_DIGEST, state=ATTEST_FAIL), "EE", "NOT VALID: configuration digest"),
    (_report(ee=ST_NOT_CHECKED), "EE", "not checked yet"),
])
def test_a_record_that_is_not_valid_is_named(report, device, text):
    view = build_view(REGISTERS.get, RELEASE, report)

    assert not view["verdictOk"]
    assert not _device(view, device)["valid"]
    assert _device(view, device)["record"].startswith(text)
    assert "not the unit's record" in view["verdict"]


def test_no_attestation_report_is_not_valid():
    view = build_view(REGISTERS.get, RELEASE, None)
    assert not view["verdictOk"] and _device(view, "EE")["record"].startswith("unknown")


def test_a_limit_not_applied_is_flagged():
    info = BootInfo(2, 0x08020400, 0x08020000, interlocks=0x20, fpga_attest=ATTEST_PASS, build=0)

    view = build_view(REGISTERS.get, info, _report())

    assert not view["verdictOk"]
    assert "interlock 0x20" in view["verdict"]
    assert view["interlocks"].startswith("0x20: a safety limit")


def test_factory_image_says_the_configuration_may_override_the_record():
    view = build_view(REGISTERS.get, FACTORY, _report())
    assert view["factory"] and not view["verdictOk"]
    assert "Factory image" in view["verdict"]


def test_a_register_that_does_not_read_fails_the_view():
    registers = dict(REGISTERS)
    del registers["OPT_DRIVE_CL"]

    view = build_view(registers.get, RELEASE, _report())

    assert not view["verdictOk"]
    assert _value(view, "OPT_DRIVE_CL") == "read failed"


@pytest.mark.parametrize(("info", "verdict"), [
    (BootInfo(1, 0x08020400, 0x08020000), "does not load a safety record"),
    (None, "did not answer the boot-info request"),
])
def test_without_a_loaded_record_the_registers_are_still_shown(info, verdict):
    view = build_view(REGISTERS.get, info, None)

    assert not view["supported"] and not view["verdictOk"]
    assert verdict in view["verdict"]
    assert _value(view, "EE_DRIVE_CL") == "3917.16 mA"
