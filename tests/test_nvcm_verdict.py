"""Unit tests for utils.nvcm_verdict.interpret_boot_probe (issue #44).

Run from the repo root with:  python -m pytest tests/test_nvcm_verdict.py

History: the check first inferred PROGRAMMED from the keyless 0x40 probe
(wrong — 0x40 never ACKs without the activation key), then from STATUS
bit 19 (wrong — it tracks the NVCM Done fuse, so a part whose fuse is
burned but whose image does not boot still reads PROGRAMMED; right sensor
camera 8, 2026-07-17). The check now times the firmware's own non-forced
program path: fpga_detect_nvcm() skips in ~0.1 s when the NVCM design
boots, and SRAM-loads for ~10-15 s when it does not.
"""
import pytest

from utils.nvcm_verdict import (SRAM_LOAD_THRESHOLD_S, interpret_boot_probe,
                                interpret_pin_probe)


def test_pin_probe_booted_is_programmed():
    verdict, detail = interpret_pin_probe(True)
    assert verdict == "PROGRAMMED"
    assert "pin probe" in detail


def test_pin_probe_no_boot_is_blank():
    """Right cam 8 (Done fuse burned, image does not boot) must read BLANK
    on the fast path too — the pin probe is behavioral, not fuse-based."""
    verdict, detail = interpret_pin_probe(False)
    assert verdict == "BLANK"
    assert "unbootable" in detail


def test_fast_program_is_programmed():
    """Bench 2026-07-17, left cam 8 (bootable NVCM): skip took 0.11 s."""
    verdict, detail = interpret_boot_probe(True, True, 0.11)
    assert verdict == "PROGRAMMED"
    assert "0.11 s" in detail


def test_slow_program_is_blank():
    """Bench 2026-07-17, right cam 8 (Done fuse burned, image does not
    boot): the firmware SRAM-loaded it in 13.87 s."""
    verdict, detail = interpret_boot_probe(True, True, 13.87)
    assert verdict == "BLANK"
    assert "13.9 s" in detail


@pytest.mark.parametrize("elapsed,expected", [
    (SRAM_LOAD_THRESHOLD_S - 0.01, "PROGRAMMED"),
    (SRAM_LOAD_THRESHOLD_S, "BLANK"),
    (SRAM_LOAD_THRESHOLD_S + 0.01, "BLANK"),
])
def test_threshold_boundary(elapsed, expected):
    verdict, _ = interpret_boot_probe(True, True, elapsed)
    assert verdict == expected


def test_reset_failure_is_inconclusive():
    """Without a successful OW_FPGA_RESET the isProgrammed cache may be
    stale, and program_fpga would return instantly without running the
    boot test — the timing carries no signal."""
    verdict, detail = interpret_boot_probe(False, False, 0.0)
    assert verdict == "INCONCLUSIVE"
    assert "reset" in detail.lower()


def test_program_failure_is_no_response():
    verdict, detail = interpret_boot_probe(True, False, 0.4)
    assert verdict == "NO RESPONSE"
    assert "failed" in detail


def test_reset_failure_wins_over_program_result():
    # Even if a (nonsensical) fast success were reported, a failed reset
    # means the cache may have answered — never report PROGRAMMED.
    verdict, _ = interpret_boot_probe(False, True, 0.05)
    assert verdict == "INCONCLUSIVE"
