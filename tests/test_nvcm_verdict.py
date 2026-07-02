"""Unit tests for utils.nvcm_verdict.interpret_nvcm_blob (issue #44).

Run from the repo root with:  python -m pytest tests/test_nvcm_verdict.py

The old interpreter derived PROGRAMMED from the auto-boot "0x40 stopped
ACKing" signal, which is unconditionally true on this part (the CrossLink
config port needs the activation key to respond at all), so every camera
with a valid IDCODE read as PROGRAMMED. The verdict must instead come from
the STATUS register Done bit (blob[8] bit 0).
"""
import pytest

from utils.nvcm_verdict import STEP_STATUS, interpret_nvcm_blob

IDCODE = bytes([0x01, 0x2C, 0x00, 0x43])


def make_blob(idcode_ok=1, step_status=0x7F, status=b"\x00\x00\x02\x08",
              feature_row=b"\xFF" * 8, feabits=b"\xFF\xFF",
              usercode=b"\x00" * 4, boot_probe_done=0,
              boot_0x40_responds=0, rows=(b"\xFF" * 16,)):
    """Build an OW_FACTORY_NVCM_CHECK response blob (see nvcm_verdict)."""
    blob = bytearray()
    blob += IDCODE
    blob.append(idcode_ok)
    blob.append(step_status)
    blob += status
    blob += feature_row
    blob += feabits
    blob += usercode
    blob.append(boot_probe_done)
    blob.append(boot_0x40_responds)
    blob.append(len(rows))
    for row in rows:
        blob += row
    return bytes(blob)


def test_empty_blob_is_no_response():
    verdict, detail = interpret_nvcm_blob(b"")
    assert verdict == "NO RESPONSE"
    assert "no data" in detail


def test_short_blob_is_no_response():
    verdict, detail = interpret_nvcm_blob(b"\x01\x2C\x00\x43\x01")
    assert verdict == "NO RESPONSE"
    assert "5 bytes" in detail


def test_idcode_mismatch_is_inconclusive():
    verdict, _ = interpret_nvcm_blob(make_blob(idcode_ok=0))
    assert verdict == "INCONCLUSIVE"


def test_missing_status_read_is_inconclusive():
    # step_status without the STATUS bit: Done can't be trusted.
    verdict, detail = interpret_nvcm_blob(
        make_blob(step_status=0x7F & ~STEP_STATUS))
    assert verdict == "INCONCLUSIVE"
    assert "STATUS" in detail


def test_blank_part_regression_issue_44():
    """Real blank-camera blob (sensor-fw NVCM notebook, cam8 pre-burn):
    STATUS 00 00 02 08 -> Done=0, but the boot test reported "0x40 gone"
    (boot_0x40_responds=0), which the old code read as PROGRAMMED."""
    blob = make_blob(status=b"\x00\x00\x02\x08",
                     boot_probe_done=1, boot_0x40_responds=0)
    verdict, detail = interpret_nvcm_blob(blob)
    assert verdict == "BLANK"
    assert "00 00 02 08" in detail


def test_programmed_part_done_bit_set():
    # Done = STATUS bit 8 = blob[8] bit 0 (big-endian status bytes).
    blob = make_blob(status=b"\x00\x00\x03\x08")
    verdict, detail = interpret_nvcm_blob(blob)
    assert verdict == "PROGRAMMED"
    assert "00 00 03 08" in detail


def test_boot_test_bytes_are_ignored():
    """The auto-boot bytes carry no information; the verdict must not
    change with them (old code flipped PROGRAMMED/BLANK on blob[25])."""
    for done_byte, expected in ((0x02, "BLANK"), (0x03, "PROGRAMMED")):
        status = bytes([0x00, 0x00, done_byte, 0x08])
        verdicts = {
            interpret_nvcm_blob(make_blob(status=status,
                                          boot_probe_done=probe,
                                          boot_0x40_responds=ack))[0]
            for probe in (0, 1) for ack in (0, 1)
        }
        assert verdicts == {expected}


def test_no_rows_blob_is_still_parseable():
    # num_rows=0 gives the 27-byte minimum layout.
    blob = make_blob(rows=())
    assert len(blob) == 27
    verdict, _ = interpret_nvcm_blob(blob)
    assert verdict == "BLANK"


@pytest.mark.parametrize("extra", [b"", b"\x00" * 16])
def test_verdict_independent_of_row_payload(extra):
    blob = make_blob(rows=(b"\x00" * 16,)) + extra
    verdict, _ = interpret_nvcm_blob(blob)
    assert verdict == "BLANK"
