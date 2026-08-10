"""The DFU driver preflight blocks only on a positively unusable binding.

The registry is the evidence: Windows records the driver bound to every
0483:DF11 instance it has ever seen. Never-seen must not block (fresh bench
PC, first flash), and any single usable binding clears the machine.
"""

import dfu_driver
from dfu_driver import _evaluate, dfu_driver_issue


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
    result = dfu_driver_issue()
    if dfu_driver.sys.platform == "win32":
        assert "Zadig" in result
    else:
        # Non-Windows short-circuits before reading anything.
        assert result == ""
