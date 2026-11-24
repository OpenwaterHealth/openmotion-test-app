"""Unit tests for trigger_refusal (issue #130).

Run from the repo root with:
    python -m pytest tests/test_trigger_refusal.py

A refused Start Trigger used to show only "Failed to apply and start trigger
config". These pin the text that now appears next to the button, and the TEC
Trip warning.
"""
from omotion.config import (
    TRIG_INTERLOCK_LASER_SAFETY,
    TRIG_INTERLOCK_TEC,
)

from trigger_refusal import (
    NO_WAIT,
    TEC_REARM_SEC,
    describe_start_refusal,
    tec_trip_warning,
)


# --- TEC Trip warning --------------------------------------------------------

def test_trip_below_laser_temperature_warns():
    text = tec_trip_warning(23, 25.4)
    assert "TEC Trip 23 °C" in text
    assert "25.4 °C" in text
    assert "refuse every Start Trigger" in text


def test_trip_equal_to_laser_temperature_warns():
    assert tec_trip_warning(25, 25.0)


def test_trip_above_laser_temperature_is_quiet():
    assert tec_trip_warning(40, 25.4) == ""


def test_unknown_values_are_quiet():
    """No TEC read yet, or TEC_TRIP not read: no basis for a warning."""
    assert tec_trip_warning(None, 25.4) == ""
    assert tec_trip_warning(23, None) == ""
    assert tec_trip_warning(0, 25.4) == ""


# --- Refusal text ------------------------------------------------------------

def test_tec_refusal_while_tripped_names_temps_and_has_no_countdown():
    r = describe_start_refusal(
        TRIG_INTERLOCK_TEC, tec_known=True, tec_good=False,
        laser_temp_c=25.4, trip_c=23,
    )
    assert "TEC thermal trip" in r["text"]
    assert "status 0x04" in r["text"]
    assert "TEC trip engaged" in r["text"]
    assert "Laser temperature 25.4 °C, TEC Trip 23 °C" in r["text"]
    # Still tripped: the wait has not started, so nothing to count down.
    assert r["rearm_sec"] == NO_WAIT


def test_tec_refusal_inside_rearm_window_counts_down():
    """Issue #130 step 4: TEC Trip raised back to 40, Start pressed 3 s later."""
    r = describe_start_refusal(
        TRIG_INTERLOCK_TEC, tec_known=True, tec_good=True,
        laser_temp_c=25.4, trip_c=40, tec_good_since=100.0, now=103.0,
    )
    assert r["rearm_sec"] == 2
    assert "re-arms in about 2 s" in r["text"]


def test_tec_refusal_after_window_says_retry():
    r = describe_start_refusal(
        TRIG_INTERLOCK_TEC, tec_known=True, tec_good=True,
        tec_good_since=100.0, now=100.0 + TEC_REARM_SEC + 1,
    )
    assert r["rearm_sec"] == 0
    assert "try Start Trigger again" in r["text"]


def test_tec_refusal_unknown_recovery_assumes_full_window():
    r = describe_start_refusal(TRIG_INTERLOCK_TEC, tec_known=True, tec_good=True)
    assert r["rearm_sec"] == int(TEC_REARM_SEC)


def test_laser_safety_refusal_has_no_wait():
    r = describe_start_refusal(TRIG_INTERLOCK_LASER_SAFETY)
    assert "laser-safety fault latched" in r["text"]
    assert "status 0x02" in r["text"]
    assert "Safety Limits" in r["text"]
    assert r["rearm_sec"] == NO_WAIT


def test_combined_refusal_lists_both_and_keeps_tec_wait():
    r = describe_start_refusal(
        TRIG_INTERLOCK_TEC | TRIG_INTERLOCK_LASER_SAFETY,
        tec_known=True, tec_good=True, tec_good_since=0.0, now=1.0,
    )
    assert "laser-safety fault latched" in r["text"]
    assert "TEC thermal trip" in r["text"]
    assert r["rearm_sec"] == 4


def test_no_status_byte_points_at_firmware():
    r = describe_start_refusal(None)
    assert "no status byte" in r["text"]
    assert "1.8.2" in r["text"]
    assert r["rearm_sec"] == NO_WAIT


# --- Connector wiring ----------------------------------------------------------

import logging  # noqa: E402

import pytest  # noqa: E402

import motion_connector  # noqa: E402
from motion_connector import MOTIONConnector  # noqa: E402


@pytest.fixture(autouse=True)
def _module_logger(monkeypatch):
    """main.py installs the connector's logger; tests get a plain one."""
    monkeypatch.setattr(motion_connector, "logger", logging.getLogger("test"))


class _Signal:
    def __init__(self):
        self.count = 0

    def emit(self, *args):
        self.count += 1


class _Mutex:
    def lock(self):
        pass

    def unlock(self):
        pass


class _Console:
    def __init__(self, refusal):
        self.last_start_refusal = refusal

    def start_trigger(self):
        return self.last_start_refusal is None


class _Interface:
    def __init__(self, console):
        self.console = console


class _Connector:
    """Carrier for the attributes startTrigger touches, running the real code."""

    startTrigger = MOTIONConnector.startTrigger
    _set_trigger_refusal = MOTIONConnector._set_trigger_refusal
    _describe_start_refusal = MOTIONConnector._describe_start_refusal
    _start_runlog = lambda self: None  # noqa: E731

    def __init__(self, tec_good):
        self._console_mutex = _Mutex()
        self._trigger_refusal_text = ""
        self._trigger_rearm_sec = NO_WAIT
        self.triggerRefusalChanged = _Signal()
        self.triggerStateChanged = _Signal()
        self._console_status_thread = object()  # don't start a real thread
        self._trigger_state = "OFF"
        self._tec_trip_known = False
        self._tec_good = True
        self._tec_voltage = 0.0
        self._tec_trip_value = 23
        self._tec_good_since = None
        self._fresh_tec_good = tec_good
        self.tec_reads = 0

    def tec_status(self):
        self.tec_reads += 1
        self._tec_trip_known = True
        self._tec_good = self._fresh_tec_good
        self._tec_voltage = 25.4


def test_tec_refusal_reads_tec_and_shows_reason(monkeypatch):
    c = _Connector(tec_good=False)
    monkeypatch.setattr(motion_connector, "motion_interface",
                        _Interface(_Console(TRIG_INTERLOCK_TEC)))
    assert c.startTrigger() is False
    assert c.tec_reads == 1
    assert "TEC trip engaged" in c._trigger_refusal_text
    assert "Laser temperature 25.4 °C, TEC Trip 23 °C" in c._trigger_refusal_text
    assert c.triggerRefusalChanged.count == 1


def test_successful_start_clears_the_refusal(monkeypatch):
    c = _Connector(tec_good=True)
    c._trigger_refusal_text = "old refusal"
    monkeypatch.setattr(motion_connector, "motion_interface", _Interface(_Console(None)))
    assert c.startTrigger() is True
    assert c._trigger_refusal_text == ""
    assert c._trigger_state == "ON"


def test_non_tec_refusal_skips_tec_read(monkeypatch):
    c = _Connector(tec_good=True)
    monkeypatch.setattr(motion_connector, "motion_interface",
                        _Interface(_Console(TRIG_INTERLOCK_LASER_SAFETY)))
    assert c.startTrigger() is False
    assert c.tec_reads == 0
    assert "laser-safety fault latched" in c._trigger_refusal_text


class _Config(dict):
    def set(self, key, value):
        self[key] = value


class _RefusingConsole:
    """Console FW 1.8.2 application image: TEC_TRIP is firmware-owned (0xFA)."""

    last_config_refusal_reason = (
        "names a firmware-owned safety parameter; writable only on a Factory image"
    )

    def read_config(self):
        return _Config()

    def write_config(self, config):
        return None


def test_refused_tec_trip_write_names_the_console_reason(monkeypatch):
    """Bench run on console FW 1.8.2: the write is refused, so say why."""
    c = _Connector(tec_good=True)
    c.tecTripSetFailed = _Signal()
    messages = []
    c.tecTripSetFailed.emit = messages.append
    monkeypatch.setattr(motion_connector, "motion_interface", _Interface(_RefusingConsole()))
    assert MOTIONConnector.setTecTrip(c, 23) is False
    assert messages and "TEC_TRIP=23" in messages[0]
    assert "firmware-owned safety parameter" in messages[0]
