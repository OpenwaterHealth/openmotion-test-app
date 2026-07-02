"""Interpret an OW_FACTORY_NVCM_CHECK response blob. Pure logic, no Qt.

Blob layout (sensor-fw ``if_factory_prog.c``, command 0x6C)::

    [0:4]   IDCODE            [4]  idcode_ok        [5]   step_status
    [6:10]  STATUS (big-endian, bits[31:24] first)
    [10:18] feature_row       [18:20] feabits       [20:24] usercode
    [24]    boot_probe_done   [25] boot_0x40_responds
    [26]    num_rows_read     [27:] NVCM rows (16 B each)

The programmed/blank verdict comes from STATUS **bit 19** (byte
``blob[7]`` bit 3 — big-endian word bit 0x00080000). Empirically verified
on hardware 2026-07-02 with a 16-camera blind sweep: all 15
NVCM-programmed cameras read STATUS ``00 08 02 08`` (bit 19 set) and the
one known-blank camera read ``00 00 02 08`` (bit 19 clear), matching the
known-blank blob in the openmotion-sensor-fw notebook. The SRAM
configuration Done bit (bit 8 -> ``blob[8]`` bit 0) is NOT usable here:
the probe's ISC sequence holds the part unconfigured, so Done reads 0 on
every camera regardless of NVCM state. The feature-row / feabits /
NVCM-row reads return all-0xFF for programmed and blank parts alike in
this mode, so they cannot discriminate either.

The auto-boot bytes ([24]/[25]) are deliberately IGNORED: the CrossLink
I2C slave config port at 0x40 only becomes active after receiving the
activation key, so after a key-less CRESETB release 0x40 never ACKs
regardless of NVCM state. Interpreting "0x40 gone" as "programmed" made
the check report PROGRAMMED for blank parts too (issue #44).
"""

# step_status bits (sensor-fw crosslink.h FPGA_NVCM_STEP_*)
STEP_ACTIVATION = 1 << 0
STEP_IDCODE = 1 << 1
STEP_ISC_ENABLE = 1 << 2
STEP_STATUS = 1 << 3
STEP_FEATROW = 1 << 4
STEP_FEABITS = 1 << 5
STEP_USERCODE = 1 << 6

_MIN_BLOB_LEN = 27


def interpret_nvcm_blob(blob: bytes) -> tuple[str, str]:
    """Reduce an OW_FACTORY_NVCM_CHECK response to a verdict + detail string.

    Returns (verdict, detail) where verdict is one of:
        PROGRAMMED, BLANK, INCONCLUSIVE, NO RESPONSE.
    """
    if not blob:
        return ("NO RESPONSE",
                "firmware returned no data (camera absent/unpowered?)")
    if len(blob) < _MIN_BLOB_LEN:
        return "NO RESPONSE", f"short response ({len(blob)} bytes)"
    if blob[4] != 1:
        return "INCONCLUSIVE", "IDCODE mismatch — check power / mux / CRESETB"
    if not (blob[5] & STEP_STATUS):
        return "INCONCLUSIVE", "STATUS register read failed"
    status_hex = " ".join(f"{b:02X}" for b in blob[6:10])
    if blob[7] & 0x08:  # STATUS bit 19 — NVCM-programmed discriminator
        return "PROGRAMMED", f"STATUS bit 19 set (status {status_hex})"
    return "BLANK", f"STATUS bit 19 clear (status {status_hex})"
