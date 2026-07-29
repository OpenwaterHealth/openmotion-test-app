# Offline Bootloader Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a factory operator install the console/sensor bootloader from a `.bin` on disk, with no network, without being able to flash the wrong device's image.

**Architecture:** `_BootloaderInstallThread` gains an optional local path; the GitHub fetch becomes one branch of a new `_resolve_image()` step, mirroring `_ConsoleFpgaUpdateThread`. A pure `_validate_production_image()` helper closes the cross-device brick the SDK does not catch, and is called from QML before the irreversible-warning modal as well as inside the thread. The three duplicated lock-button handlers collapse into one QML helper.

**Tech Stack:** Python 3.13, PyQt6 6.8.0, QML (Qt Quick Controls Material), pytest 7.4.0, `omotion` SDK (editable install from `../openmotion-sdk`).

**Spec:** [`docs/superpowers/specs/2026-07-28-offline-bootloader-install-design.md`](../specs/2026-07-28-offline-bootloader-install-design.md)
**Issue:** [openmotion-test-app#83](https://github.com/OpenwaterHealth/openmotion-test-app/issues/83)
**Branch:** `feature/83-offline-bootloader-install` (already checked out, based on `origin/next`)

## Global Constraints

- Commit prefixes: `feat:` / `fix:` / `refactor:` / `docs:` / `test:` / `chore:`.
- Every commit body ends with `Refs #83` and `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. Never `Closes #83` — the ticket stays open through pre-release validation.
- Do **not** use `Closes`/`Fixes` in commits or the PR body.
- The irreversible-install confirmation dialog (`bootloaderWarningDialog`) must remain in the path. No task removes or bypasses it.
- Baseline before starting: `python -m pytest tests/ -q` → **32 passed**. Never finish a task with fewer than that plus the tests it added.
- All new Python goes in `motion_connector.py` (the repo keeps connector logic there; do not create new modules for this).
- Existing test style: build the object with `Cls.__new__(Cls)` and stub Qt signals with `MagicMock()` — never construct a real `QObject`/`QThread` in a test. See `tests/test_boot_mode_cache.py`.
- `is_production_asset` and friends are imported inside a `try/except` that sets them to `None`. Any new code touching them must tolerate `None`.

---

### Task 1: Production-image validation helper

Closes the cross-device brick: the SDK's only content gate is `"production" in name`, which would happily write `motion-sensor-production.bin` to a console.

**Files:**
- Modify: `motion_connector.py` — import block at lines 47-66, then add the helper next to `_firmware_kind` (~line 78-82)
- Test: `tests/test_production_image_validation.py` (create)

**Interfaces:**
- Consumes: `is_production_asset` from `omotion.firmware_update` (new import)
- Produces: `_validate_production_image(target: str, local_path: str) -> str` — returns `""` when usable, else an operator-readable reason. Used by Task 2 (`_resolve_image`) and Task 3 (`validateProductionImage` slot).

- [ ] **Step 1: Write the failing test**

Create `tests/test_production_image_validation.py`:

```python
"""Filename validation for browsed production images (#83).

The SDK's only content gate before an irreversible write is
`is_production_asset()`, which just checks for the substring "production"
(omotion/firmware_update.py). It does not check console-vs-sensor. The GitHub
path cannot flash the wrong device's image -- it fetches production_asset(kind)
by exact name -- but the browse dialog added in #83 can, so the check lives here.
"""
import pytest

from motion_connector import _validate_production_image


@pytest.fixture
def image(tmp_path):
    """Make a named file on disk and return its path as a string."""
    def _make(name):
        p = tmp_path / name
        p.write_bytes(b"\x00" * 16)
        return str(p)
    return _make


@pytest.mark.parametrize("name", [
    "motion-console-production.bin",
    "motion-console-production-1.8.3.bin",
    "MOTION-CONSOLE-PRODUCTION.BIN",
])
def test_accepts_console_production_images(image, name):
    assert _validate_production_image("console", image(name)) == ""


@pytest.mark.parametrize("target", ["left", "right"])
def test_accepts_sensor_production_image_for_either_slot(image, target):
    assert _validate_production_image(target, image("motion-sensor-production.bin")) == ""


def test_rejects_sensor_image_for_console_the_brick_case(image):
    err = _validate_production_image("console", image("motion-sensor-production.bin"))
    assert err != ""
    assert "console" in err.lower()


def test_rejects_console_image_for_a_sensor(image):
    err = _validate_production_image("left", image("motion-console-production.bin"))
    assert err != ""
    assert "sensor" in err.lower()


def test_rejects_a_non_production_image(image):
    err = _validate_production_image("console", image("motion-console-fw-signed.bin"))
    assert err != ""
    assert "production" in err.lower()


def test_rejects_a_missing_file(tmp_path):
    err = _validate_production_image("console", str(tmp_path / "nope.bin"))
    assert err != ""


def test_rejects_an_unknown_target(image):
    err = _validate_production_image("middle", image("motion-console-production.bin"))
    assert err != ""
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_production_image_validation.py -q
```

Expected: collection error — `ImportError: cannot import name '_validate_production_image' from 'motion_connector'`.

- [ ] **Step 3: Add the `is_production_asset` import**

In `motion_connector.py`, the `try` block starting at line 48 currently imports:

```python
    from omotion.firmware_update import (
        FirmwareKind,
        FirmwareUpdateError,
        FirmwareUpdater,
        UnsupportedReleaseError,
        candidate_assets,
        production_asset,
        register_download,
    )
```

Add `is_production_asset` in alphabetical position (after `FirmwareUpdater`, before `UnsupportedReleaseError` is wrong — the list is roughly CamelCase-then-snake_case; put it with the snake_case names):

```python
    from omotion.firmware_update import (
        FirmwareKind,
        FirmwareUpdateError,
        FirmwareUpdater,
        UnsupportedReleaseError,
        candidate_assets,
        is_production_asset,
        production_asset,
        register_download,
    )
```

And in the matching `except Exception:` block below it (which currently sets `candidate_assets = None`, `production_asset = None`, `register_download = None`), add:

```python
    is_production_asset = None
```

- [ ] **Step 4: Write the helper**

In `motion_connector.py`, immediately after the `_firmware_kind` function (which ends around line 82), add:

```python
# Which filename token each target's production image must carry, and which one
# proves it belongs to the other device. Order: (expected, opposing).
_PRODUCTION_KIND_TOKENS = {
    "console": ("console", "sensor"),
    "left": ("sensor", "console"),
    "right": ("sensor", "console"),
}


def _validate_production_image(target: str, local_path: str) -> str:
    """Vet a browsed production image. Returns "" if usable, else why not.

    The SDK's only content gate before an irreversible write at
    BARE_METAL_FLASH_ADDRESS is is_production_asset(), which checks for the
    substring "production" and nothing else -- notably not console-vs-sensor.
    The GitHub path cannot reach that failure because it fetches
    production_asset(kind) by exact name; a browse dialog can, so the kind check
    has to happen here.
    """
    tokens = _PRODUCTION_KIND_TOKENS.get(target)
    if tokens is None:
        return "Invalid update target."
    expected, opposing = tokens

    if is_production_asset is None:
        return (
            "Bootloader installation is unavailable (omotion SDK not found, "
            "or too old to support it)."
        )

    p = Path(local_path)
    if not p.is_file():
        return "Selected file does not exist."

    name = p.name.lower()
    if not is_production_asset(name):
        return (
            f"{p.name} is not a production image. Converting a device needs the "
            f"bootloader + signed app image, published as "
            f"motion-{expected}-production.bin."
        )
    if expected not in name or opposing in name:
        return (
            f"{p.name} is not a {expected} production image. Flashing another "
            f"device's image is irreversible over USB."
        )
    return ""
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/test_production_image_validation.py -q
```

Expected: PASS (10 tests). Then the full suite:

```bash
python -m pytest tests/ -q
```

Expected: **42 passed** (32 baseline + 10 new).

- [ ] **Step 6: Commit**

```bash
git add tests/test_production_image_validation.py motion_connector.py && git commit -F - <<'MSG'
feat: validate browsed production images by device kind

The SDK gates the production image on the substring "production" alone and
never checks console-vs-sensor, so motion-sensor-production.bin selected for
a console would pass and irreversibly write the wrong image. The GitHub path
cannot reach that -- it fetches production_asset(kind) by exact name -- but
the browse dialog in #83 can.

Requires the expected kind token and rejects the opposing one, so
version-stamped names like motion-console-production-1.8.3.bin still work.

Refs #83

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 2: Thread resolves its image from GitHub *or* a local path

**Files:**
- Modify: `motion_connector.py` — `_BootloaderInstallThread` at lines 566-667
- Test: `tests/test_bootloader_install_source.py` (create)

**Interfaces:**
- Consumes: `_validate_production_image` (Task 1)
- Produces:
  - `_BootloaderSourceError(RuntimeError)` — raised when the image cannot be resolved
  - `_DFU_STRANDED_HINT: str` and `_with_dfu_hint(msg: str) -> str`
  - `_BootloaderInstallThread(connector, target, tag, local_path=None)` — Task 3 passes `local_path`
  - `_BootloaderInstallThread._resolve_image(kind) -> Path`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bootloader_install_source.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_bootloader_install_source.py -q
```

Expected: collection error — `ImportError: cannot import name '_BootloaderSourceError'`.

- [ ] **Step 3: Add the error class and hint helper**

In `motion_connector.py`, immediately **above** `class _BootloaderInstallThread` (line 566), add:

```python
class _BootloaderSourceError(RuntimeError):
    """Raised when a production image can't be resolved (local file or release)."""


# install_bootloader enters DFU before it can detect that a device is already
# converted, and it is flash_bin that passes ":leave" to dfu-util. So every
# abort after enter_dfu() leaves the device sitting in DFU until it is
# power-cycled. Exiting DFU programmatically would need a new DFUProgrammer
# detach call -- an SDK change -- so the failure message says so instead.
_DFU_STRANDED_HINT = "Power-cycle the device to bring it out of DFU."


def _with_dfu_hint(msg: str) -> str:
    """Append the power-cycle hint, unless it is already there."""
    text = str(msg).strip()
    if _DFU_STRANDED_HINT in text:
        return text
    sep = " " if text.endswith(".") else ". "
    return f"{text}{sep}{_DFU_STRANDED_HINT}"
```

- [ ] **Step 4: Add `local_path` to the constructor**

Replace `_BootloaderInstallThread.__init__` (lines 579-584):

```python
    def __init__(self, connector: "MOTIONConnector", target: str, tag: str,
                 local_path: str | None = None):
        super().__init__()
        self._connector = connector
        self._target = target
        self._tag = tag
        self._local_path = local_path
```

- [ ] **Step 5: Extract `_resolve_image`**

Add this method to `_BootloaderInstallThread`, after `__init__`. The download branch is today's code from `run()` moved verbatim, minus the two guards that now gate only this branch:

```python
    def _resolve_image(self, kind) -> Path:
        """Path to the production image, from disk or from a GitHub release.

        The local branch must not touch the network at all -- the factory floor
        runs offline. That is why the GitHubReleases availability check and the
        --no-github bail live here in the download branch rather than in run():
        as top-of-run() guards they blocked local installs too.
        """
        if self._local_path:
            err = _validate_production_image(self._target, self._local_path)
            if err:
                raise _BootloaderSourceError(err)
            return Path(self._local_path)

        if GitHubReleases is None:
            raise _BootloaderSourceError(
                "GitHubReleases is unavailable (omotion SDK not found in environment)."
            )
        if self._connector._github_disabled:
            raise _BootloaderSourceError(
                "GitHub access is disabled (--no-github). Choose 'Upload File...' in "
                "the release dropdown to install from a local image."
            )

        name = production_asset(kind)
        dl_dir = _downloads_dir()
        dl_dir.mkdir(parents=True, exist_ok=True)

        repo_name = (
            _CONSOLE_FW_REPO_NAME
            if self._target == "console"
            else _SENSOR_FW_REPO_NAME
        )
        gh = GitHubReleases(_CONSOLE_FW_REPO_OWNER, repo_name, timeout=30)

        release = None
        last_exc = None
        for candidate_tag in _candidate_console_fw_tags(self._tag):
            try:
                self.progress.emit(-1, f"Fetching release {candidate_tag}...")
                release = gh.get_release_by_tag(candidate_tag)
                break
            except Exception as exc:
                last_exc = exc
        if release is None:
            raise _BootloaderSourceError(
                f"Release '{self._tag}' not found. ({last_exc})"
            )

        names = {
            a.get("name") for a in (gh.get_asset_list(release=release) or [])
            if isinstance(a, dict)
        }
        if name not in names:
            raise _BootloaderSourceError(
                f"Release '{self._tag}' has no {name}. Only releases built "
                "with bootloader support can convert a device."
            )

        self.progress.emit(-1, f"Downloading {name}...")
        return Path(gh.download_asset(release, name, output_dir=dl_dir))
```

- [ ] **Step 6: Rewrite `run()` to use it**

Replace the whole body of `run()` (everything from `if install_bootloader is None` through the final `self.failed.emit(str(exc))`):

```python
    def run(self):
        if install_bootloader is None:
            self.failed.emit(
                "Bootloader installation is unavailable (omotion SDK not found, "
                "or too old to support it)."
            )
            return

        kind = _firmware_kind(self._target)
        try:
            path = self._resolve_image(kind)
        except Exception as exc:
            # Nothing has entered DFU yet, so no power-cycle hint here.
            self.failed.emit(str(exc))
            return

        self.progress.emit(0, "Installing bootloader...")

        def on_progress(p):
            pct = -1
            try:
                if p.percent is not None:
                    pct = int(p.percent)
            except Exception:
                pct = -1
            self.progress.emit(pct, "Installing bootloader...")

        try:
            result = install_bootloader(
                _MutexedDfuHandle(self._connector, self._target),
                kind,
                path,
                acknowledge_irreversible=True,
                programmer=DFUProgrammer(vidpid="0483:df11"),
                dfu_wait_timeout_s=30.0,
                progress_cb=on_progress,
            )
            if not getattr(result, "success", False):
                code = getattr(result, "returncode", "?")
                self.failed.emit(
                    _with_dfu_hint(f"Install failed (dfu-util exit code {code}).")
                )
                return

            self._connector._note_boot_mode(self._target, BootMode.BOOTLOADER)
            self.progress.emit(100, "Bootloader installed")
            self.finished_ok.emit()
        except Exception as exc:
            # Includes BootloaderInstallError -- notably "already installed",
            # which is the abort that makes this safe to offer unconditionally.
            # Everything here is downstream of enter_dfu(), so the device is
            # very likely stranded in DFU; say so.
            self.failed.emit(_with_dfu_hint(str(exc)))
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
python -m pytest tests/test_bootloader_install_source.py -q
```

Expected: PASS (6 tests).

```bash
python -m pytest tests/ -q
```

Expected: **48 passed**.

- [ ] **Step 8: Commit**

```bash
git add tests/test_bootloader_install_source.py motion_connector.py && git commit -F - <<'MSG'
feat: resolve the production image from a local file or a release

Splits image resolution out of _BootloaderInstallThread.run() into
_resolve_image(), mirroring the shape _ConsoleFpgaUpdateThread already uses.
Fixes two guards that ran before any resolution and so would have blocked
local installs: run() required GitHubReleases to be importable even though
a local install does not use it, and the --no-github bail fired regardless
of source.

Also appends a power-cycle hint to install failures. install_bootloader
enters DFU before it can detect an already-converted device, and only
flash_bin passes :leave, so an aborted install strands the device in DFU.

Refs #83

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 3: Connector slots for QML

**Files:**
- Modify: `motion_connector.py` — replace the tail of `installBootloader` (lines 1385-1409) with a call to a new shared helper, then add the helper and both slots
- Test: `tests/test_bootloader_install_slots.py` (create)

**Interfaces:**
- Consumes: `_validate_production_image` (Task 1), `_BootloaderInstallThread(..., local_path=...)` (Task 2)
- Produces:
  - `MOTIONConnector._start_bootloader_thread(target, tag, local_path=None) -> None` — internal
  - `validateProductionImage(target: str, local_path: str) -> str` — `""` when usable, called from QML in Task 4
  - `installBootloaderFromLocal(target: str, local_path: str) -> None` — called from QML in Task 4

> **Decision (pre-flight, confirmed with the repo owner):** the thread wiring is shared
> between the two slots rather than copied. The plan originally specified a copy; sharing
> it won. `installBootloader`'s guards are untouched — only its tail moves.

- [ ] **Step 1: Write the failing test**

Create `tests/test_bootloader_install_slots.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_bootloader_install_slots.py -q
```

Expected: FAIL — `AttributeError: 'MOTIONConnector' object has no attribute 'validateProductionImage'`.

- [ ] **Step 3a: Extract the shared thread starter**

In `installBootloader`, replace everything from `self._set_console_fw_busy(True)` (line 1385) through `self._bl_install_thread.start()` (line 1409) with a single call:

```python
        self._start_bootloader_thread(target, tag)
```

The guards above it (target whitelist, `tag`/`N/A` check, busy check) stay exactly as they are.

Then add the helper immediately after `installBootloader`:

```python
    def _start_bootloader_thread(
        self, target: str, tag: str, local_path: str | None = None
    ) -> None:
        """Wire up and start a bootloader install. Callers do the gating.

        Shared by installBootloader (release tag) and installBootloaderFromLocal
        (browsed file): only the image source differs, so the thread wiring and
        the finished/failed handling live here rather than in both slots.
        """
        self._set_console_fw_busy(True)
        self._bl_install_thread = _BootloaderInstallThread(
            self, target, tag, local_path=local_path
        )
        self._bl_install_thread.progress.connect(
            lambda pct, msg: self.consoleFirmwareUpdateProgress.emit(
                target, "install", int(pct), str(msg)
            )
        )

        def _ok() -> None:
            self._set_console_fw_busy(False)
            self.bootloaderInstallFinished.emit(
                target, True,
                "Bootloader installed. Power-cycle the device before using it.",
            )

        def _fail(msg: str) -> None:
            self._set_console_fw_busy(False)
            self.bootloaderInstallFinished.emit(target, False, str(msg))

        self._bl_install_thread.finished_ok.connect(_ok)
        self._bl_install_thread.failed.connect(_fail)
        self._bl_install_thread.finished.connect(
            lambda: setattr(self, "_bl_install_thread", None)
        )
        self._bl_install_thread.start()
```

- [ ] **Step 3b: Add both slots**

After `_start_bootloader_thread` and before `def _note_boot_mode`, add:

```python
    @pyqtSlot(str, str, result=str)
    def validateProductionImage(self, target: str, local_path: str) -> str:
        """Vet a browsed production image for QML. "" means usable.

        Called from the file dialog so a bad image is rejected *before* the
        irreversible-install confirmation appears, rather than after the
        operator has already agreed to it.
        """
        return _validate_production_image(target, local_path)

    @pyqtSlot(str, str)
    def installBootloaderFromLocal(self, target: str, local_path: str) -> None:
        """Convert a device using a production image from disk. Irreversible.

        The offline counterpart to installBootloader. QML gates this behind the
        same confirmation dialog; the image is validated here as well, because
        by the time the thread runs the operator has already confirmed.
        """
        logger.info(
            f"installBootloaderFromLocal target={target} path={local_path}"
        )
        if target not in ("console", "left", "right"):
            self.bootloaderInstallFinished.emit(target, False, "Invalid target.")
            return
        if self.consoleFirmwareUpdateBusy:
            self.bootloaderInstallFinished.emit(
                target, False, "A firmware operation is already in progress."
            )
            return
        err = _validate_production_image(target, local_path)
        if err:
            self.bootloaderInstallFinished.emit(target, False, err)
            return

        self._start_bootloader_thread(target, "local", local_path)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_bootloader_install_slots.py -q
```

Expected: PASS (6 tests).

```bash
python -m pytest tests/ -q
```

Expected: **54 passed**.

- [ ] **Step 5: Commit**

```bash
git add tests/test_bootloader_install_slots.py motion_connector.py && git commit -F - <<'MSG'
feat: add installBootloaderFromLocal and validateProductionImage slots

validateProductionImage lets the file dialog reject a bad image before the
irreversible-install confirmation is shown, rather than after the operator
has agreed to it. installBootloaderFromLocal re-checks anyway, because by
the time the thread runs the confirmation is already past.

Refs #83

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 4: QML — one lock-button helper, file dialog, routed confirmation

**Files:**
- Modify: `pages/Settings.qml` — properties (~line 67), new helper after `_startFpgaFromLocal` (ends line 116), new `FileDialog` after `fpgaJedUploadDialog` (ends line 649), three `onClicked` handlers (1636, 1837, 2033), `bootloaderWarningDialog` (2222-2240), and the stale comment at 1607-1612

**Interfaces:**
- Consumes: `MOTIONInterface.validateProductionImage(target, path) -> string` and `MOTIONInterface.installBootloaderFromLocal(target, path)` (Task 3)
- Produces: nothing consumed by later tasks

There is no unit-test harness for QML in this repo. Verification is a headless load plus a manual click-through; **do not claim this task is verified beyond what the smoke test actually covers.**

- [ ] **Step 1: Add the routing property**

In `pages/Settings.qml`, after line 68 (`property string blInstallTag: ""`), add:

```qml
    // Set when the pending install came from a browsed file; "" means the
    // install should come from the selected release tag instead. Must be
    // cleared on the tag path or a later tag install would re-flash this file.
    property string blInstallLocalPath: ""
    // Target used when opening the production-image upload dialog
    property string blUploadTarget: ""
```

- [ ] **Step 2: Add the shared helper**

After `_startFpgaFromLocal` closes (line 116), add:

```qml
    // FileDialog reports its selection as a QUrl or a file:// string depending
    // on the Qt version; the connector needs a native path.
    //
    // fwUploadDialog and fpgaJedUploadDialog each still carry their own copy of
    // this logic. They are deliberately left alone: #83 promised no changes to
    // the application-firmware and FPGA local paths, and those are exactly the
    // paths those two dialogs drive.
    function _localPathFromDialog(dialog) {
        var file = ""
        if (typeof dialog.selectedFiles !== 'undefined' && dialog.selectedFiles && dialog.selectedFiles.length > 0) file = dialog.selectedFiles[0]
        else if (typeof dialog.fileUrls !== 'undefined' && dialog.fileUrls && dialog.fileUrls.length > 0) file = dialog.fileUrls[0]
        else if (typeof dialog.fileUrl !== 'undefined' && dialog.fileUrl) file = dialog.fileUrl
        if (!file) return ""

        if (typeof file !== 'string') {
            if (typeof file.toLocalFile === 'function') file = file.toLocalFile()
            else if (typeof file.toString === 'function') file = file.toString()
            else file = String(file)
        }

        if (typeof file === 'string' && file.indexOf("file://") === 0) {
            file = file.replace(/^file:\/\//, "")
            // Windows paths arrive as /C:/... -- drop the leading slash.
            if (file.length > 0 && file[0] === '/' && file[2] === ':') file = file.substring(1)
        }
        return file
    }

    // Shared by the three lock buttons. An "Upload File..." / empty / N/A tag
    // means the operator wants a local image -- on the factory floor with
    // --no-github that is the only entry the dropdown has.
    function _startBootloaderInstall(target, tag) {
        if (!tag || tag === "" || tag === "N/A" || tag === "Upload File...") {
            blUploadTarget = target
            blUploadDialog.open()
            return
        }
        blInstallTarget = target
        blInstallTag = tag
        blInstallLocalPath = ""
        bootloaderWarningDialog.open()
    }
```

- [ ] **Step 3: Add the file dialog**

After the `fpgaJedUploadDialog` block closes (line 649), add:

```qml
    FileDialog {
        id: blUploadDialog
        title: "Select production firmware image"
        nameFilters: ["Production images (*.bin)"]
        onAccepted: {
            var file = _localPathFromDialog(blUploadDialog)
            if (!file) return

            // Reject a wrong-device or non-production image here, before the
            // irreversible-install confirmation is shown.
            var err = MOTIONInterface.validateProductionImage(blUploadTarget, file)
            if (err !== "") {
                fwErrorDialog.message = err
                fwErrorDialog.open()
                return
            }

            var idx = file.lastIndexOf("/")
            if (idx < 0) idx = file.lastIndexOf("\\")
            var fname = idx >= 0 ? file.substring(idx + 1) : file

            blInstallTarget = blUploadTarget
            // The confirmation reads "Install the bootloader from <this>?", so
            // show the filename where a release tag would normally go.
            blInstallTag = fname
            blInstallLocalPath = file
            bootloaderWarningDialog.open()
        }
    }
```

- [ ] **Step 4: Route the confirmation dialog**

In `bootloaderWarningDialog` (line 2233), replace `onConfirmed`:

```qml
        onConfirmed: {
            consoleFwPercent = -1
            consoleFwMessage = ""
            consoleFwStageText = "Starting…"
            if (blInstallLocalPath !== "")
                MOTIONInterface.installBootloaderFromLocal(blInstallTarget, blInstallLocalPath)
            else
                MOTIONInterface.installBootloader(blInstallTarget, blInstallTag)
            fwProgressDialog.open()
        }
```

- [ ] **Step 5: Collapse the three click handlers**

Console — replace `onClicked` at line 1636:

```qml
                                        onClicked: {
                                            if (!blConsoleLockBtn.actionable)
                                                return
                                            var tag = consoleLatestCombo.currentText
                                            if (!tag || tag === "")
                                                tag = consoleLatestFirmware
                                            _startBootloaderInstall("console", tag)
                                        }
```

Left — replace `onClicked` at line 1837:

```qml
                                        onClicked: {
                                            if (!blLeftLockBtn.actionable)
                                                return
                                            var tag = leftLatestCombo.currentText
                                            if (!tag || tag === "")
                                                tag = leftLatestFirmware
                                            _startBootloaderInstall("left", tag)
                                        }
```

Right — replace `onClicked` at line 2033:

```qml
                                        onClicked: {
                                            if (!blRightLockBtn.actionable)
                                                return
                                            var tag = rightLatestCombo.currentText
                                            if (!tag || tag === "")
                                                tag = rightLatestFirmware
                                            _startBootloaderInstall("right", tag)
                                        }
```

- [ ] **Step 6: Fix the stale comment above the console lock button**

Replace the comment block at lines 1607-1612:

```qml
                                // Lock-state control: 🔓 green = bare-metal (unlocked, click to install
                                // the bootloader) · 🔒 amber = bootloader installed (locked indicator).
                                // Boot mode is queried over normal comms (OW_CMD_BOOT_INFO) on connect,
                                // so this reflects real state without a DFU cycle -- but only where the
                                // firmware answers. Sensor firmware does; console firmware does not yet
                                // (issue #73), so a converted console still reads unlocked. Installing is
                                // irreversible over USB; the SDK aborts without writing if a bootloader
                                // is already present.
```

The same stale claim appears above the left and right lock buttons (~1809-1813, ~2005-2009). Replace both with the same corrected text, adjusting nothing else.

- [ ] **Step 7: Smoke-test that the QML still loads**

The app runs without hardware — devices simply read disconnected. Launch it headless for a few seconds and check for QML errors:

```bash
QT_QPA_PLATFORM=offscreen timeout 15 python main.py --no-github 2>&1 | grep -iE "qml|error|warning" | head -30
```

Expected: no `.qml` syntax/reference errors and no `Cannot assign`/`is not a type`/`ReferenceError` lines. A clean run prints nothing from the grep. If `timeout` is unavailable, run `python main.py --no-github`, watch the console, and close the window.

**This only proves the file parses and binds.** It does not exercise any click path.

- [ ] **Step 8: Commit**

```bash
git add pages/Settings.qml && git commit -F - <<'MSG'
feat: install the bootloader from a browsed file

The three lock-button handlers were near-identical copies; they now share
_startBootloaderInstall(). An "Upload File..." or empty tag opens a file
dialog instead of the old refusal, which was unreachable-by-design on an
offline factory floor where the dropdown has no release entries.

The image is validated before the irreversible-install confirmation opens,
and the confirmation shows the filename where a release tag would go. The
confirmation itself is unchanged.

Also corrects the comment above each lock button: boot mode is queried over
OW_CMD_BOOT_INFO on connect, not only after a DFU op.

Refs #83

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

---

### Task 5: Docs

**Files:**
- Modify: `motion_connector.py` — `deviceBootMode` docstring (line 1432-1440)
- Modify: `README.md` — add offline firmware section
- Modify: `C:/Users/ethan/Projects/openmotion-test-app/CLAUDE.md` — **outside this worktree, untracked**

- [ ] **Step 1: Fix the `deviceBootMode` docstring**

Its second paragraph claims mode is only observable in DFU, which stopped being true when the `OW_CMD_BOOT_INFO` query landed. Replace the docstring:

```python
        """Last observed boot mode for a target, or "" if not yet known.

        Populated on connect by querySensorInfo / queryConsoleInfo, which ask
        the device over normal comms (OW_CMD_BOOT_INFO) -- no DFU cycle -- and
        by any DFU operation that runs. Stays "" where the firmware does not
        answer: sensor firmware implements the command, console firmware does
        not yet (issue #73), so a converted console reads "" and shows as
        unlocked. Cleared on disconnect so a swapped device does not inherit
        the previous one's state (issue #77).
        """
```

- [ ] **Step 2: Document the offline flow in the README**

`--no-github` is currently undocumented. After the install/run instructions, add:

```markdown
## Offline / factory use

The app queries GitHub for firmware releases at startup. On a machine with no
internet, launch with:

```
python main.py --no-github
```

This skips every release query. The firmware dropdowns then offer only
`Upload File...`, and all three flashing paths work from a file on disk:

| What | File to select |
|---|---|
| Console / sensor application firmware | `motion-console-fw-*.bin` / `motion-sensor-fw-*.bin` |
| Bootloader (converts a bare-metal device) | `motion-console-production.bin` / `motion-sensor-production.bin` |
| FPGA (TA, Seed, Safety EE, Safety OPT) | the target's `.jed` |

Installing the bootloader is **irreversible over USB** — afterwards the device
only accepts signed firmware, and returning it to a normal image needs an
ST-LINK/SWD debugger. The app confirms before doing it, refuses an image built
for the other device, and the SDK aborts without writing if the device already
has a bootloader.
```

- [ ] **Step 3: Verify nothing regressed**

```bash
python -m pytest tests/ -q && python -m flake8 motion_connector.py --max-line-length=120
```

Expected: **54 passed**, and no *new* flake8 findings. `motion_connector.py` has pre-existing findings; compare against `git stash`-ed output if unsure rather than "fixing" unrelated lines.

- [ ] **Step 4: Commit**

```bash
git add motion_connector.py README.md && git commit -F - <<'MSG'
docs: document the offline firmware flow

--no-github was undocumented, which matters now that every flashing path
including bootloader install works from a local file.

Also corrects deviceBootMode's docstring: it claimed the mode is only
observable in DFU, which stopped being true when the OW_CMD_BOOT_INFO
query landed.

Refs #83

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
MSG
```

- [ ] **Step 5: Update CLAUDE.md in the main checkout**

`CLAUDE.md` is **untracked** and lives only at
`C:/Users/ethan/Projects/openmotion-test-app/CLAUDE.md`, so it is absent from this
worktree and this edit will **not** appear in the PR. Make it last and call it out in the
handoff.

Two places state the old behaviour.

In "Working without hardware", replace this bullet:

```markdown
- `--no-github` disables the firmware-release dropdown (useful when network is locked down or you're on a factory floor without internet).
```

with:

```markdown
- `--no-github` disables the firmware-release dropdowns, leaving `Upload File...` as the only entry (useful when network is locked down or you're on a factory floor without internet). All three flashing paths work from a local file in this mode: application firmware, FPGA `.jed`, and bootloader install from a `motion-*-production.bin`. The production image is validated against the selected target first, because the SDK only checks the filename for "production" and would otherwise let a sensor image be written to a console.
```

In the "Start here" table, replace this row:

```markdown
| Trigger firmware update | `motion_connector.py` `beginFpgaFirmwareUpdate` / sensor DFU path. **No confirmation UI** — wrap in a dialog if you're worried about misclicks. |
```

with:

```markdown
| Trigger firmware update | `motion_connector.py` `beginFpgaFirmwareUpdate` / sensor DFU path. **No confirmation UI** — wrap in a dialog if you're worried about misclicks. Bootloader install is the exception: `installBootloader` (release) and `installBootloaderFromLocal` (browsed `motion-*-production.bin`) both gate behind `bootloaderWarningDialog`. |
```

- [ ] **Step 6: Push and open the PR**

```bash
git push -u origin feature/83-offline-bootloader-install
```

Write the body to a scratch file first — `gh` does not read `--body @-` from stdin (it
takes the string literally):

```bash
cat > /tmp/pr83.md <<'MSG'
## What

Bootloader install was the one firmware path that required GitHub. `_BootloaderInstallThread` only ever downloaded the production image, and the three lock buttons refused a local file outright — so on an offline factory floor, converting a device was impossible. Application firmware and FPGA `.jed` already installed from disk.

Adds a browse-per-flash local path for the production image, mirroring the existing "Upload File..." pattern.

## Safety

`omotion.bootloader_install.install_bootloader` gates the image on its **filename only** — `is_production_asset()` checks for the substring "production" and never console-vs-sensor. The GitHub path cannot reach that (it fetches `production_asset(kind)` by exact name), but a browse dialog can: `motion-sensor-production.bin` selected for a console would pass and irreversibly write the wrong image.

So the app validates first — the filename must carry `production` **and** the expected kind token, rejecting the opposing one. Version-stamped names like `motion-console-production-1.8.3.bin` still work. Validation runs before the irreversible-install confirmation, and again inside the thread.

The confirmation dialog itself is unchanged.

## Also fixed

- `_BootloaderInstallThread.run()` required *both* `install_bootloader` and `GitHubReleases` to be importable; a local install needs only the former.
- Its `--no-github` bail ran before any image resolution, so it would have blocked the local path too. Both checks moved into the download branch.
- Install failures now carry a power-cycle hint. `install_bootloader` enters DFU before it can detect an already-converted device, and only `flash_bin` passes `:leave`, so an aborted install strands the device in DFU.
- Comments above the lock buttons and on `deviceBootMode` claimed boot mode is only knowable after a DFU op. That stopped being true when the `OW_CMD_BOOT_INFO` query landed.

## Testing

22 new unit tests (54 total, up from 32). `pages/Settings.qml` verified only to parse and bind.

**Not verified — needs hardware.** The flash itself cannot be simulated; a bare-metal console and sensor are required. The manual checklist is in the plan document. Please validate before this moves past In review.

Refs #83
MSG
```

```bash
gh pr create -R OpenwaterHealth/openmotion-test-app --base next --title "feat: install the bootloader from a local file (offline factory)" --body-file /tmp/pr83.md
```

`Refs #83`, never `Closes`/`Fixes` — the ticket stays open through pre-release validation.
Then move the board item to **In review**:

```bash
gh project item-edit --id PVTI_lADOAif52c4BVgTuzg0b9a4 --project-id PVT_kwDOAif52c4BVgTuzhQ7qcU --field-id PVTSSF_lADOAif52c4BVgTuzhQ7qcU --single-select-option-id 5ef0dc97
```

---

## Verification status at completion

Report these honestly. Do **not** describe the feature as working.

**Verified by automated tests:** filename validation including the cross-device brick case; local resolution under `--no-github` and with `GitHubReleases` unavailable; the download branch still refusing both; the power-cycle hint; slot-level rejection of bad images without starting a thread.

**Verified by smoke test only:** `pages/Settings.qml` parses and binds.

**Not verified — needs a bare-metal console and sensor on the bench:**

1. Launch with `--no-github`; the release dropdown holds only `Upload File...`.
2. Install the bootloader on a bare-metal console from a staged `motion-console-production.bin`; the lock icon turns 🔒 and the device boots.
3. Repeat on a bare-metal sensor.
4. Select the sensor image for the console; it is rejected before the warning modal and nothing is written.
5. Re-run on an already-converted device; the SDK's "already installed" abort fires, nothing is written, and the message carries the power-cycle hint. A sensor should already read 🔒 with the button inert, so try the console.
6. Regression: application firmware and FPGA `.jed` still flash from a local file under `--no-github`.
