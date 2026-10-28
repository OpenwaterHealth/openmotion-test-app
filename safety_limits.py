"""Read-only view of the limits the console loaded into its safety FPGAs.

A console that owns its laser-safety state (console firmware 1.8.2 and later,
PTR-2026-1-5) decodes each safety FPGA's per-unit record from UFM at boot and
writes its six fields into the matching EE/OPT registers, reading each one
back (openmotion-console-fw ``safety_envelope.c``). A Release image cannot read
UFM over USB, so the registers are what the host can see of the record:

* attestation reports each device's record as valid (``ST_UFM_VALID``) or not;
* a register write or read-back failure sets ``TRIG_INTERLOCK_LIMITS`` (0x20).

With both records valid and 0x20 clear, the registers hold the records raw
count for raw count. A Factory image applies safety keys from the stored
configuration after the record, so there the registers may differ from it.

Pure Python: the connector passes in the register reader and the SDK replies.
"""

from __future__ import annotations

from typing import Callable, Optional

from omotion.config import TRIG_INTERLOCK_LIMITS, describe_trigger_refusal
from omotion.fpga_attest import (
    ATTEST_STATE_NAMES,
    ST_FAULT_MASK,
    ST_NOT_CHECKED,
    ST_PENDING,
    ST_UFM_VALID,
)

#: The six fields of a safety record, as register suffixes, in record order.
RECORD_FIELDS = (
    "PULSE_WIDTH_LL",
    "PULSE_WIDTH_UL",
    "RATE_LL",
    "DRIVE_CL",
    "PWM_CURRENT",
    "CW_CURRENT",
)

#: Safety device name -> attestation device index (``DEVICE_NAMES``).
SAFETY_DEVICES = (("EE", 2), ("OPT", 3))

#: The operating point the host sets, bounded by the records.
SETPOINTS = ("TA_CURRENT_DRV", "TA_PULSE_WIDTH")

# reader(friendly_name) -> (raw, scale, unit), or None when the read fails.
Reader = Callable[[str], Optional[tuple]]


def _row(reader: Reader, register: str) -> dict:
    got = reader(register)
    if got is None:
        return {"register": register, "raw": "--", "value": "read failed", "ok": False}
    raw, scale, unit = got
    value = f"{raw * scale:.2f} {unit}".strip() if scale else f"0x{raw:X}"
    return {"register": register, "raw": str(raw), "value": value, "ok": True}


def _record_state(report, index: int) -> tuple[str, bool]:
    """(text, valid) for one safety device's UFM record."""
    if report is None:
        return "unknown (no attestation report)", False
    dev = next((d for d in report.devices if d.device == index), None)
    if dev is None:
        return "unknown (not in the attestation report)", False
    if dev.status & ST_UFM_VALID and not dev.status & ST_FAULT_MASK:
        return "VALID", True
    if dev.status & (ST_PENDING | ST_NOT_CHECKED) and not dev.status & ST_FAULT_MASK:
        return "not checked yet", False
    return "NOT VALID: " + dev.describe().split(": ", 1)[-1], False


def build_view(reader: Reader, boot_info, report) -> dict:
    """Everything the Console page's Safety Limits dialog shows."""
    view = {
        "supported": bool(boot_info is not None and boot_info.firmware_owns_safety_state),
        "factory": bool(boot_info is not None and boot_info.factory_image),
        "attestation": "unknown",
        "interlocks": "unknown",
        "devices": [],
        "setpoints": [_row(reader, r) for r in SETPOINTS],
        "verdict": "",
        "verdictOk": False,
    }
    if not view["supported"]:
        view["verdict"] = (
            "The console did not answer the boot-info request, so the record state is unknown."
            if boot_info is None else
            "This console firmware does not load a safety record "
            "(it needs 1.8.2 or later); the registers are not from UFM.")
        view["devices"] = [{"name": name, "record": "not loaded by this firmware", "valid": False,
                            "rows": [_row(reader, f"{name}_{f}") for f in RECORD_FIELDS]}
                           for name, _ in SAFETY_DEVICES]
        return view

    if report is not None:
        view["attestation"] = ATTEST_STATE_NAMES.get(report.state, f"state {report.state}").upper()
    interlocks = boot_info.interlocks
    if interlocks is not None:
        view["interlocks"] = ("none" if interlocks == 0
                              else f"0x{interlocks:02X}: {describe_trigger_refusal(interlocks)}")

    all_valid = True
    for name, index in SAFETY_DEVICES:
        text, valid = _record_state(report, index)
        rows = [_row(reader, f"{name}_{f}") for f in RECORD_FIELDS]
        all_valid = all_valid and valid and all(r["ok"] for r in rows)
        view["devices"].append({"name": name, "record": text, "valid": valid, "rows": rows})

    limits_applied = interlocks is not None and not interlocks & TRIG_INTERLOCK_LIMITS
    if not all_valid:
        view["verdict"] = "A safety record is not valid or a register did not read: the limits are not the unit's record."
    elif not limits_applied:
        view["verdict"] = "Records valid, but a limit was not applied (interlock 0x20): the registers may differ from the records."
    elif view["factory"]:
        view["verdict"] = ("Records valid. Factory image: a safety key in the stored configuration "
                           "overrides the record, so the registers may differ from it.")
    else:
        view["verdict"] = "Records valid and applied: the registers hold the UFM records."
        view["verdictOk"] = True
    return view
