"""Image resolution for bootloader installs (#83).

A local install must not touch the network -- the factory floor is offline.
These pin the two guards that used to run before any image resolution and so
would have blocked the local path: the GitHubReleases availability check and
the --no-github bail.

Also covers the power-cycle hint. install_bootloader enters DFU before it can
detect that a device is already converted, and only flash_bin passes :leave
(omotion/DFUProgrammer.py), so an aborted install strands the device in DFU.
"""
from unittest.mock import MagicMock

import pytest

import motion_connector as mc
from motion_connector import (
    _BootloaderInstallThread,
    _BootloaderSourceError,
    _DFU_STRANDED_HINT,
    _with_dfu_hint,
)


def _thread(tmp_path, *, local_name=None, github_disabled=False):
    """Build the thread without running QThread.__init__."""
    t = _BootloaderInstallThread.__new__(_BootloaderInstallThread)
    t._connector = MagicMock()
    t._connector._github_disabled = github_disabled
    t._target = "console"
    t._tag = "1.8.3"
    t._local_path = None
    if local_name is not None:
        p = tmp_path / local_name
        p.write_bytes(b"\x00" * 16)
        t._local_path = str(p)
    t.progress = MagicMock()
    return t


def test_local_path_resolves_with_github_disabled(tmp_path):
    t = _thread(tmp_path, local_name="motion-console-production.bin",
                github_disabled=True)
    assert t._resolve_image(None).name == "motion-console-production.bin"


def test_local_path_resolves_when_github_releases_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "GitHubReleases", None)
    t = _thread(tmp_path, local_name="motion-console-production.bin")
    assert t._resolve_image(None).name == "motion-console-production.bin"


def test_local_path_is_validated(tmp_path):
    t = _thread(tmp_path, local_name="motion-sensor-production.bin",
                github_disabled=True)
    with pytest.raises(_BootloaderSourceError):
        t._resolve_image(None)


def test_download_still_refuses_when_github_disabled(tmp_path):
    t = _thread(tmp_path, github_disabled=True)
    with pytest.raises(_BootloaderSourceError, match="no-github"):
        t._resolve_image(None)


def test_download_still_refuses_without_github_releases(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "GitHubReleases", None)
    t = _thread(tmp_path)
    with pytest.raises(_BootloaderSourceError, match="GitHubReleases"):
        t._resolve_image(None)


def test_hint_is_appended_once():
    assert _with_dfu_hint("Boom.") == f"Boom. {_DFU_STRANDED_HINT}"
    assert _with_dfu_hint("Boom") == f"Boom. {_DFU_STRANDED_HINT}"
    once = _with_dfu_hint("Boom.")
    assert _with_dfu_hint(once) == once
