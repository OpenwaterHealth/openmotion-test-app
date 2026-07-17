"""Verdict logic for the FPGA NVCM programmed check. Pure logic, no Qt.

The check drives the sensor firmware's own NVCM boot detector — the same
one the scan path trusts — instead of reading CrossLink status registers:

    OW_FPGA_RESET       reset_camera(): CRESETB reset and clears the
                        firmware's isProgrammed cache (~1 s).
    OW_FPGA_PROG_SRAM   program_fpga(cam, force_update=false): runs
    (reserved=1)        fpga_detect_nvcm() — keyless CRESETB release, then
                        checks whether the booted user design is driving
                        the camera bus clk/data pins low.
                        NVCM boots   -> returns OK in ~0.1 s (skip)
                        no NVCM boot -> full SRAM load, ~10-15 s

The verdict comes from the elapsed time of the program call: the skip and
SRAM-load paths differ by two orders of magnitude (0.11 s vs 12.5-13.9 s
measured on hardware 2026-07-17), split at ``SRAM_LOAD_THRESHOLD_S``.

Why not the ISC status-register probe (``OW_FACTORY_NVCM_CHECK``)?  Its
STATUS bit 19 ("SDM Enable") tracks the NVCM Done *fuse*, not bootability:
a part whose Done fuse is burned but whose image does not boot reads
bit19=1 straight from cold power-up, while the firmware correctly
SRAM-loads it on every scan (right sensor camera 8, observed 2026-07-17 —
its config port even wedges after a keyless boot attempt).  The blob's
content reads float 0xFF in this mode and the keyless 0x40 probe carries no
signal (issue #44), so nothing in that blob reflects whether the design
actually boots.  Driving the firmware's pin-drive boot test makes this
checker agree with scan behavior by construction.

Note the check is no longer read-only: a camera whose NVCM does not boot is
left SRAM-configured and running — the same state a scan leaves it in.
"""

# Elapsed-time split between "firmware skipped programming because the NVCM
# design booted" (~0.1 s) and "firmware had to SRAM-load" (>10 s).
SRAM_LOAD_THRESHOLD_S = 2.0


def interpret_boot_probe(reset_ok: bool, program_ok: bool,
                         elapsed_s: float) -> tuple[str, str]:
    """Reduce a reset + timed non-forced program to a verdict + detail.

    Returns (verdict, detail) where verdict is one of:
        PROGRAMMED, BLANK, INCONCLUSIVE, NO RESPONSE.
    """
    if not reset_ok:
        return ("INCONCLUSIVE",
                "FPGA reset (OW_FPGA_RESET) failed — camera absent/unpowered?")
    if not program_ok:
        return ("NO RESPONSE",
                f"FPGA programming failed after {elapsed_s:.1f} s — "
                "camera absent/unpowered?")
    if elapsed_s < SRAM_LOAD_THRESHOLD_S:
        return ("PROGRAMMED",
                f"NVCM design booted — firmware skipped the SRAM load "
                f"({elapsed_s:.2f} s)")
    return ("BLANK",
            f"NVCM did not boot (blank or unbootable image) — firmware "
            f"SRAM-loaded the FPGA ({elapsed_s:.1f} s)")
