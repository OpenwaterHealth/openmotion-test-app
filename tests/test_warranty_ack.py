"""Unit tests for utils.warranty_ack.WarrantyAck.

Run from the repo root with:  python -m pytest tests/test_warranty_ack.py
(`python -m pytest` puts the repo root on sys.path so `import utils.*` works.)

Every test injects a QSettings backed by a file under tmp_path, so the
developer's real registry (HKCU\\Software\\Openwater\\...) is never touched.
"""
import subprocess
import sys
from pathlib import Path

import pytest
from PyQt6.QtCore import QSettings

from utils.warranty_ack import SETTINGS_KEY, WarrantyAck

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def ini_path(tmp_path):
    return str(tmp_path / "settings.ini")


def _settings(path):
    """A fresh QSettings over the given INI file."""
    return QSettings(path, QSettings.Format.IniFormat)


def test_defaults_to_not_accepted(ini_path):
    assert WarrantyAck(_settings(ini_path)).accepted is False


def test_accept_persists_to_disk(ini_path):
    ack = WarrantyAck(_settings(ini_path))
    ack.accept()

    assert Path(ini_path).exists()
    assert "accepted=true" in Path(ini_path).read_text()


def test_accept_is_visible_to_a_fresh_instance(ini_path):
    WarrantyAck(_settings(ini_path)).accept()

    assert WarrantyAck(_settings(ini_path)).accepted is True


def test_accept_emits_changed_once_and_is_idempotent(ini_path):
    ack = WarrantyAck(_settings(ini_path))
    emissions = []
    ack.acceptedChanged.connect(lambda: emissions.append(1))

    ack.accept()
    assert ack.accepted is True
    assert len(emissions) == 1

    ack.accept()
    assert len(emissions) == 1, "second accept() must not re-emit"


def test_corrupt_stored_value_fails_closed(ini_path):
    # Qt's QVariant conversion would turn this into True with type=bool,
    # which would skip the warning entirely. It must read as False.
    seeded = _settings(ini_path)
    seeded.setValue(SETTINGS_KEY, "garbage")
    seeded.sync()

    assert WarrantyAck(_settings(ini_path)).accepted is False


def test_registry_style_string_true_is_accepted(ini_path):
    # The Windows registry backend hands back the string "true", not a bool.
    seeded = _settings(ini_path)
    seeded.setValue(SETTINGS_KEY, "true")
    seeded.sync()

    assert WarrantyAck(_settings(ini_path)).accepted is True


def test_unwritable_store_does_not_raise(tmp_path):
    # Point QSettings at a path that is a directory: writes fail with
    # AccessError. accept() must swallow that.
    unwritable = tmp_path / "a_directory"
    unwritable.mkdir()
    ack = WarrantyAck(_settings(str(unwritable)))

    ack.accept()

    # The user did accept, so this session proceeds; nothing was persisted,
    # so the next launch will prompt again.
    assert ack.accepted is True

    # Nothing was persisted, so the next launch re-prompts. This must be
    # checked in a genuinely separate process: Qt caches ini-file content in
    # a process-wide registry keyed by canonical path, so a second QSettings
    # for this same path *within this test process* reads back the in-memory
    # value that never reached disk, regardless of what WarrantyAck does --
    # a false pass. Measured on PyQt6 6.8 / Windows, 2026-07-20. A subprocess
    # has no such cache and faithfully represents a real next launch.
    checker = (
        "from PyQt6.QtCore import QSettings\n"
        "from utils.warranty_ack import WarrantyAck\n"
        f"s = QSettings({str(unwritable)!r}, QSettings.Format.IniFormat)\n"
        "print(WarrantyAck(s).accepted)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", checker],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"
