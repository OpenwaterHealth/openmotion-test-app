# NVCM Flash Button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Flash (permanent)" button to the test app's Sensors page that burns CrossLink NVCM on the selected camera(s), backed by a new `omotion.NvcmProgrammer` SDK module with the `.iea`/`.ied` image bundled in the SDK wheel.

**Architecture:** The burn engine (`HardwareDriver` transaction state machine) moves from `openmotion-sdk/scripts/test_factory_prog.py` into the `omotion` package as `NvcmProgrammer`, with deterministic progress (a simulation pre-pass counts total transactions). The test app adds a QThread (mirroring its DFU-flash thread), a connector slot, and a QML button + confirmation dialog using the existing camera/sensor selectors.

**Tech Stack:** Python 3.12+, PyQt6/QML, pytest, setuptools package-data. Spec: `docs/superpowers/specs/2026-06-10-nvcm-flash-button-design.md`.

**Repos/branches:**
- `openmotion-sdk` — continue on `fix/i2c-parser-verify` (PR #66; NvcmProgrammer depends on the parser-verification fix on that branch). SDK is pip-installed editable, so app picks changes up immediately.
- `openmotion-test-app` — new branch `feature/nvcm-flash-button` off `next-next`.

**Safety note:** NVCM is one-time programmable. Hardware tasks in this plan only touch **right sensor camera 2** (approved sacrificial) and **right camera 1** (already burned, used for the negative test). Never run a burn against any left-sensor camera.

---

### Task 0: SDK-compat gate — verify the test app runs with the local SDK

**Files:** none (verification only)

- [ ] **Step 1: Confirm the app environment uses the local editable SDK**

Run:
```powershell
cd C:\Users\ethan\Projects\openmotion-test-app
python -c "import omotion, inspect; print(omotion.__file__); from omotion import MotionInterface; print(inspect.signature(MotionInterface.__init__))"
```
Expected: path under `C:\Users\ethan\Projects\openmotion-sdk\omotion\`, signature includes `data_dir`, `scan_db_path`, `operator_id`.

- [ ] **Step 2: Launch the app with debug logging**

Run (background, give it ~30 s):
```powershell
cd C:\Users\ethan\Projects\openmotion-test-app
python main.py --debug
```
Expected: window opens; no tracebacks in the console/`debug.log`; log shows console + left + right connecting (`state ... -> CONNECTED`).

- [ ] **Step 3: Exercise the Sensors page**

In the GUI (or ask the operator): open Sensors page, toggle a camera power checkbox on the right sensor, confirm telemetry updates and no errors. Then close the app.

- [ ] **Step 4: Record result**

If anything fails here, STOP and fix compatibility before feature work. Note findings in the PR description later.

---

### Task 1: SDK — bundle the NVCM image as package data

**Files:**
- Create: `openmotion-sdk/omotion/nvcm/impl1_algo.iea` (copy)
- Create: `openmotion-sdk/omotion/nvcm/impl1_data.ied` (copy)
- Create: `openmotion-sdk/omotion/nvcm/README.md`
- Modify: `openmotion-sdk/pyproject.toml` (package-data block, ~line 56)

- [ ] **Step 1: Copy the files**

```powershell
cd C:\Users\ethan\Projects\openmotion-sdk
New-Item -ItemType Directory -Force omotion\nvcm | Out-Null
Copy-Item C:\Users\ethan\Projects\openmotion-camera-fpga\HistoFPGAFw\impl1\impl1_algo.iea omotion\nvcm\
Copy-Item C:\Users\ethan\Projects\openmotion-camera-fpga\HistoFPGAFw\impl1\impl1_data.ied omotion\nvcm\
```

- [ ] **Step 2: Write the provenance README**

Create `omotion/nvcm/README.md`:

```markdown
# Bundled NVCM image

`impl1_algo.iea` / `impl1_data.ied` are the Lattice Diamond ISP outputs for
the CrossLink camera FPGA, used by `omotion.NvcmProgrammer` to burn NVCM
(one-time programmable!) through a sensor module's factory I2C commands.

Source: `openmotion-camera-fpga/HistoFPGAFw/impl1/` (Diamond build of
2026-06-08).

To update: rebuild in Diamond, copy both files here, and bump the SDK
version. The pair must always be replaced together — the .iea encodes
offsets into the .ied.
```

- [ ] **Step 3: Add package-data entry**

In `pyproject.toml`, inside the `"omotion" = [` list under `[tool.setuptools.package-data]` (around line 56), add:

```toml
    "nvcm/*",
```

- [ ] **Step 4: Verify wheel collection**

Run:
```powershell
cd C:\Users\ethan\Projects\openmotion-sdk
python -m build --wheel 2>$null | Select-Object -Last 3
python -c "import zipfile,glob; w=sorted(glob.glob('dist/*.whl'))[-1]; print([n for n in zipfile.ZipFile(w).namelist() if 'nvcm' in n])"
```
Expected: list contains `omotion/nvcm/impl1_algo.iea`, `omotion/nvcm/impl1_data.ied`, `omotion/nvcm/README.md`.
(If `python -m build` is unavailable, `pip install build` first.)

- [ ] **Step 5: Commit**

```bash
cd C:/Users/ethan/Projects/openmotion-sdk
git add omotion/nvcm pyproject.toml
git commit -m "feat(nvcm): bundle CrossLink NVCM iea/ied image as package data"
```

---

### Task 2: SDK — `NvcmProgrammer` module (TDD)

**Files:**
- Create: `openmotion-sdk/omotion/NvcmProgrammer.py`
- Test: `openmotion-sdk/tests/test_nvcm_programmer.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_nvcm_programmer.py`:

```python
"""Unit tests for omotion.NvcmProgrammer (no hardware).

Uses hand-assembled micro .iea/.ied files (same technique as
test_i2c_parser_verify.py) against a scripted mock sensor.
"""

import pytest

from omotion.NvcmProgrammer import (
    NvcmProgrammer,
    NvcmResult,
    DEFAULT_ALGO_PATH,
    DEFAULT_DATA_PATH,
)
from omotion.i2c_parser import (
    I2C_STARTTRAN, I2C_RESTARTTRAN, I2C_ENDTRAN, I2C_TRANSOUT, I2C_TRANSIN,
    I2C_TDI, I2C_TDO, I2C_CONTINUE, I2C_TRST, I2C_ENDVME,
)

VERSION = b"_I2C1.0"


class MockSensor:
    """Records every call; serves scripted bytes for write-reads."""

    def __init__(self, read_results=None):
        self.calls = []
        self._reads = list(read_results or [])

    def enable_camera_power(self, mask):
        self.calls.append(("enable_camera_power", mask))
        return True

    def switch_camera(self, camera_id):
        self.calls.append(("switch_camera", camera_id))
        return True

    def creset(self, state):
        self.calls.append(("creset", state))
        return 1 if state else 0

    def i2c_write(self, addr, data):
        self.calls.append(("i2c_write", addr, bytes(data)))

    def i2c_write_read(self, addr, data, read_len):
        self.calls.append(("i2c_write_read", addr, bytes(data), read_len))
        if self._reads:
            return self._reads.pop(0)
        return bytes([0xFF] * read_len)

    def i2c_read(self, addr, read_len):
        self.calls.append(("i2c_read", addr, read_len))
        if self._reads:
            return self._reads.pop(0)
        return bytes([0xFF] * read_len)


def transout(payload: bytes) -> bytes:
    return bytes([I2C_TRANSOUT, len(payload) * 8, I2C_TDI]) + payload + bytes([I2C_CONTINUE])


def transin(expected: bytes) -> bytes:
    return bytes([I2C_TRANSIN, len(expected) * 8, I2C_TDO]) + expected + bytes([I2C_CONTINUE])


def micro_files(tmp_path, expected_idcode=b"\x01\x2c\x00\x43"):
    """Algo: creset low, one pure write, one write+read of 4 bytes."""
    algo = bytearray()
    algo += VERSION
    algo += bytes([I2C_TRST, 0x00])                       # creset low
    # pure write: START, addr 0x80, payload C6 02 00 00, STOP
    algo += bytes([I2C_STARTTRAN])
    algo += transout(b"\x80")
    algo += transout(b"\xc6\x02\x00\x00")
    algo += bytes([I2C_ENDTRAN])
    # write-then-read: START, addr, cmd E0, RESTART, read-addr, read 4 expect idcode
    algo += bytes([I2C_STARTTRAN])
    algo += transout(b"\x80")
    algo += transout(b"\xe0\x00\x00\x00")
    algo += bytes([I2C_RESTARTTRAN])
    algo += transout(b"\x81")
    algo += transin(expected_idcode)
    algo += bytes([I2C_ENDTRAN])
    algo += bytes([I2C_ENDVME])
    algo_p = tmp_path / "t.iea"
    data_p = tmp_path / "t.ied"
    algo_p.write_bytes(bytes(algo))
    data_p.write_bytes(b"\x00")  # compress flag only
    return str(algo_p), str(data_p)


def test_default_files_exist_and_parse():
    assert DEFAULT_ALGO_PATH.is_file()
    assert DEFAULT_DATA_PATH.is_file()
    assert DEFAULT_ALGO_PATH.read_bytes()[:7] == VERSION


def test_burn_happy_path_dispatches_and_succeeds(tmp_path):
    algo, data = micro_files(tmp_path)
    sensor = MockSensor(read_results=[b"\x01\x2c\x00\x43"])
    res = NvcmProgrammer(sensor).burn(3, algo_path=algo, data_path=data)
    assert isinstance(res, NvcmResult)
    assert res.success is True
    assert res.error is None
    assert ("enable_camera_power", 0x04) in sensor.calls
    assert ("switch_camera", 2) in sensor.calls          # camera 3 -> id 2
    assert ("i2c_write", 0x40, b"\xc6\x02\x00\x00") in sensor.calls
    assert ("i2c_write_read", 0x40, b"\xe0\x00\x00\x00", 4) in sensor.calls


def test_burn_verify_failure_maps_error(tmp_path):
    algo, data = micro_files(tmp_path)
    sensor = MockSensor(read_results=[b"\x82\x91\x15\xf8"])  # wrong idcode
    res = NvcmProgrammer(sensor).burn(3, algo_path=algo, data_path=data)
    assert res.success is False
    assert "VERIFY FAIL" in res.error


def test_progress_callback_monotonic_and_complete(tmp_path):
    algo, data = micro_files(tmp_path)
    sensor = MockSensor(read_results=[b"\x01\x2c\x00\x43"])
    seen = []
    res = NvcmProgrammer(sensor).burn(
        3, algo_path=algo, data_path=data,
        progress_cb=lambda done, total: seen.append((done, total)))
    assert res.success
    assert seen, "progress callback never fired"
    totals = {t for _, t in seen}
    assert len(totals) == 1
    dones = [d for d, _ in seen]
    assert dones == sorted(dones)
    assert seen[-1][0] == seen[-1][1]                    # ends at 100%


def test_invalid_camera_rejected():
    with pytest.raises(ValueError):
        NvcmProgrammer(MockSensor()).burn(0)
    with pytest.raises(ValueError):
        NvcmProgrammer(MockSensor()).burn(9)
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
cd C:\Users\ethan\Projects\openmotion-sdk
python -m pytest tests\test_nvcm_programmer.py -v
```
Expected: collection error — `ModuleNotFoundError: omotion.NvcmProgrammer`.

- [ ] **Step 3: Implement `omotion/NvcmProgrammer.py`**

The driver body is moved verbatim from `scripts/test_factory_prog.py`
(`HardwareDriver`, lines 46–194 there), with a transaction counter added.
Full file:

```python
"""NvcmProgrammer — burn a Lattice CrossLink NVCM image via a MotionSensor.

Replays Diamond-generated .iea/.ied I2C transactions over the sensor's
factory commands (OW_FACTORY_*). The default image is bundled in
omotion/nvcm/ (see README there for provenance).

NVCM is ONE-TIME programmable: a successful burn is permanent. The replay
performs full readback verification (omotion.i2c_parser), so a non-blank
or already-programmed device fails fast and corrupt burns cannot PASS.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional

from omotion.i2c_parser import I2CDriver, isp_entry_point, ERR_MESSAGES

logger = logging.getLogger(__name__)

_NVCM_DIR = Path(__file__).resolve().parent / "nvcm"
DEFAULT_ALGO_PATH = _NVCM_DIR / "impl1_algo.iea"
DEFAULT_DATA_PATH = _NVCM_DIR / "impl1_data.ied"

#: Failures within this many transactions are almost always the algorithm's
#: initial IDCODE/status checks rejecting a non-blank (already programmed)
#: device rather than a mid-burn error.
_EARLY_FAIL_TX = 200


@dataclass
class NvcmResult:
    success: bool
    error: Optional[str]
    transactions: int


class _TxState(Enum):
    IDLE = auto()
    AFTER_START = auto()
    WRITE_PHASE = auto()
    READ_ONLY = auto()
    AFTER_RESTART = auto()
    READ_PHASE = auto()


class _CountingSimDriver(I2CDriver):
    """Simulation driver that counts countable transactions.

    The counting rule (stop + read + creset) must stay identical to
    _SensorI2CDriver's so the sim total matches the hardware run.
    """

    def __init__(self) -> None:
        self.count = 0

    def is_simulation(self) -> bool:
        return True

    def start(self) -> None:
        pass

    def restart(self) -> None:
        pass

    def stop(self) -> None:
        self.count += 1

    def write(self, data: bytes) -> None:
        pass

    def read(self, num_bytes: int) -> bytes:
        self.count += 1
        return bytes([0xFF] * num_bytes)

    def creset(self, value: int) -> None:
        self.count += 1

    def wait(self, ms: int) -> None:
        pass


class _SensorI2CDriver(I2CDriver):
    """Group raw parser signals into MotionSensor factory I2C transactions.

    The .iea encodes the I2C address as the first WRITE byte after each
    START/RESTART (0x80 = 0x40 write, 0x81 = 0x40 read). This driver strips
    that byte, accumulates payload, and dispatches on STOP/READ:
        pure write       -> sensor.i2c_write(addr, data)
        write then read  -> sensor.i2c_write_read(addr, data, n)
        pure read        -> sensor.i2c_read(addr, n)
    """

    def __init__(self, sensor, total: int = 0,
                 progress_cb: Optional[Callable[[int, int], None]] = None,
                 default_addr: int = 0x40) -> None:
        self._sensor = sensor
        self._default_addr = default_addr
        self._state = _TxState.IDLE
        self._addr = default_addr
        self._write_buf = bytearray()
        self.count = 0
        self._total = total
        self._progress_cb = progress_cb
        self._last_pct = -1

    def is_simulation(self) -> bool:
        return False

    # ------------------------------------------------------------------

    def _tick(self) -> None:
        self.count += 1
        if self._progress_cb is None or self._total <= 0:
            return
        pct = self.count * 100 // self._total
        if pct != self._last_pct:
            self._last_pct = pct
            self._progress_cb(self.count, self._total)

    # ------------------------------------------------------------------

    def start(self) -> None:
        self._state = _TxState.AFTER_START
        self._addr = self._default_addr
        self._write_buf = bytearray()

    def restart(self) -> None:
        self._state = _TxState.AFTER_RESTART

    def stop(self) -> None:
        if self._state == _TxState.WRITE_PHASE and self._write_buf:
            self._sensor.i2c_write(self._addr, bytes(self._write_buf))
        self._state = _TxState.IDLE
        self._write_buf = bytearray()
        self._tick()

    def write(self, data: bytes) -> None:
        if not data:
            return
        if self._state == _TxState.AFTER_START:
            addr_byte = data[0]
            self._addr = addr_byte >> 1
            if addr_byte & 0x01:
                self._state = _TxState.READ_ONLY
            else:
                self._state = _TxState.WRITE_PHASE
            if len(data) > 1:
                self._write_buf += data[1:]
        elif self._state == _TxState.WRITE_PHASE:
            self._write_buf += data
        elif self._state == _TxState.AFTER_RESTART:
            addr_byte = data[0]
            self._addr = addr_byte >> 1
            self._state = _TxState.READ_PHASE
            if len(data) > 1:
                self._write_buf += data[1:]
        else:
            logger.warning("write() in unexpected state %s", self._state)

    def read(self, num_bytes: int) -> bytes:
        if self._state == _TxState.READ_PHASE and self._write_buf:
            result = self._sensor.i2c_write_read(
                self._addr, bytes(self._write_buf), num_bytes)
        else:
            result = self._sensor.i2c_read(self._addr, num_bytes)
        self._write_buf = bytearray()
        self._tick()
        return result

    def select_camera(self, camera: int) -> None:
        if not (1 <= camera <= 8):
            raise ValueError(f"camera must be 1-8, got {camera}")
        self._sensor.switch_camera(camera - 1)

    def creset(self, value: int) -> None:
        self._sensor.creset(value != 0)
        self._tick()

    def wait(self, ms: int) -> None:
        time.sleep(ms / 1000.0)


class NvcmProgrammer:
    """Burns one camera's CrossLink NVCM. PERMANENT — see module docstring."""

    def __init__(self, sensor) -> None:
        self._sensor = sensor

    def burn(self, camera: int,
             algo_path: Optional[str] = None,
             data_path: Optional[str] = None,
             progress_cb: Optional[Callable[[int, int], None]] = None,
             ) -> NvcmResult:
        """Burn `camera` (1-8). progress_cb(done, total) fires per percent."""
        if not (1 <= camera <= 8):
            raise ValueError(f"camera must be 1-8, got {camera}")
        algo = str(algo_path or DEFAULT_ALGO_PATH)
        data = str(data_path or DEFAULT_DATA_PATH)

        # Deterministic sim pre-pass: exact transaction total for progress,
        # and a file sanity check before touching hardware.
        sim = _CountingSimDriver()
        ret = isp_entry_point(algo, data, driver=sim)
        if ret < 0:
            msg = ERR_MESSAGES.get(ret, f"error {ret}")
            return NvcmResult(False, f"image pre-check failed: {msg}", 0)
        total = sim.count

        # Power the target camera and route the mux.
        self._sensor.enable_camera_power(1 << (camera - 1))
        time.sleep(0.5)

        driver = _SensorI2CDriver(self._sensor, total=total,
                                  progress_cb=progress_cb)
        driver.select_camera(camera)
        logger.info("NVCM burn start: camera %d, %d transactions", camera, total)

        ret = isp_entry_point(algo, data, driver=driver)
        if ret < 0:
            msg = ERR_MESSAGES.get(ret, f"error {ret}")
            if driver.count < _EARLY_FAIL_TX:
                msg += (" (failed during initial checks — device may already"
                        " be programmed / not blank)")
            logger.warning("NVCM burn FAILED: camera %d after %d tx: %s",
                           camera, driver.count, msg)
            return NvcmResult(False, msg, driver.count)

        if progress_cb is not None:
            progress_cb(total, total)
        logger.info("NVCM burn PASSED: camera %d (%d tx)", camera, driver.count)
        return NvcmResult(True, None, driver.count)
```

- [ ] **Step 4: Run tests to verify they pass**

```powershell
cd C:\Users\ethan\Projects\openmotion-sdk
python -m pytest tests\test_nvcm_programmer.py tests\test_i2c_parser_verify.py -v
```
Expected: all PASS (5 new + 7 existing).

- [ ] **Step 5: Commit**

```bash
cd C:/Users/ethan/Projects/openmotion-sdk
git add omotion/NvcmProgrammer.py tests/test_nvcm_programmer.py
git commit -m "feat(nvcm): NvcmProgrammer module with bundled image and progress"
```

---

### Task 3: SDK — make `test_factory_prog.py` a thin CLI over NvcmProgrammer

**Files:**
- Modify: `openmotion-sdk/scripts/test_factory_prog.py` (replace entirely)

- [ ] **Step 1: Replace the script**

New full content:

```python
#!/usr/bin/env python3
"""test_factory_prog.py - Burn a CrossLink NVCM image via the sensor board.

Thin CLI over omotion.NvcmProgrammer. With no file arguments, burns the
image bundled with the SDK (omotion/nvcm/).

Usage:
    python test_factory_prog.py [ALGO.IEA DATA.IED] [--sensor left|right] [--cam N]
"""

import argparse
import logging
import sys
import time

from omotion import MotionInterface
from omotion.NvcmProgrammer import (
    NvcmProgrammer, DEFAULT_ALGO_PATH, DEFAULT_DATA_PATH,
)

logger = logging.getLogger(__name__)
_CONNECT_TIMEOUT = 12.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("algo", nargs="?", default=str(DEFAULT_ALGO_PATH),
                        metavar="ALGO.IEA", help="Lattice algorithm file")
    parser.add_argument("data", nargs="?", default=str(DEFAULT_DATA_PATH),
                        metavar="DATA.IED", help="Lattice data file")
    parser.add_argument("--sensor", default="left", choices=("left", "right"))
    parser.add_argument("--cam", default=1, type=int, help="camera 1-8")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s")

    if not (1 <= args.cam <= 8):
        print(f"Error: --cam must be 1-8, got {args.cam}", file=sys.stderr)
        return 1

    print("Connecting to Motion Sensor...")
    iface = MotionInterface()
    iface.start(wait=True, wait_timeout=_CONNECT_TIMEOUT)
    sensor = iface.left if args.sensor == "left" else iface.right
    deadline = time.monotonic() + _CONNECT_TIMEOUT
    while time.monotonic() < deadline and not sensor.is_connected():
        time.sleep(0.1)
    if not sensor.is_connected():
        print(f"Requested sensor '{args.sensor}' is not connected.")
        iface.stop()
        return 1

    print(f"Connected.  Sensor: {args.sensor}  Camera: {args.cam}")
    print(f"  algo: {args.algo}\n  data: {args.data}\n")

    last = [-1]

    def progress(done, total):
        pct = done * 100 // total
        if pct != last[0]:
            last[0] = pct
            print(f"\r  {pct:3d}%  ({done:,}/{total:,})", end="", flush=True)

    try:
        result = NvcmProgrammer(sensor).burn(
            args.cam, algo_path=args.algo, data_path=args.data,
            progress_cb=progress)
    finally:
        iface.stop()
    print()

    if not result.success:
        print(f"\nProgramming failed: {result.error}", file=sys.stderr)
        print("+=======+\n| FAIL! |\n+=======+\n")
        return 1
    print("\n+=========+\n| PASSED! |\n+=========+\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Check CLI surface**

```powershell
cd C:\Users\ethan\Projects\openmotion-sdk
python scripts\test_factory_prog.py --help
```
Expected: usage shows optional ALGO/DATA, `--sensor`, `--cam`; exits 0. (No hardware run here — Task 7 covers it end to end.)

- [ ] **Step 3: Run the test suite**

```powershell
python -m pytest tests\test_nvcm_programmer.py tests\test_i2c_parser_verify.py -v
```
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add scripts/test_factory_prog.py
git commit -m "refactor(scripts): test_factory_prog as thin CLI over NvcmProgrammer"
```

---

### Task 4: Test app — connector thread, slot, signals

**Files:**
- Modify: `openmotion-test-app/motion_connector.py`
  - new thread class after `_DeviceFirmwareFlashThread` (ends ~line 487)
  - signals + property + slot inside `MOTIONConnector`

- [ ] **Step 1: Create branch**

```bash
cd C:/Users/ethan/Projects/openmotion-test-app
git checkout -b feature/nvcm-flash-button
```

- [ ] **Step 2: Add the thread class**

Insert after `_DeviceFirmwareFlashThread` (after ~line 487):

```python
class _NvcmFlashThread(QThread):
    """Burn CrossLink NVCM on one or more cameras, sequentially.

    NVCM is one-time programmable; QML gates this behind a confirmation
    dialog. Each camera takes ~4-5 minutes (full readback verification).
    """

    progress = pyqtSignal(int, str)          # percent (0-100), message
    cameraDone = pyqtSignal(int, bool, str)  # camera (1-8), ok, error
    finishedAll = pyqtSignal(bool, str)      # overall ok, summary text

    def __init__(self, connector: "MOTIONConnector", sensor_tag: str,
                 cameras: list):
        super().__init__()
        self._connector = connector
        self._sensor_tag = sensor_tag
        self._cameras = cameras

    def run(self):
        try:
            from omotion.NvcmProgrammer import NvcmProgrammer
        except ImportError:
            self.finishedAll.emit(
                False, "NvcmProgrammer unavailable — omotion SDK too old.")
            return

        sensor = getattr(motion_interface, self._sensor_tag)
        programmer = NvcmProgrammer(sensor)
        mutex = self._connector._get_sensor_mutex(self._sensor_tag)
        results = []

        for idx, cam in enumerate(self._cameras):
            self.progress.emit(
                0, f"Camera {cam}: starting NVCM burn "
                   f"({idx + 1} of {len(self._cameras)})…")

            def cb(done, total, cam=cam):
                pct = int(done * 100 / total) if total else 0
                self.progress.emit(
                    pct, f"Camera {cam} — {pct}% ({done:,}/{total:,})")

            mutex.lock()
            try:
                result = programmer.burn(cam, progress_cb=cb)
                ok, err = result.success, (result.error or "")
            except Exception as exc:  # never let the thread die silently
                logger.exception("NVCM burn raised for camera %d", cam)
                ok, err = False, str(exc)
            finally:
                mutex.unlock()

            self.cameraDone.emit(cam, ok, err)
            results.append((cam, ok, err))

        all_ok = all(ok for _, ok, _ in results)
        summary = "\n".join(
            f"Camera {cam}: {'PASS' if ok else 'FAIL — ' + err}"
            for cam, ok, err in results)
        self.finishedAll.emit(all_ok, summary)
```

- [ ] **Step 3: Add signals, property, and slot to `MOTIONConnector`**

Next to the other firmware-update signal declarations add:

```python
    nvcmFlashProgress = pyqtSignal(int, str)           # percent, message
    nvcmFlashCameraDone = pyqtSignal(int, bool, str)   # camera, ok, error
    nvcmFlashFinished = pyqtSignal(bool, str)          # overall ok, summary
    nvcmFlashBusyChanged = pyqtSignal()
```

In `__init__` (near the other thread refs): `self._nvcm_thread = None` and
`self._nvcm_busy = False`.

Next to the other `pyqtProperty` definitions (~line 930):

```python
    @pyqtProperty(bool, notify=nvcmFlashBusyChanged)
    def nvcmFlashBusy(self) -> bool:
        return self._nvcm_busy

    def _set_nvcm_busy(self, busy: bool) -> None:
        if self._nvcm_busy != busy:
            self._nvcm_busy = busy
            self.nvcmFlashBusyChanged.emit()
```

Slot (near `beginDeviceFirmwareDownload`, ~line 1016):

```python
    @pyqtSlot(str, int)
    def flashNvcm(self, sensor_tag: str, camera_mask: int) -> None:
        """Permanently burn NVCM on the masked cameras of one sensor."""
        logger.info(f"flashNvcm sensor={sensor_tag} mask=0x{camera_mask:02X}")
        if sensor_tag not in ("left", "right"):
            self.nvcmFlashFinished.emit(False, "Invalid sensor target.")
            return
        if self._nvcm_busy:
            self.nvcmFlashFinished.emit(
                False, "An NVCM flash is already in progress.")
            return
        cameras = [i + 1 for i in range(8) if camera_mask & (1 << i)]
        if not cameras:
            self.nvcmFlashFinished.emit(False, "No cameras selected.")
            return

        self._set_nvcm_busy(True)
        self._nvcm_thread = _NvcmFlashThread(self, sensor_tag, cameras)
        self._nvcm_thread.progress.connect(self.nvcmFlashProgress)
        self._nvcm_thread.cameraDone.connect(self.nvcmFlashCameraDone)

        def _done(ok: bool, summary: str) -> None:
            self._set_nvcm_busy(False)
            self.nvcmFlashFinished.emit(ok, summary)

        self._nvcm_thread.finishedAll.connect(_done)
        self._nvcm_thread.start()
```

- [ ] **Step 4: Sanity-check the module loads**

```powershell
cd C:\Users\ethan\Projects\openmotion-test-app
python -c "import motion_connector; print('ok')"
```
Expected: `ok` (no syntax/import errors).

- [ ] **Step 5: Commit**

```bash
git add motion_connector.py
git commit -m "feat: NVCM flash thread + flashNvcm slot in connector"
```

---

### Task 5: Test app — QML button, confirmation dialog, progress, summary

**Files:**
- Modify: `openmotion-test-app/pages/Sensor.qml`
  - button column in the Camera Tests pane (after the existing "Flash" button, ~line 1005)
  - two dialogs at page scope (next to `csvFolderDialog`, ~line 241)

- [ ] **Step 1: Add the dialogs at page scope**

Insert after the closing brace of `csvFolderDialog` (~line 330; match indentation of sibling dialogs):

```qml
    // NVCM permanent-flash confirmation
    Dialog {
        id: nvcmConfirmDialog
        title: "Permanent NVCM Flash"
        width: 520
        height: 280
        modal: true
        anchors.centerIn: parent

        property string sensorTag: "left"
        property int cameraMask: 0
        property string cameraLabel: ""

        ColumnLayout {
            anchors.fill: parent
            spacing: 16

            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: "#E74C3C"
                font.pixelSize: 14
                font.bold: true
                text: "This permanently programs the FPGA's one-time-" +
                      "programmable memory and CANNOT be undone."
            }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                color: "#BDC3C7"
                font.pixelSize: 13
                text: "Target: " + nvcmConfirmDialog.cameraLabel + " on the " +
                      nvcmConfirmDialog.sensorTag.toUpperCase() + " sensor.\n\n" +
                      "Each camera takes about 5 minutes to burn and verify; " +
                      "\"All Cameras\" takes about 40 minutes. The app must stay " +
                      "connected for the whole burn."
            }

            RowLayout {
                Layout.alignment: Qt.AlignRight
                spacing: 10

                Button {
                    text: "Cancel"
                    Layout.preferredWidth: 100
                    Layout.preferredHeight: 32
                    background: Rectangle {
                        color: parent.hovered ? "#4A90E2" : "#3A3F4B"
                        radius: 4
                        border.color: "#BDC3C7"
                    }
                    contentItem: Text {
                        text: parent.text
                        color: "#BDC3C7"
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    onClicked: nvcmConfirmDialog.close()
                }
                Button {
                    text: "Flash NVCM"
                    Layout.preferredWidth: 120
                    Layout.preferredHeight: 32
                    background: Rectangle {
                        color: parent.hovered ? "#E74C3C" : "#3A3F4B"
                        radius: 4
                        border.color: "#E74C3C"
                    }
                    contentItem: Text {
                        text: parent.text
                        color: "#E74C3C"
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        font.bold: true
                    }
                    onClicked: {
                        nvcmConfirmDialog.close()
                        MOTIONInterface.flashNvcm(nvcmConfirmDialog.sensorTag,
                                                  nvcmConfirmDialog.cameraMask)
                    }
                }
            }
        }
    }

    // NVCM result summary
    Dialog {
        id: nvcmSummaryDialog
        title: "NVCM Flash Result"
        width: 520
        height: 300
        modal: true
        anchors.centerIn: parent

        property bool resultOk: false
        property string summaryText: ""

        ColumnLayout {
            anchors.fill: parent
            spacing: 16

            Text {
                text: nvcmSummaryDialog.resultOk ? "All cameras PASSED"
                                                 : "One or more cameras FAILED"
                color: nvcmSummaryDialog.resultOk ? "#27AE60" : "#E74C3C"
                font.pixelSize: 15
                font.bold: true
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Text {
                    text: nvcmSummaryDialog.summaryText
                    color: "#BDC3C7"
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                }
            }
            Button {
                text: "Close"
                Layout.alignment: Qt.AlignRight
                Layout.preferredWidth: 100
                Layout.preferredHeight: 32
                background: Rectangle {
                    color: parent.hovered ? "#4A90E2" : "#3A3F4B"
                    radius: 4
                    border.color: "#BDC3C7"
                }
                contentItem: Text {
                    text: parent.text
                    color: "#BDC3C7"
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                onClicked: nvcmSummaryDialog.close()
            }
        }
    }
```

- [ ] **Step 2: Add the button + progress text + signal connections**

Inside the camera select/test `ColumnLayout`, directly after the existing
"Flash" button's closing brace (~line 1005), insert:

```qml
                                    // NVCM permanent flash
                                    Button {
                                        id: nvcmFlashButton
                                        text: "Flash (permanent)"
                                        Layout.preferredWidth: 248
                                        Layout.preferredHeight: 40
                                        Layout.alignment: Qt.AlignLeft
                                        hoverEnabled: true
                                        enabled: {
                                            if (MOTIONInterface.nvcmFlashBusy) return false
                                            if (sensorSelector.currentIndex === 0) {
                                                return MOTIONInterface.leftSensorConnected
                                            } else {
                                                return MOTIONInterface.rightSensorConnected
                                            }
                                        }
                                        contentItem: Text {
                                            text: parent.text
                                            color: parent.enabled ? "#E74C3C" : "#7F8C8D"
                                            horizontalAlignment: Text.AlignHCenter
                                            verticalAlignment: Text.AlignVCenter
                                        }
                                        background: Rectangle {
                                            color: parent.hovered && parent.enabled ? "#5A3A3A" : "#3A3F4B"
                                            radius: 4
                                            border.color: parent.enabled ? "#E74C3C" : "#7F8C8D"
                                        }
                                        onClicked: {
                                            let selectedIndex = cameraDropdown.currentIndex;
                                            let cameraMask = 0x01 << selectedIndex;
                                            let label = "Camera " + (selectedIndex + 1);
                                            if (selectedIndex === 8) {
                                                cameraMask = 0xFF;
                                                label = "ALL cameras (1-8)";
                                            }
                                            nvcmConfirmDialog.sensorTag =
                                                (sensorSelector.currentIndex === 0) ? "left" : "right";
                                            nvcmConfirmDialog.cameraMask = cameraMask;
                                            nvcmConfirmDialog.cameraLabel = label;
                                            nvcmConfirmDialog.open();
                                        }
                                    }

                                    // NVCM progress line
                                    Text {
                                        id: nvcmProgressText
                                        visible: MOTIONInterface.nvcmFlashBusy
                                        Layout.preferredWidth: 248
                                        color: "#F39C12"
                                        font.pixelSize: 12
                                        wrapMode: Text.WordWrap
                                        text: ""
                                    }

                                    Connections {
                                        target: MOTIONInterface
                                        function onNvcmFlashProgress(percent, message) {
                                            nvcmProgressText.text = message
                                        }
                                        function onNvcmFlashFinished(ok, summary) {
                                            nvcmProgressText.text = ""
                                            nvcmSummaryDialog.resultOk = ok
                                            nvcmSummaryDialog.summaryText = summary
                                            nvcmSummaryDialog.open()
                                        }
                                    }
```

- [ ] **Step 3: Launch and visually verify (no burn!)**

```powershell
cd C:\Users\ethan\Projects\openmotion-test-app
python main.py --debug
```
Expected: Sensors page shows "Flash (permanent)" with red accent under "Flash"; clicking opens the confirmation dialog with the duration warning; **Cancel** closes it without side effects. Close the app.

- [ ] **Step 4: Commit**

```bash
git add pages/Sensor.qml
git commit -m "feat: Flash (permanent) NVCM button with confirm dialog on Sensors page"
```

---

### Task 6: PyInstaller packaging check

**Files:** possibly modify `openmotion-test-app/openwater.spec` (only if collection fails)

- [ ] **Step 1: Build the exe**

```powershell
cd C:\Users\ethan\Projects\openmotion-test-app
python -m PyInstaller -y openwater.spec
```

- [ ] **Step 2: Verify the NVCM files are collected**

```powershell
Get-ChildItem -Recurse dist | Where-Object Name -match "impl1_(algo|data)" | Select-Object FullName
```
Expected: both files under `dist\...\omotion\nvcm\`. If absent, add to the spec's `datas` (mirroring how omotion's dfu-util binaries are collected — check for a `collect_data_files('omotion')` or explicit entries) and rebuild. Commit any spec change:

```bash
git add openwater.spec
git commit -m "build: ensure omotion/nvcm package data ships in the exe"
```

---

### Task 7: End-to-end hardware validation (right sensor only)

**Files:** none (validation)

**Safety:** burn target is **right camera 2** (approved sacrificial). Negative test uses **right camera 1** (already burned). Do not touch left-sensor cameras.

- [ ] **Step 1: Positive burn via the UI**

1. `python main.py --debug`
2. Sensors page → sensor selector = **Right**, camera dropdown = **Camera 2**.
3. Click "Flash (permanent)" → dialog shows RIGHT / Camera 2 / duration text → click "Flash NVCM".
4. Expect: progress line counts up (~4–5 min), then summary dialog "All cameras PASSED".

- [ ] **Step 2: Verify NVCM behavior on hardware**

```powershell
cd C:\Users\ethan\Projects\openmotion-sdk
python scripts\nvcm_probe.py --sensor right --camera 2
```
Expected: `VERDICT: *** NVCM PROGRAMMED ***` (0x40 disappears after auto-boot).

Then confirm firmware detect (USART3 pins):
```powershell
python -c "
from omotion import MotionInterface
from omotion.config import DEBUG_FLAG_USB_PRINTF, DEBUG_FLAG_CMD_VERBOSE
import time
iface = MotionInterface(); iface.start(wait=False); time.sleep(4)
s = iface.right
s.set_debug_flags(DEBUG_FLAG_USB_PRINTF | DEBUG_FLAG_CMD_VERBOSE)
s.disable_camera_power(0x02); time.sleep(0.5)
s.enable_camera_power(0x02); time.sleep(1)
print('program_fpga:', s.program_fpga(0x02, False))
s.set_debug_flags(0); iface.stop()
"
```
Expected log: `C2: NVCM detect clk=0 data=0` and `NVCM programmed, skipping SRAM load`.

- [ ] **Step 3: Scan integrity (catches the USART bit-shift regression)**

```powershell
python -u scripts\validate_scan_integrity.py --sensor right --camera-mask 0x02 --duration 8
```
Expected: `VERDICT: PASS`, 0 mismatches.

- [ ] **Step 4: Negative test — already-burned camera fails fast and clean**

In the app: Right sensor, **Camera 1**, "Flash (permanent)" → confirm.
Expected: failure within ~30 s; summary dialog shows
`Camera 1: FAIL — VERIFY FAIL (failed during initial checks — device may already be programmed / not blank)`; app remains responsive; button re-enables.

- [ ] **Step 5: Record results in both PRs**

---

### Task 8: Push branches, update PRs

- [ ] **Step 1: SDK**

```bash
cd C:/Users/ethan/Projects/openmotion-sdk
python -m pytest tests/test_nvcm_programmer.py tests/test_i2c_parser_verify.py -q
git push
gh pr comment 66 --body "Adds omotion.NvcmProgrammer (burn engine moved out of scripts/, bundled iea/ied as package data, progress callbacks). Validated end-to-end from the test app: right cam 2 burned, auto-boots, detect + scan integrity PASS."
```

- [ ] **Step 2: Test app**

```bash
cd C:/Users/ethan/Projects/openmotion-test-app
git push -u origin feature/nvcm-flash-button
gh pr create --base next-next --title "feat: NVCM Flash (permanent) button on Sensors page" --body "Adds a permanent NVCM flash button to the Camera Tests pane using the existing camera/sensor selectors. Burns run on a worker thread via omotion.NvcmProgrammer with live progress; gated behind a confirmation dialog stating permanence and duration (~5 min/camera, ~40 min all). Validated on hardware (right cam 2: burn -> auto-boot -> detect -> scan PASS; negative test on a burned camera fails fast). Requires openmotion-sdk PR #66. Spec: docs/superpowers/specs/2026-06-10-nvcm-flash-button-design.md"
```

---

## Self-Review Notes

- **Spec coverage:** SDK module + bundled files (Tasks 1–2), thin CLI (Task 3), connector + QML with selectors, confirm dialog incl. duration, progress, summary (Tasks 4–5), PyInstaller check (Task 6), SDK-compat gate (Task 0), end-to-end + negative validation (Task 7). ✔
- **Type consistency:** `NvcmResult(success, error, transactions)` used identically in Tasks 2/3/4; thread signals `progress/cameraDone/finishedAll` match the connector wiring; QML uses `nvcmFlashBusy`, `flashNvcm`, `onNvcmFlashProgress`, `onNvcmFlashFinished` exactly as declared. ✔
- **Counting alignment risk:** `_CountingSimDriver` and `_SensorI2CDriver` must tick at the same points (stop/read/creset) — both implementations in Task 2 do; the progress test asserts the final callback equals the total, which catches drift.
- **Honest limits:** GUI steps (Task 0 step 3, Task 5 step 3, Task 7 steps 1/4) need a human or screen automation; flag results rather than assuming.
