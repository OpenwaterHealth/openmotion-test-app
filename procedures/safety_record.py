"""Per-unit safety record of the console's safety FPGAs (EE and OPT UFM).

A console that owns its laser-safety state (console firmware 1.8.2 and later,
PTR-2026-1-5) loads each safety FPGA's limits from a 32-byte record in that
device's UFM, pages 0-1, and attests it at every boot. A Release image cannot
write the record; a Factory image can, through the ``fpga_prog_*`` UFM page
commands. This module builds that record from the values WI-00015 Safety
Calibration produced, and checks those values against the run's evidence and
the console's stored configuration before anything is written.

The record layout and the envelope mirror openmotion-console-fw
``Core/Inc/safety_ufm_record.h``, ``Core/Inc/safety_envelope.h`` and
``scripts/safety_ufm_record.py`` (layout 1, magic "OW", raw register values,
16-bit truncated byte-sum checksum). Values are converted to raw register
values rounding to the conservative side (upper limits down, the rate lower
limit up), so a record built from in-envelope values stays in the envelope.

Pure Python: no device access here (see ``wi15_commit_safety_record``).
Planned to move into the SDK (``omotion``) for 1.16.0.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

MAGIC = b"OW"
LAYOUT = 1
RECORD_SIZE = 32
PAGE_BYTES = 16
DEVICES = ("EE", "OPT")
# magic, layout, reserved, pw_ll, pw_ul, rate_ll, drive_cl, pwm, cw, reserved[8], checksum
_FMT = "<2sBBIIIHHH8sH"

# Register LSBs (console fw fpga_register_map.c, safety_envelope.h SAFETY_FPGA_*_LSB)
TIME_LSB_US = 0.320
DRIVE_LSB = 1.86
CURRENT_LSB_MA = 0.160

# Envelope, engineering units (console fw safety_envelope.h)
PULSE_UL_MAX_US = 660
RATE_LL_MIN_US = 23125
DRIVE_CL_MAX = {"EE": 5000, "OPT": 7200}
PWM_MAX_MA = 140
CW_MAX_MA = 140

# Not set by WI-00015: the safety design's reset values go into the record.
DEFAULT_PULSE_LL_US = 0.0
DEFAULT_PWM_MA = 140.0
DEFAULT_CW_MA = 140.0

#: The User Configuration keys a record is built from, per device.
RECORD_KEYS = {
    device: (f"{device}_DRIVE_CL", f"{device}_PULSE_WIDTH_UL", f"{device}_RATE_LL")
    for device in DEVICES
}
ALL_RECORD_KEYS = tuple(key for keys in RECORD_KEYS.values() for key in keys)


class SafetyRecordError(ValueError):
    """A record cannot be built, or its source values do not agree."""


@dataclass(frozen=True)
class DeviceLimits:
    """One safety device's limits in engineering units."""

    device: str
    drive_cl: float
    pulse_ul_us: float
    rate_ll_us: float
    pulse_ll_us: float = DEFAULT_PULSE_LL_US
    pwm_ma: float = DEFAULT_PWM_MA
    cw_ma: float = DEFAULT_CW_MA


def checksum(record: bytes) -> int:
    return sum(record[:30]) & 0xFFFF


def _floor_raw(value: float, lsb: float) -> int:
    return math.floor(value / lsb + 1e-9)


def _ceil_raw(value: float, lsb: float) -> int:
    return math.ceil(value / lsb - 1e-9)


def envelope_problems(device: str, raw: Mapping[str, int]) -> list[str]:
    """The firmware's ``safety_ufm_limits_in_envelope()``, on raw values."""
    problems = []
    if raw["pw_ll"] > raw["pw_ul"]:
        problems.append("pulse width lower limit above the upper limit")
    if raw["pw_ul"] * TIME_LSB_US > PULSE_UL_MAX_US:
        problems.append(
            f"pulse width upper limit {raw['pw_ul'] * TIME_LSB_US:.2f} us > {PULSE_UL_MAX_US}")
    if raw["rate_ll"] * TIME_LSB_US < RATE_LL_MIN_US:
        problems.append(f"rate lower limit {raw['rate_ll'] * TIME_LSB_US:.2f} us < {RATE_LL_MIN_US}")
    if raw["drive_cl"] == 0 or raw["drive_cl"] * DRIVE_LSB > DRIVE_CL_MAX[device]:
        problems.append(
            f"drive current limit {raw['drive_cl'] * DRIVE_LSB:.2f} outside "
            f"(0, {DRIVE_CL_MAX[device]}]")
    if raw["pwm"] * CURRENT_LSB_MA > PWM_MAX_MA:
        problems.append(f"PWM current limit {raw['pwm'] * CURRENT_LSB_MA:.2f} mA > {PWM_MAX_MA}")
    if raw["cw"] * CURRENT_LSB_MA > CW_MAX_MA:
        problems.append(f"CW current limit {raw['cw'] * CURRENT_LSB_MA:.2f} mA > {CW_MAX_MA}")
    return problems


def build_record(limits: DeviceLimits) -> bytes:
    """The 32-byte record for one device; refuses anything outside the envelope."""
    if limits.device not in DEVICES:
        raise SafetyRecordError(f"unknown safety device {limits.device!r}")
    # Checked before rounding as well: rounding down would quietly pull a
    # value just above a ceiling inside it, and a calibrated value above the
    # envelope means something upstream is wrong.
    over = []
    if limits.drive_cl > DRIVE_CL_MAX[limits.device]:
        over.append(f"drive current limit {limits.drive_cl:g} > {DRIVE_CL_MAX[limits.device]}")
    if limits.pulse_ul_us > PULSE_UL_MAX_US:
        over.append(f"pulse width upper limit {limits.pulse_ul_us:g} us > {PULSE_UL_MAX_US}")
    if limits.pwm_ma > PWM_MAX_MA:
        over.append(f"PWM current limit {limits.pwm_ma:g} mA > {PWM_MAX_MA}")
    if limits.cw_ma > CW_MAX_MA:
        over.append(f"CW current limit {limits.cw_ma:g} mA > {CW_MAX_MA}")
    if over:
        raise SafetyRecordError(
            f"{limits.device} record outside the safety envelope: " + "; ".join(over))
    raw = {
        "pw_ll": _floor_raw(limits.pulse_ll_us, TIME_LSB_US),
        "pw_ul": _floor_raw(limits.pulse_ul_us, TIME_LSB_US),
        "rate_ll": _ceil_raw(limits.rate_ll_us, TIME_LSB_US),
        "drive_cl": _floor_raw(limits.drive_cl, DRIVE_LSB),
        "pwm": _floor_raw(limits.pwm_ma, CURRENT_LSB_MA),
        "cw": _floor_raw(limits.cw_ma, CURRENT_LSB_MA),
    }
    problems = envelope_problems(limits.device, raw)
    if problems:
        raise SafetyRecordError(
            f"{limits.device} record outside the safety envelope: " + "; ".join(problems))
    body = struct.pack(_FMT[:-1], MAGIC, LAYOUT, 0, raw["pw_ll"], raw["pw_ul"],
                       raw["rate_ll"], raw["drive_cl"], raw["pwm"], raw["cw"], bytes(8))
    return body + struct.pack("<H", checksum(body))


def decode_record(device: str, record: bytes) -> dict[str, object]:
    """Engineering-unit view of a record and its validity, as the firmware judges it."""
    record = bytes(record)
    if len(record) < RECORD_SIZE:
        return {"status": f"short record ({len(record)} bytes)"}
    record = record[:RECORD_SIZE]
    if record in (bytes(RECORD_SIZE), b"\xff" * RECORD_SIZE):
        return {"status": "blank"}
    magic, layout, res0, pw_ll, pw_ul, rate_ll, drive_cl, pwm, cw, res, csum = struct.unpack(
        _FMT, record)
    raw = dict(pw_ll=pw_ll, pw_ul=pw_ul, rate_ll=rate_ll, drive_cl=drive_cl, pwm=pwm, cw=cw)
    out: dict[str, object] = {
        "pulse_width_ll_us": round(pw_ll * TIME_LSB_US, 2),
        "pulse_width_ul_us": round(pw_ul * TIME_LSB_US, 2),
        "rate_ll_us": round(rate_ll * TIME_LSB_US, 2),
        "drive_cl": round(drive_cl * DRIVE_LSB, 2),
        "pwm_current_ma": round(pwm * CURRENT_LSB_MA, 2),
        "cw_current_ma": round(cw * CURRENT_LSB_MA, 2),
        "raw": raw,
        "checksum": f"0x{csum:04x}",
    }
    if magic != MAGIC:
        out["status"] = "bad magic"
    elif checksum(record) != csum:
        out["status"] = "bad checksum"
    elif layout != LAYOUT:
        out["status"] = "unknown layout"
    elif res0 != 0 or res != bytes(8):
        out["status"] = "reserved bytes set"
    else:
        problems = envelope_problems(device, raw)
        out["status"] = "valid" if not problems else "outside envelope: " + "; ".join(problems)
    return out


def _number(configuration: Mapping[str, object], key: str) -> float:
    value = configuration.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise SafetyRecordError(f"{key} is missing or not a number ({value!r})")
    return float(value)


def limits_from_configuration(configuration: Mapping[str, object]) -> dict[str, DeviceLimits]:
    """EE and OPT limits from a WI-00015 User Configuration."""
    limits = {}
    for device, (drive_key, pulse_key, rate_key) in RECORD_KEYS.items():
        limits[device] = DeviceLimits(
            device=device,
            drive_cl=_number(configuration, drive_key),
            pulse_ul_us=_number(configuration, pulse_key),
            rate_ll_us=_number(configuration, rate_key),
        )
    return limits


def configuration_mismatches(expected: Mapping[str, object],
                             actual: Mapping[str, object]) -> list[str]:
    """One line per record key whose value differs (or is missing on either side)."""
    lines = []
    for key in ALL_RECORD_KEYS:
        if key not in expected or key not in actual or expected[key] != actual[key]:
            lines.append(f"{key}: calibration evidence {expected.get(key, 'missing')!r}, "
                         f"console {actual.get(key, 'missing')!r}")
    return lines


@dataclass(frozen=True)
class SafetyCalibrationEvidence:
    """The latest PASSED Safety Calibration run for one console."""

    path: Path
    sha256: str
    console_serial: str
    ended_at: str
    configuration: Mapping[str, object]


def _status_text(value: object) -> str:
    if isinstance(value, Mapping):
        value = value.get("value", value.get("name"))
    return str(value).lower()


def _is_safety_calibration_result(payload: object) -> bool:
    return (isinstance(payload, Mapping)
            and "ee_calculation" in payload
            and "post_restart_configuration" in payload
            and "console_identity" in payload)


def find_passed_safety_calibration(output_dir: str | Path,
                                   console_serial: str) -> SafetyCalibrationEvidence | None:
    """The most recent PASSED Safety Calibration run.json for ``console_serial``.

    Looks one level down in ``output_dir`` (each run has its own directory) at
    every ``*run.json``. The values a record is built from are the run's
    ``post_restart_configuration``: what the console held after the observed
    power cycle, which the run verified.
    """
    root = Path(output_dir)
    best: SafetyCalibrationEvidence | None = None
    for path in sorted(root.glob("*/*run.json")):
        try:
            raw = path.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError):
            continue
        if not _is_safety_calibration_result(payload):
            continue
        if _status_text(payload.get("status")) != "passed":
            continue
        identity = payload.get("console_identity") or {}
        serial = str(identity.get("serial") or "").strip()
        configuration = payload.get("post_restart_configuration")
        if serial != console_serial or not isinstance(configuration, Mapping):
            continue
        ended_at = str(payload.get("ended_at") or "")
        if best is None or ended_at > best.ended_at:
            best = SafetyCalibrationEvidence(
                path=path,
                sha256=hashlib.sha256(raw).hexdigest(),
                console_serial=serial,
                ended_at=ended_at,
                configuration=dict(configuration),
            )
    return best
