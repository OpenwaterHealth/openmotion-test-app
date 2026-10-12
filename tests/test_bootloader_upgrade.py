"""Bootloader upgrade (old bootloader -> 1.2.0) and what each flashing thread
asks of the SDK.

Replacing or installing a bootloader is irreversible over USB, so the SDK only
does it when asked with ``acknowledge_irreversible=True``. These pin which of
the app's three flashing paths ask, and that the upgrade's two files are vetted
before the operator is shown the confirmation.
"""
import hashlib
import struct
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

import motion_connector as mc
from motion_connector import (
    MOTIONConnector,
    _BootloaderInstallThread,
    _BootloaderUpgradeThread,
    _DeviceFirmwareFlashThread,
    _DFU_STRANDED_HINT,
    _validate_bootloader_upgrade,
)


# ---------------------------------------------------------------------------
# Signed images, with throwaway keys standing in for the bootloader keys
# ---------------------------------------------------------------------------

class _Keys:
    def __init__(self):
        self._keys = {
            (product, legacy): ec.generate_private_key(ec.SECP256R1())
            for product in ("console", "sensor") for legacy in (False, True)
        }

    def pem(self, kind, *, legacy=False):
        key = self._keys[(getattr(kind, "value", kind), bool(legacy))]
        return key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )

    def signed(self, product, *, legacy=False, fw_version=11300):
        body = bytes(range(256)) * 4
        tag = hashlib.sha256(body).digest()
        header = struct.pack("<4sHHIII32s32s16s28s", b"SFU1", 1, fw_version, len(body),
                             0, 0, tag, tag, b"\x00" * 16, b"\x00" * 28)
        r, s = decode_dss_signature(
            self._keys[(product, legacy)].sign(header, ec.ECDSA(hashes.SHA256())))
        head = header + r.to_bytes(32, "big") + s.to_bytes(32, "big")
        return head + b"\xff" * (0x400 - len(head)) + body


@pytest.fixture(scope="module")
def keys():
    return _Keys()


@pytest.fixture
def files(tmp_path, keys, monkeypatch):
    """A console updater (old key) and a console application (new key)."""
    monkeypatch.setattr(mc, "public_key_pem", keys.pem)
    updater = tmp_path / "console-bl-updater-signed.bin"
    updater.write_bytes(keys.signed("console", legacy=True, fw_version=10898))
    app = tmp_path / "motion-console-fw-signed.bin"
    app.write_bytes(keys.signed("console"))
    sensor_app = tmp_path / "motion-sensor-fw-signed.bin"
    sensor_app.write_bytes(keys.signed("sensor"))
    return {"updater": str(updater), "app": str(app), "sensor_app": str(sensor_app)}


# ---------------------------------------------------------------------------
# Vetting the two files
# ---------------------------------------------------------------------------

def test_a_matching_updater_and_application_pass(files):
    assert _validate_bootloader_upgrade("console", files["updater"], files["app"]) == ""


def test_swapped_files_are_rejected(files):
    """Each file is signed for the other bootloader generation."""
    err = _validate_bootloader_upgrade("console", files["app"], files["updater"])
    assert "bootloader updater" in err


def test_an_application_for_the_other_device_is_rejected(files):
    err = _validate_bootloader_upgrade("console", files["updater"], files["sensor_app"])
    assert "motion-sensor-fw-signed.bin" in err
    assert "console application" in err


def test_console_files_are_rejected_for_a_sensor(files):
    assert _validate_bootloader_upgrade("left", files["updater"], files["app"]) != ""


def test_a_missing_file_is_rejected(files, tmp_path):
    err = _validate_bootloader_upgrade("console", str(tmp_path / "nope.bin"), files["app"])
    assert "does not exist" in err


def test_an_invalid_target_is_rejected(files):
    assert _validate_bootloader_upgrade("middle", files["updater"], files["app"]) != ""


def test_an_sdk_without_image_verification_is_reported(files, monkeypatch):
    monkeypatch.setattr(mc, "verify_image", None)
    assert "unavailable" in _validate_bootloader_upgrade(
        "console", files["updater"], files["app"])


# ---------------------------------------------------------------------------
# The QML-facing slots
# ---------------------------------------------------------------------------

@pytest.fixture
def connector(monkeypatch):
    monkeypatch.setattr(mc, "logger", MagicMock())
    c = MOTIONConnector.__new__(MOTIONConnector)
    c._console_fw_busy = False
    c._bl_install_thread = None
    c.bootloaderInstallFinished = MagicMock()
    c.consoleFirmwareUpdateBusyChanged = MagicMock()
    c.consoleFirmwareUpdateProgress = MagicMock()
    return c


def test_validate_slot_reports_to_qml(connector, files):
    assert connector.validateBootloaderUpgrade("console", files["updater"], files["app"]) == ""
    assert connector.validateBootloaderUpgrade("console", files["app"], files["app"]) != ""


def test_upgrade_refuses_bad_files_without_starting_a_thread(connector, files):
    connector.upgradeBootloaderFromLocal("console", files["app"], files["updater"])
    connector.bootloaderInstallFinished.emit.assert_called_once()
    _, ok, _ = connector.bootloaderInstallFinished.emit.call_args[0]
    assert ok is False
    assert connector._bl_install_thread is None


def test_upgrade_refuses_an_invalid_target(connector, files):
    connector.upgradeBootloaderFromLocal("middle", files["updater"], files["app"])
    target, ok, _ = connector.bootloaderInstallFinished.emit.call_args[0]
    assert (target, ok) == ("middle", False)
    assert connector._bl_install_thread is None


def test_upgrade_refuses_while_another_operation_is_running(connector, files):
    connector._console_fw_busy = True
    connector.upgradeBootloaderFromLocal("console", files["updater"], files["app"])
    _, ok, msg = connector.bootloaderInstallFinished.emit.call_args[0]
    assert ok is False and "already in progress" in msg.lower()


def test_upgrade_starts_an_upgrade_thread_for_good_files(connector, files, monkeypatch):
    monkeypatch.setattr(connector, "_run_bootloader_thread", MagicMock())
    connector.upgradeBootloaderFromLocal("console", files["updater"], files["app"])
    target, make_thread = connector._run_bootloader_thread.call_args[0]
    assert target == "console"
    connector.bootloaderInstallFinished.emit.assert_not_called()
    # Nothing is built until the driver preflight has passed.
    monkeypatch.setattr(_BootloaderUpgradeThread, "__init__", lambda self, *a: setattr(self, "args", a))
    assert make_thread().args == (connector, "console", files["updater"], files["app"])


def test_driver_preflight_failure_starts_nothing(connector, monkeypatch):
    monkeypatch.setattr(mc, "dfu_driver_issue", lambda: "no WinUSB on 0483:DF11")
    make_thread = MagicMock()
    connector._run_bootloader_thread("console", make_thread)
    make_thread.assert_not_called()
    _, ok, msg = connector.bootloaderInstallFinished.emit.call_args[0]
    assert ok is False and "WinUSB" in msg
    assert connector._console_fw_busy is False


# ---------------------------------------------------------------------------
# What each thread asks of the SDK
# ---------------------------------------------------------------------------

class _FakeUpdater:
    """Stands in for omotion.FirmwareUpdater; records the update() call."""

    calls = []
    error = None

    def __init__(self, **kwargs):
        self.last_boot_mode = None

    def update(self, handle, bin_path, progress_cb=None, **kwargs):
        _FakeUpdater.calls.append((handle, bin_path, kwargs))
        if _FakeUpdater.error is not None:
            raise _FakeUpdater.error
        return MagicMock(needs_power_cycle=False, message="application installed")


@pytest.fixture
def sdk(monkeypatch):
    _FakeUpdater.calls = []
    _FakeUpdater.error = None
    monkeypatch.setattr(mc, "FirmwareUpdater", _FakeUpdater)
    monkeypatch.setattr(mc, "_MutexedDfuHandle", lambda connector, target: f"handle:{target}")
    return _FakeUpdater


def _bare(cls, **attrs):
    """Build a thread without running QThread.__init__."""
    t = cls.__new__(cls)
    t._connector = MagicMock()
    t.progress = MagicMock()
    t.failed = MagicMock()
    t.finished_ok = MagicMock()
    for name, value in attrs.items():
        setattr(t, name, value)
    return t


def test_ordinary_update_never_authorises_a_bootloader_change(sdk, files):
    t = _bare(_DeviceFirmwareFlashThread, _bin_path=files["app"], _target="console")
    t.run()

    (handle, bin_path, kwargs), = sdk.calls
    assert handle == "handle:console" and str(bin_path) == files["app"]
    assert kwargs["kind"] is mc.FirmwareKind.CONSOLE
    assert "acknowledge_irreversible" not in kwargs
    assert "updater_image" not in kwargs and "production_image" not in kwargs
    t.finished_ok.emit.assert_called_once()


def test_ordinary_update_reports_a_refusal_with_the_power_cycle_hint(sdk, files):
    sdk.error = RuntimeError("this unit runs bootloader 1.0.0, which does not accept it.")
    t = _bare(_DeviceFirmwareFlashThread, _bin_path=files["app"], _target="left")
    t.run()

    t.finished_ok.emit.assert_not_called()
    (msg,), _ = t.failed.emit.call_args
    assert "1.0.0" in msg and _DFU_STRANDED_HINT in msg


def test_install_passes_the_production_image_with_the_acknowledgement(sdk, tmp_path):
    production = tmp_path / "motion-console-production.bin"
    production.write_bytes(b"\x00" * 16)
    t = _bare(_BootloaderInstallThread, _target="console", _tag="local",
              _local_path=str(production), result_message="")
    t.run()

    (_, bin_path, kwargs), = sdk.calls
    assert bin_path == production
    assert kwargs["production_image"] == production
    assert kwargs["acknowledge_irreversible"] is True
    assert kwargs["kind"] is mc.FirmwareKind.CONSOLE
    assert "updater_image" not in kwargs
    t.finished_ok.emit.assert_called_once()
    assert t.result_message.startswith("Bootloader installed.")


def test_upgrade_passes_the_updater_and_application_with_the_acknowledgement(sdk, files):
    t = _bare(_BootloaderUpgradeThread, _target="right", _updater_path=files["updater"],
              _app_path=files["app"], result_message="")
    t.run()

    (handle, bin_path, kwargs), = sdk.calls
    assert handle == "handle:right" and str(bin_path) == files["app"]
    assert str(kwargs["updater_image"]) == files["updater"]
    assert kwargs["acknowledge_irreversible"] is True
    assert kwargs["kind"] is mc.FirmwareKind.SENSOR
    assert "production_image" not in kwargs
    assert t.result_message == "Application installed."


def test_upgrade_failure_carries_the_power_cycle_hint(sdk, files):
    sdk.error = RuntimeError("the old bootloader refused the updater")
    t = _bare(_BootloaderUpgradeThread, _target="console", _updater_path=files["updater"],
              _app_path=files["app"], result_message="")
    t.run()

    (msg,), _ = t.failed.emit.call_args
    assert "refused the updater" in msg and _DFU_STRANDED_HINT in msg
