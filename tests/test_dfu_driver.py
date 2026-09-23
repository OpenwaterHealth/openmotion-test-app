"""The DFU driver preflight blocks only on a positively unusable binding.

The registry is the evidence: Windows records the driver bound to every
0483:DF11 instance it has ever seen. Never-seen must not block (fresh bench
PC, first flash), any single usable binding clears the machine, and
driverless records are treated as stale once the driver store holds a DF11
INF. Unreadable instances are unknown, never "driverless".
"""

import dfu_driver
from dfu_driver import _UNREADABLE, _evaluate, _read_service, dfu_driver_issue


def test_winusb_binding_clears_the_machine():
    assert _evaluate(["WINUSB"]) == ""


def test_any_usable_instance_is_enough():
    # One good binding means the INF is in the driver store; stale driverless
    # or wrong-driver instances from before it was installed don't matter.
    assert _evaluate([None, "STTub30", "WinUSB"]) == ""


def test_service_match_is_case_insensitive():
    assert _evaluate(["WinUsb"]) == ""
    assert _evaluate(["libusbK"]) == ""


def test_never_seen_does_not_block():
    assert _evaluate(None) == ""
    assert _evaluate([]) == ""


def test_driverless_instance_blocks_with_zadig_guidance():
    issue = _evaluate([None])
    assert "0483:DF11" in issue
    assert "Zadig" in issue
    assert "no driver" in issue
    # The fix must work without a device already in DFU mode.
    assert "Create New Device" in issue


def test_stale_driverless_record_passes_when_store_has_df11_inf():
    # Record left from before the driver was installed: the next DFU entry
    # binds to the INF in the driver store.
    assert _evaluate([None], store_has_inf=True) == ""


def test_unknown_driver_store_does_not_block_driverless_records():
    assert _evaluate([None, None], store_has_inf=None) == ""


def test_wrong_driver_blocks_even_with_df11_inf_in_store():
    # An existing binding wins over a store INF; dfu-util still fails.
    assert "STTub30" in _evaluate(["STTub30"], store_has_inf=True)


class _FakeKey:
    def __init__(self, service_exc=None, service="WinUSB"):
        self.service_exc = service_exc
        self.service = service

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeWinreg:
    def __init__(self, open_exc=None, service_exc=None):
        self.open_exc = open_exc
        self.service_exc = service_exc

    def OpenKey(self, parent, name):
        if self.open_exc:
            raise self.open_exc
        return _FakeKey(self.service_exc)

    def QueryValueEx(self, key, name):
        if key.service_exc:
            raise key.service_exc
        return (key.service, 1)


def test_missing_service_value_means_driverless():
    assert _read_service(_FakeWinreg(service_exc=FileNotFoundError()), None, "x") is None


def test_access_denied_is_unknown_not_driverless():
    assert _read_service(_FakeWinreg(open_exc=PermissionError()), None, "x") is _UNREADABLE
    assert _read_service(_FakeWinreg(service_exc=PermissionError()), None, "x") is _UNREADABLE


def test_bound_service_is_returned():
    assert _read_service(_FakeWinreg(), None, "x") == "WinUSB"


def test_wrong_driver_blocks_and_names_it():
    issue = _evaluate(["STTub30"])
    assert "STTub30" in issue
    assert "Zadig" in issue


def test_mixed_bad_instances_report_the_bound_driver():
    issue = _evaluate([None, "STTub30"])
    assert "STTub30" in issue


def test_registry_failure_never_blocks(monkeypatch):
    def boom():
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(dfu_driver, "_instance_services", boom)
    assert dfu_driver_issue() == ""


def test_issue_uses_recorded_services(monkeypatch):
    monkeypatch.setattr(dfu_driver, "_instance_services", lambda: [None])
    monkeypatch.setattr(dfu_driver, "_driver_store_has_df11", lambda: False)
    result = dfu_driver_issue()
    if dfu_driver.sys.platform == "win32":
        assert "Zadig" in result
    else:
        # Non-Windows short-circuits before reading anything.
        assert result == ""


def test_issue_consults_driver_store_for_driverless_records(monkeypatch):
    monkeypatch.setattr(dfu_driver, "_instance_services", lambda: [None])
    monkeypatch.setattr(dfu_driver, "_driver_store_has_df11", lambda: True)
    assert dfu_driver_issue() == ""
