"""Why the console refused Start Trigger, in words an operator can act on (#130).

Console firmware 1.8.2 refuses ``OW_CTRL_START_TRIG`` while any interlock is
set and answers ``OW_ERROR`` with one status byte. The SDK keeps that byte in
``MotionConsole.last_start_refusal`` and its text in
``last_start_refusal_reason`` (``omotion.config.describe_trigger_refusal``).
This module turns the byte, plus what the app knows about the TEC, into the
line shown next to the Start Trigger button.

The TEC interlock (0x04) is the one refusal that clears on its own: the
firmware's ``tec_trip_evaluate()`` re-arms after 200 consecutive clean polls,
about 5 s after the TEC recovers, so the TEC refusal gets a re-arm wait. The
other interlocks have no fixed wait; the text points at Safety Limits.

Pure Python: the connector passes in the SDK values and its TEC readings.
"""

from __future__ import annotations

import math
from typing import Optional

from omotion.config import (
    TRIG_INTERLOCK_TEC,
    describe_trigger_refusal,
)

#: Seconds from the TEC recovering to the console accepting a start again
#: (200 clean polls of ``tec_trip_evaluate()``, console-fw PR #83).
TEC_REARM_SEC = 5.0

#: rearm_sec when there is no wait to show.
NO_WAIT = -1


def _fmt_temp(value: float) -> str:
    return f"{value:.1f} °C"


def tec_trip_warning(trip_c: Optional[float], laser_temp_c: Optional[float]) -> str:
    """Warning for a TEC Trip at or below the current laser temperature.

    Returns "" when there is nothing to warn about, or when either value is
    unknown (no TEC read yet, or TEC_TRIP not read from the console).
    """
    if trip_c is None or laser_temp_c is None or trip_c <= 0:
        return ""
    if trip_c > laser_temp_c:
        return ""
    return (
        f"TEC Trip {trip_c:g} °C is at or below the laser temperature "
        f"({_fmt_temp(laser_temp_c)}): the TEC interlock will refuse every "
        f"Start Trigger until TEC Trip is raised above it."
    )


def describe_start_refusal(
    status: Optional[int],
    *,
    tec_known: bool = False,
    tec_good: bool = True,
    laser_temp_c: Optional[float] = None,
    trip_c: Optional[float] = None,
    tec_good_since: Optional[float] = None,
    now: Optional[float] = None,
) -> dict:
    """Describe a refused start for the UI.

    ``status`` is ``MotionConsole.last_start_refusal`` (``None`` when the
    console sent no status byte). The TEC arguments come from a fresh
    ``tec_status()`` read: ``tec_good_since`` is the monotonic time the app
    last saw the TEC recover (or wrote a TEC Trip above the laser
    temperature), ``now`` the current monotonic time.

    Returns ``{"text": str, "rearm_sec": int}``. ``rearm_sec`` is the
    seconds left until the console re-arms after a TEC trip, 0 when it
    should have re-armed already, and ``NO_WAIT`` (-1) when waiting will not
    help, or when the TEC has not recovered yet so no countdown can start.
    """
    reason = describe_trigger_refusal(status)
    code = "" if status is None else f" (status 0x{status:02X})"
    parts = [f"Console refused Start Trigger: {reason}{code}."]
    rearm = NO_WAIT

    if status is not None and status & TRIG_INTERLOCK_TEC:
        if tec_known and not tec_good:
            temps = ""
            if laser_temp_c is not None and trip_c:
                temps = (
                    f" Laser temperature {_fmt_temp(laser_temp_c)}, "
                    f"TEC Trip {trip_c:g} °C."
                )
            parts.append(
                f"TEC trip engaged.{temps} Raise TEC Trip above the laser "
                f"temperature or let the laser cool; the console re-arms about "
                f"{TEC_REARM_SEC:g} s after the TEC recovers."
            )
        else:
            remaining = TEC_REARM_SEC
            if tec_good_since is not None and now is not None:
                remaining = max(0.0, TEC_REARM_SEC - (now - tec_good_since))
            rearm = int(math.ceil(remaining))
            if rearm > 0:
                parts.append(
                    f"TEC has recovered; the console re-arms in about {rearm} s."
                )
            else:
                parts.append("TEC has recovered; try Start Trigger again.")
    elif status is None:
        parts.append(
            "This console firmware does not say why; update it to 1.8.2 or "
            "later to see the interlock."
        )
    else:
        parts.append("Safety Limits on the Console page shows the interlocks.")
    return {"text": " ".join(parts), "rearm_sec": rearm}
