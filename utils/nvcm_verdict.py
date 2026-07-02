"""Interpret an OW_FACTORY_NVCM_CHECK response blob. Pure logic, no Qt.

Blob layout (sensor-fw ``if_factory_prog.c``, command 0x6C)::

    [0:4]   IDCODE            [4]  idcode_ok        [5]   step_status
    [6:10]  STATUS (big-endian, bits[31:24] first)
    [10:18] feature_row       [18:20] feabits       [20:24] usercode
    [24]    boot_probe_done   [25] boot_0x40_responds
    [26]    num_rows_read     [27:] NVCM rows (16 B each)

The programmed/blank verdict comes from the STATUS register **Done bit**
(bit 8 -> byte ``blob[8]`` bit 0). Done is the last fuse burned during NVCM
programming and is what gates auto-boot, so Done=1 means a complete,
bootable NVCM image; Done=0 means blank (or an incomplete burn that will
not boot). Verified on hardware in openmotion-sensor-fw commit c40a6b4.

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
    if blob[8] & 0x01:  # STATUS Done bit (bit 8)
        return "PROGRAMMED", f"STATUS Done bit set (status {status_hex})"
    return "BLANK", f"STATUS Done bit clear (status {status_hex})"
