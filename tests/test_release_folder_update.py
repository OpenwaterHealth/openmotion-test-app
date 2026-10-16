""""Update from release folder", and what each flashing thread asks of the SDK.

Replacing or installing a bootloader is irreversible over USB, so the SDK only
does it when asked with ``acknowledge_irreversible=True``. These pin which of
the app's flashing paths ask, and that a release folder's images are picked and
verified before the operator is shown the confirmation.
"""
import hashlib
import json
import struct
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

import motion_connector as mc
from motion_connector import (
    MOTIONConnector,
    _BootloaderInstallThread,
    _DeviceFirmwareFlashThread,
    _DFU_STRANDED_HINT,
    _ReleaseFolderUpdateThread,
    _release_folder_plan,
)
from omotion import firmware_update


# ---------------------------------------------------------------------------
# A release folder, signed with throwaway keys standing in for the real ones
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

    def signed(self, product, *, legacy=False, fw_version=10802, body=None):
        body = bytes(range(256)) * 4 if body is None else body
        tag = hashlib.sha256(body).digest()
        header = struct.pack("<4sHHIII32s32s16s28s", b"SFU1", 1, fw_version, len(body),
                             0, 0, tag, tag, b"\x00" * 16, b"\x00" * 28)
        r, s = decode_dss_signature(
            self._keys[(product, legacy)].sign(header, ec.ECDSA(hashes.SHA256())))
        head = header + r.to_bytes(32, "big") + s.to_bytes(32, "big")
        return head + b"\xff" * (0x400 - len(head)) + body


def _production(signed: bytes) -> tuple[bytes, bytes]:
    """(production image, its bootloader part) around ``signed``."""
    bootloader = struct.pack("<II", 0x24080000, 0x08000401) + b"\xb1" * 1024
    return bootloader + b"\xff" * (0x20000 - len(bootloader)) + signed, bootloader


@pytest.fixture(scope="module")
def keys():
    return _Keys()


@pytest.fixture
def release(tmp_path, keys, monkeypatch):
    """A complete release folder for both products; no debug probe on USB."""
    monkeypatch.setattr(firmware_update, "public_key_pem", keys.pem)
    monkeypatch.setattr(mc, "stlink_probes", lambda: 0)
    for product in ("console", "sensor"):
        signed = keys.signed(product)
        production, bootloader = _production(signed)
        updater = keys.signed(product, legacy=True, fw_version=10897,
                              body=b"\x00" * 64 + bootloader + b"\x00" * 64)
        (tmp_path / f"openmotion-{product}-fw-signed.bin").write_bytes(signed)
        (tmp_path / f"openmotion-{product}-fw-production.bin").write_bytes(production)
        (tmp_path / f"open-motion-{product}-bl-updater.bin").write_bytes(updater)
    return tmp_path


# ---------------------------------------------------------------------------
# Picking and vetting the folder
# ---------------------------------------------------------------------------

def test_a_complete_folder_yields_the_devices_three_images(release):
    plan = _release_folder_plan("right", str(release))

    assert plan["error"] == ""
    assert {role: Path(p).name for role, p in plan["images"].items()} == {
        "app": "openmotion-sensor-fw-signed.bin",
        "updater": "open-motion-sensor-bl-updater.bin",
        "production": "openmotion-sensor-fw-production.bin",
    }


def test_the_summary_names_each_file_and_what_it_is(release):
    summary = _release_folder_plan("console", str(release))["summary"]

    assert "openmotion-console-fw-signed.bin" in summary and "1.8.2" in summary
    assert "open-motion-console-bl-updater.bin" in summary and "1.8.97" in summary
    assert "openmotion-console-fw-production.bin" in summary


def test_a_folder_without_the_optional_images_says_what_cannot_be_done(release):
    (release / "open-motion-console-bl-updater.bin").unlink()
    (release / "openmotion-console-fw-production.bin").unlink()

    plan = _release_folder_plan("console", str(release))

    assert plan["error"] == "" and set(plan["images"]) == {"app"}
    assert "older than 1.2.0 cannot be updated" in plan["summary"]
    assert "no bootloader cannot be updated" in plan["summary"]


def test_a_folder_without_an_application_is_refused(release):
    (release / "openmotion-console-fw-signed.bin").unlink()
    assert "No signed console application" in _release_folder_plan("console", str(release))["error"]


def test_a_damaged_image_is_refused_before_the_confirmation(release):
    path = release / "openmotion-sensor-fw-signed.bin"
    data = bytearray(path.read_bytes())
    data[0x400 + 10] ^= 0x01
    path.write_bytes(bytes(data))

    assert "FwTag" in _release_folder_plan("left", str(release))["error"]


def test_an_image_signed_for_the_other_device_is_refused(release):
    """A sensor image dropped into the folder under a console name."""
    (release / "openmotion-console-fw-signed.bin").write_bytes(
        (release / "openmotion-sensor-fw-signed.bin").read_bytes())

    assert "sensor bootloader" in _release_folder_plan("console", str(release))["error"]


def test_two_updaters_in_the_folder_are_refused(release):
    (release / "open-motion-console-bl-updater-old.bin").write_bytes(b"\x00")
    assert "2 console updater images" in _release_folder_plan("console", str(release))["error"]


def test_a_debug_probe_on_usb_is_refused(release, monkeypatch):
    monkeypatch.setattr(mc, "stlink_probes", lambda: 1)
    assert "ST-LINK" in _release_folder_plan("console", str(release))["error"]


def test_a_missing_folder_and_an_invalid_target_are_refused(release, tmp_path):
    assert _release_folder_plan("console", str(tmp_path / "nope"))["error"] != ""
    assert _release_folder_plan("middle", str(release))["error"] != ""


def test_an_sdk_without_release_folder_support_is_reported(release, monkeypatch):
    monkeypatch.setattr(mc, "find_release_images", None)
    assert "unavailable" in _release_folder_plan("console", str(release))["error"]


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


def test_describe_slot_hands_qml_the_error_and_summary_as_json(connector, release):
    good = json.loads(connector.describeReleaseFolder("console", str(release)))
    assert good["error"] == "" and "openmotion-console-fw-signed.bin" in good["summary"]

    bad = json.loads(connector.describeReleaseFolder("console", str(release / "nope")))
    assert bad["error"] != "" and bad["summary"] == ""


def test_update_refuses_a_bad_folder_without_starting_a_thread(connector, release):
    connector.updateFromReleaseFolder("console", str(release / "nope"))
    connector.bootloaderInstallFinished.emit.assert_called_once()
    _, ok, _ = connector.bootloaderInstallFinished.emit.call_args[0]
    assert ok is False
    assert connector._bl_install_thread is None


def test_update_refuses_an_invalid_target(connector, release):
    connector.updateFromReleaseFolder("middle", str(release))
    target, ok, _ = connector.bootloaderInstallFinished.emit.call_args[0]
    assert (target, ok) == ("middle", False)


def test_update_refuses_while_another_operation_is_running(connector, release):
    connector._console_fw_busy = True
    connector.updateFromReleaseFolder("console", str(release))
    _, ok, msg = connector.bootloaderInstallFinished.emit.call_args[0]
    assert ok is False and "already in progress" in msg.lower()


def test_update_starts_a_thread_with_the_images_it_vetted(connector, release, monkeypatch):
    monkeypatch.setattr(connector, "_run_bootloader_thread", MagicMock())
    connector.updateFromReleaseFolder("left", str(release))

    target, make_thread = connector._run_bootloader_thread.call_args[0]
    assert target == "left"
    connector.bootloaderInstallFinished.emit.assert_not_called()
    # Nothing is built until the driver preflight has passed.
    monkeypatch.setattr(_ReleaseFolderUpdateThread, "__init__",
                        lambda self, *a: setattr(self, "args", a))
    conn, tgt, images = make_thread().args
    assert (conn, tgt) == (connector, "left")
    assert Path(images["app"]).name == "openmotion-sensor-fw-signed.bin"


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
    needs_power_cycle = False

    def __init__(self, **kwargs):
        self.last_boot_mode = None

    def update(self, handle, bin_path, progress_cb=None, **kwargs):
        _FakeUpdater.calls.append((handle, bin_path, kwargs))
        if _FakeUpdater.error is not None:
            raise _FakeUpdater.error
        return MagicMock(needs_power_cycle=_FakeUpdater.needs_power_cycle,
                         message="application installed")


@pytest.fixture
def sdk(monkeypatch):
    _FakeUpdater.calls = []
    _FakeUpdater.error = None
    _FakeUpdater.needs_power_cycle = False
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


def test_ordinary_update_never_authorises_a_bootloader_change(sdk, release):
    app = str(release / "openmotion-console-fw-signed.bin")
    t = _bare(_DeviceFirmwareFlashThread, _bin_path=app, _target="console")
    t.run()

    (handle, bin_path, kwargs), = sdk.calls
    assert handle == "handle:console" and str(bin_path) == app
    assert kwargs["kind"] is mc.FirmwareKind.CONSOLE
    assert "acknowledge_irreversible" not in kwargs
    assert "updater_image" not in kwargs and "production_image" not in kwargs
    t.finished_ok.emit.assert_called_once()


def test_ordinary_update_reports_a_refusal_with_the_power_cycle_hint(sdk, release):
    sdk.error = RuntimeError("this unit runs bootloader 1.0.0, which does not accept it.")
    t = _bare(_DeviceFirmwareFlashThread,
              _bin_path=str(release / "openmotion-sensor-fw-signed.bin"), _target="left")
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


def test_folder_update_passes_everything_with_the_acknowledgement(sdk, release):
    """One call that serves any starting state, as scripts/update_firmware.py does."""
    images = _release_folder_plan("right", str(release))["images"]
    t = _bare(_ReleaseFolderUpdateThread, _target="right", _images=images, result_message="")
    t.run()

    (handle, bin_path, kwargs), = sdk.calls
    assert handle == "handle:right"
    assert bin_path == release / "openmotion-sensor-fw-signed.bin"
    assert kwargs["updater_image"] == release / "open-motion-sensor-bl-updater.bin"
    assert kwargs["production_image"] == release / "openmotion-sensor-fw-production.bin"
    assert kwargs["acknowledge_irreversible"] is True
    assert kwargs["kind"] is mc.FirmwareKind.SENSOR
    assert t.result_message == "Application installed."


def test_folder_update_without_optional_images_passes_none(sdk, release):
    t = _bare(_ReleaseFolderUpdateThread, _target="console",
              _images={"app": str(release / "openmotion-console-fw-signed.bin")},
              result_message="")
    t.run()

    (_, _, kwargs), = sdk.calls
    assert kwargs["updater_image"] is None and kwargs["production_image"] is None


def test_folder_update_tells_the_operator_to_power_cycle_when_needed(sdk, release):
    sdk.needs_power_cycle = True
    images = _release_folder_plan("left", str(release))["images"]
    t = _bare(_ReleaseFolderUpdateThread, _target="left", _images=images, result_message="")
    t.run()
    assert "Power-cycle" in t.result_message


def test_folder_update_failure_carries_the_power_cycle_hint(sdk, release):
    sdk.error = RuntimeError("the old bootloader refused the updater")
    images = _release_folder_plan("console", str(release))["images"]
    t = _bare(_ReleaseFolderUpdateThread, _target="console", _images=images, result_message="")
    t.run()

    (msg,), _ = t.failed.emit.call_args
    assert "refused the updater" in msg and _DFU_STRANDED_HINT in msg
def test_ordinary_update_reports_what_the_sdk_did(sdk, release):
    t = _bare(_DeviceFirmwareFlashThread,
              _bin_path=str(release / "openmotion-console-fw-signed.bin"), _target="console",
              result_message="")
    t.run()
    assert t.result_message == "Application installed."
    t.finished_ok.emit.assert_called_once()


def test_ordinary_sensor_update_tells_the_operator_to_power_cycle_when_needed(sdk, release):
    sdk.needs_power_cycle = True
    t = _bare(_DeviceFirmwareFlashThread,
              _bin_path=str(release / "openmotion-sensor-fw-signed.bin"), _target="right",
              result_message="")
    t.run()
    # The SDK's message (here the fake's) is kept, and the sensor-specific hint
    # follows it: the operator must know the unit is sitting dead on USB.
    assert t.result_message.startswith("Application installed.")
    assert "console power cycle restarts both sensors" in t.result_message
    t.finished_ok.emit.assert_called_once()


class _WaitRecordingUpdater(_FakeUpdater):
    """Records the application wait each thread leaves on its updater."""
    waits = []

    def update(self, handle, bin_path, progress_cb=None, **kwargs):
        _WaitRecordingUpdater.waits.append(getattr(self, "app_wait_timeout_s", None))
        return super().update(handle, bin_path, progress_cb, **kwargs)


def test_ordinary_update_asks_for_a_power_cycle_after_45_s(sdk, release, monkeypatch):
    monkeypatch.setattr(mc, "FirmwareUpdater", _WaitRecordingUpdater)
    _WaitRecordingUpdater.waits = []
    t = _bare(_DeviceFirmwareFlashThread,
              _bin_path=str(release / "openmotion-sensor-fw-signed.bin"), _target="right",
              result_message="")
    t.run()
    assert _WaitRecordingUpdater.waits == [45.0]


def test_folder_update_keeps_the_sdk_application_wait(sdk, release, monkeypatch):
    # It can include the bootloader's first boot: no shorter wait there.
    monkeypatch.setattr(mc, "FirmwareUpdater", _WaitRecordingUpdater)
    _WaitRecordingUpdater.waits = []
    images = _release_folder_plan("left", str(release))["images"]
    t = _bare(_ReleaseFolderUpdateThread, _target="left", _images=images, result_message="")
    t.run()
    assert _WaitRecordingUpdater.waits == [None]
