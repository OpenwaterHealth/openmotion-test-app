"""QML-facing slots for local bootloader installs (#83).

installBootloaderFromLocal must refuse a bad image without starting a thread:
by the time the thread runs, the confirmation dialog has already been accepted.
"""
from unittest.mock import MagicMock

import pytest

import motion_connector as mc
from motion_connector import MOTIONConnector


@pytest.fixture
def connector(monkeypatch):
    # The module logger is None until _configure_logging runs, so the slot's
    # logger.info() would raise AttributeError in a bare unit test.
    monkeypatch.setattr(mc, "logger", MagicMock())
    c = MOTIONConnector.__new__(MOTIONConnector)
    c._console_fw_busy = False
    c._bl_install_thread = None
    c.bootloaderInstallFinished = MagicMock()
    c.consoleFirmwareUpdateBusyChanged = MagicMock()
    c.consoleFirmwareUpdateProgress = MagicMock()
    return c


@pytest.fixture
def production_bin(tmp_path):
    p = tmp_path / "motion-console-production.bin"
    p.write_bytes(b"\x00" * 16)
    return str(p)


def _failure(connector):
    """(target, ok, message) of the single bootloaderInstallFinished emit."""
    connector.bootloaderInstallFinished.emit.assert_called_once()
    return connector.bootloaderInstallFinished.emit.call_args[0]


def test_validate_slot_passes_a_good_image(connector, production_bin):
    assert connector.validateProductionImage("console", production_bin) == ""


def test_validate_slot_rejects_the_wrong_kind(connector, production_bin):
    assert connector.validateProductionImage("left", production_bin) != ""


def test_install_rejects_an_invalid_target(connector, production_bin):
    connector.installBootloaderFromLocal("middle", production_bin)
    target, ok, _ = _failure(connector)
    assert (target, ok) == ("middle", False)
    assert connector._bl_install_thread is None


def test_install_rejects_a_wrong_kind_image_without_starting_a_thread(
    connector, production_bin
):
    connector.installBootloaderFromLocal("left", production_bin)
    _, ok, msg = _failure(connector)
    assert ok is False
    assert "sensor" in msg.lower()
    assert connector._bl_install_thread is None


def test_install_rejects_a_missing_file(connector, tmp_path):
    connector.installBootloaderFromLocal("console", str(tmp_path / "nope.bin"))
    _, ok, _ = _failure(connector)
    assert ok is False
    assert connector._bl_install_thread is None


def test_install_refuses_while_another_operation_is_running(
    connector, production_bin
):
    connector._console_fw_busy = True
    connector.installBootloaderFromLocal("console", production_bin)
    _, ok, msg = _failure(connector)
    assert ok is False
    assert "already in progress" in msg.lower()
    assert connector._bl_install_thread is None
