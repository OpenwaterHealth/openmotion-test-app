"""Regression tests for the per-slot boot-mode cache lifecycle.

Bug (#77): _boot_modes is keyed by slot ("console"/"left"/"right"), not device
identity, and was never cleared on disconnect. Swapping a converted device for
an old/bare-metal one on the same slot left the old device showing the previous
device's "Bootloader" (locked) state, because the old firmware answers UNKNOWN
and the query path never overwrites a definite cached value.

The fix clears the slot on disconnect. These exercise the cache methods in
isolation with the Qt signal stubbed.
"""
from unittest.mock import MagicMock

import pytest

from omotion.boot_mode import BootMode
from motion_connector import MOTIONConnector


def _connector():
    c = MOTIONConnector.__new__(MOTIONConnector)
    c._boot_modes = {}
    c.deviceBootModeChanged = MagicMock()
    return c


def test_disconnect_clears_stale_bootloader_so_swapped_device_is_not_locked():
    c = _connector()
    # A converted device was seen on this slot.
    c._note_boot_mode("console", BootMode.BOOTLOADER)
    assert c.deviceBootMode("console") == "Bootloader"

    # It is unplugged: the slot's cached mode must be forgotten.
    c._clear_boot_mode("console")
    assert c.deviceBootMode("console") == ""

    # An old device (no boot-info command) reconnects. The query path skips
    # UNKNOWN, so the slot stays empty -> the icon reads unlocked, not the
    # previous device's locked state.
    assert c.deviceBootMode("console") == ""


def test_clear_emits_change_when_something_was_cached():
    c = _connector()
    c._note_boot_mode("right", BootMode.BOOTLOADER)
    c.deviceBootModeChanged.reset_mock()
    c._clear_boot_mode("right")
    c.deviceBootModeChanged.emit.assert_called_once_with("right", "")


def test_clear_is_a_noop_when_nothing_cached():
    c = _connector()
    c._clear_boot_mode("left")
    assert c.deviceBootMode("left") == ""
    c.deviceBootModeChanged.emit.assert_not_called()


def test_definite_mode_survives_while_connected():
    """The guard that keeps a transient UNKNOWN from clobbering a known state is
    still wanted; only disconnect clears. Recording the same mode twice is a
    no-op (no redundant signal)."""
    c = _connector()
    c._note_boot_mode("console", BootMode.BOOTLOADER)
    c.deviceBootModeChanged.reset_mock()
    c._note_boot_mode("console", BootMode.BOOTLOADER)
    c.deviceBootModeChanged.emit.assert_not_called()
    assert c.deviceBootMode("console") == "Bootloader"
