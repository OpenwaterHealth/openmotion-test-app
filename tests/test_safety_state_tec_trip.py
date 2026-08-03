"""Unit tests for MOTIONConnector._apply_safety_state (issue #89).

Run from the repo root with:
    python -m pytest tests/test_safety_state_tec_trip.py

The Failure indicator is fed by two independent sources: the EE/OPT laser-safety
interlock in the safety FPGAs, and the console's TEC over-temp trip. Console FW
``tec_trip_evaluate()`` opens the safety disconnect on an over-temp and clears
``TecStats.tec_status``; before #89 the GUI ignored that bit entirely, so a TEC
shutdown stopped the laser with no on-screen reason.

The reconciliation logic is exercised against a stub ``self`` rather than a real
MOTIONConnector, which would need a live Qt object and an SDK interface. The
methods under test are the real ones, bound to the stub.
"""
from motion_connector import MOTIONConnector


class RecordingSignal:
    """Stand-in for a pyqtSignal that records what it emitted."""

    def __init__(self):
        self.emissions = []

    def emit(self, value):
        self.emissions.append(value)


class FakeConnector:
    """Minimal carrier for the safety-state attributes, running the real logic."""

    _TEC_TRIP_LABEL = MOTIONConnector._TEC_TRIP_LABEL
    _tec_trip_state = MOTIONConnector._tec_trip_state
    _apply_safety_state = MOTIONConnector._apply_safety_state

    def __init__(self, tec_known=True, tec_good=True):
        self._tec_trip_known = tec_known
        self._tec_good = tec_good
        self._safety_ee_opt_text = ""
        self._safety_ee_opt_tripped = False
        self._safetyFaultText = ""
        self._safetyFailure = False
        self._trigger_state = "ON"
        self.safetyFaultTextChanged = RecordingSignal()
        self.safetyFailureStateChanged = RecordingSignal()
        self.triggerStateChanged = RecordingSignal()
        self.stop_trigger_calls = 0

    def stopTrigger(self):
        self.stop_trigger_calls += 1


CLEAR = dict(known=True, ok=True, fault_text="")
EE_FAULT = dict(known=True, ok=False, fault_text="EE:PEAK_CURRENT")
UNKNOWN = dict(known=False, ok=True, fault_text="")


def test_tec_trip_raises_failure_and_names_the_fault():
    c = FakeConnector(tec_good=False)
    c._apply_safety_state(**CLEAR)
    assert c._safetyFailure is True
    assert c._safetyFaultText == "TEC_TRIP"
    assert c.safetyFailureStateChanged.emissions == [True]


def test_tec_trip_tears_down_the_trigger():
    """Same belt-and-suspenders teardown an EE/OPT trip gets, so the Laser and
    Failure dots can't land in opposite states (#56)."""
    c = FakeConnector(tec_good=False)
    c._apply_safety_state(**CLEAR)
    assert c.stop_trigger_calls == 1
    assert c._trigger_state == "OFF"
    assert c.triggerStateChanged.emissions == ["OFF"]


def test_tec_trip_clearing_returns_to_ok():
    """Firmware re-arms after 200 clean polls; the indicator must follow."""
    c = FakeConnector(tec_good=False)
    c._apply_safety_state(**CLEAR)
    c._tec_good = True
    c._apply_safety_state(**CLEAR)
    assert c._safetyFailure is False
    assert c._safetyFaultText == ""
    assert c.safetyFailureStateChanged.emissions == [True, False]


def test_simultaneous_ee_and_tec_trip_lists_both():
    c = FakeConnector(tec_good=False)
    c._apply_safety_state(**EE_FAULT)
    assert c._safetyFaultText == "EE:PEAK_CURRENT, TEC_TRIP"
    assert c._safetyFailure is True


def test_tec_trip_label_is_not_appended_twice_across_polls():
    """The composed text is rebuilt from the two stored halves each poll; if the
    TEC label were appended to the previous composed text it would accumulate."""
    c = FakeConnector(tec_good=False)
    for _ in range(3):
        c._apply_safety_state(**EE_FAULT)
    assert c._safetyFaultText == "EE:PEAK_CURRENT, TEC_TRIP"
    # Text emitted once, not once per poll.
    assert c.safetyFaultTextChanged.emissions == ["EE:PEAK_CURRENT, TEC_TRIP"]


def test_clearing_ee_fault_keeps_failure_while_tec_still_tripped():
    """resetSafety clears the EE/OPT latch, but there is no clear command for the
    TEC trip — it must hold the indicator red on its own."""
    c = FakeConnector(tec_good=False)
    c._apply_safety_state(**EE_FAULT)
    c._apply_safety_state(**CLEAR)  # operator cleared EE/OPT
    assert c._safetyFailure is True
    assert c._safetyFaultText == "TEC_TRIP"


def test_unread_tec_never_reports_a_trip():
    """_tec_good initialises False, which is indistinguishable from tripped. Until
    a read succeeds the TEC source must stay silent."""
    c = FakeConnector(tec_known=False, tec_good=False)
    c._apply_safety_state(**CLEAR)
    assert c._safetyFailure is False
    assert c._safetyFaultText == ""


def test_no_fresh_data_from_either_source_leaves_indicators_alone():
    c = FakeConnector(tec_known=False, tec_good=False)
    c._safetyFailure = True
    c._safetyFaultText = "EE:PEAK_CURRENT"
    c._apply_safety_state(**UNKNOWN)
    assert c._safetyFailure is True
    assert c._safetyFaultText == "EE:PEAK_CURRENT"
    assert c.safetyFailureStateChanged.emissions == []


def test_tec_trip_surfaces_even_when_interlock_does_not_answer():
    """A silent EE/OPT read must not suppress a TEC trip."""
    c = FakeConnector(tec_good=False)
    c._apply_safety_state(**UNKNOWN)
    assert c._safetyFailure is True
    assert c._safetyFaultText == "TEC_TRIP"


def test_unreadable_interlock_does_not_clear_a_latched_ee_trip():
    """The EE/OPT verdict is held across polls where the interlock is silent, so
    a dropped read can't silently un-trip a latched fault."""
    c = FakeConnector(tec_good=True)
    c._apply_safety_state(**EE_FAULT)
    assert c._safetyFailure is True
    c._apply_safety_state(**UNKNOWN)
    assert c._safetyFailure is True
    assert c._safetyFaultText == "EE:PEAK_CURRENT"


def test_ee_only_trip_is_unchanged_by_the_tec_wiring():
    """Regression guard: the pre-#89 EE/OPT path must behave as before."""
    c = FakeConnector(tec_good=True)
    c._apply_safety_state(**EE_FAULT)
    assert c._safetyFailure is True
    assert c._safetyFaultText == "EE:PEAK_CURRENT"
    assert c.stop_trigger_calls == 1
    c._apply_safety_state(**CLEAR)
    assert c._safetyFailure is False
    assert c._safetyFaultText == ""
