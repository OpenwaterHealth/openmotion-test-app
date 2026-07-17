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
                                interpret_check_blob)

IDCODE = bytes([0x01, 0x2C, 0x00, 0x43])


def make_blob(status=b"\x00\x00\x02\x08", step_status=0x7F,
              rows=(b"\xFF" * 16,), boot_byte=None):
    """OW_FACTORY_NVCM_CHECK blob: 27 fixed bytes + 16 B/row, and since
    sensor-fw #91 a trailing pin-drive boot verdict byte."""
    blob = bytearray()
    blob += IDCODE                    # [0:4] idcode
    blob.append(1)                    # [4] idcode_ok
    blob.append(step_status)          # [5] step_status
    blob += status                    # [6:10] STATUS, big-endian
    blob += b"\xFF" * 8               # [10:18] feature_row (floats)
    blob += b"\xFF\xFF"               # [18:20] feabits
    blob += b"\x00" * 4               # [20:24] usercode
    blob += b"\x00\x00"               # [24] boot_probe_done, [25] 0x40 resp
    blob.append(len(rows))            # [26] num_rows_read
    for row in rows:
        blob += row
    if boot_byte is not None:
        blob.append(boot_byte)
    return bytes(blob)


def test_check_blob_booted_is_programmed():
    verdict, detail = interpret_check_blob(make_blob(boot_byte=1))
    assert verdict == "PROGRAMMED"
    assert "pin probe" in detail


def test_check_blob_no_boot_blank_fuse_clear():
    verdict, detail = interpret_check_blob(
        make_blob(status=b"\x00\x00\x02\x08", boot_byte=0))
    assert verdict == "BLANK"
    assert "NVCM blank" in detail


def test_check_blob_no_boot_with_burned_fuse_is_flagged():
    """Right cam 8's exact signature: STATUS bit 19 set (Done fuse burned)
    but the design does not boot — the OTP part is dead for NVCM."""
    verdict, detail = interpret_check_blob(
        make_blob(status=b"\x00\x08\x02\x08", boot_byte=0))
    assert verdict == "BLANK"
    assert "cannot be NVCM-flashed again" in detail


def test_check_blob_probe_refused_is_no_response():
    verdict, detail = interpret_check_blob(make_blob(boot_byte=0xFF))
    assert verdict == "NO RESPONSE"
    assert "not powered" in detail


def test_check_blob_without_verdict_byte_returns_none():
    """Pre-#91 firmware blob (no trailing byte) -> caller must fall back."""
    assert interpret_check_blob(make_blob(boot_byte=None)) is None


def test_check_blob_row_bytes_not_mistaken_for_verdict():
    """The verdict offset must skip the variable-length rows: a two-row
    blob with no trailing byte has 0x01-looking bytes inside the rows."""
    rows = (b"\x01" * 16, b"\x01" * 16)
    assert interpret_check_blob(make_blob(rows=rows, boot_byte=None)) is None
    verdict, _ = interpret_check_blob(make_blob(rows=rows, boot_byte=0))
    assert verdict == "BLANK"


def test_check_blob_empty_or_short_returns_none():
    assert interpret_check_blob(b"") is None
    assert interpret_check_blob(b"\x01\x2C\x00\x43\x01") is None


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
