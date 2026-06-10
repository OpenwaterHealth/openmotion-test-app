# NVCM Flash Button — Design

**Date:** 2026-06-10
**Repos touched:** `openmotion-sdk`, `openmotion-test-app`
**Prerequisite branches:** `openmotion-sdk` `fix/i2c-parser-verify` (readback
verification in the ISP parser — PR #66). NVCM must never be burned through a
parser without verification.

## Goal

Add a "Flash (permanent)" button to the Camera Tests pane of the test app's
Sensors page that burns the CrossLink NVCM of the camera(s) chosen in the
existing camera dropdown (Camera 1–8 / All Cameras) on the sensor chosen in
the existing sensor selector (left/right). Before feature work, verify the
test app runs correctly against the current local SDK.

## Background

NVCM is one-time programmable. The burn replays a Lattice Diamond
`.iea`/`.ied` transaction stream over the sensor's factory I2C commands
(`OW_FACTORY_*`), today only reachable via
`openmotion-sdk/scripts/test_factory_prog.py`. The replay engine
(`omotion/i2c_parser.py`) now performs real readback verification and real
busy-wait polling; a burn takes ~4–5 minutes per camera. The driver that maps
parsed transactions onto `MotionSensor` calls (`HardwareDriver`) lives in the
script, which does not ship in the `omotion` wheel the app installs.

## Part 1 — SDK: `omotion/NvcmProgrammer.py`

Promote the burn engine into the package (precedent: `DFUProgrammer`,
`FPGAProgrammer`).

- Move `HardwareDriver` (the transaction state machine) from
  `scripts/test_factory_prog.py` into `omotion/NvcmProgrammer.py`.
- Public API:

  ```python
  @dataclass
  class NvcmResult:
      success: bool
      error: str | None          # ERR_MESSAGES text on failure
      transactions: int          # transactions executed

  class NvcmProgrammer:
      def __init__(self, sensor: MotionSensor): ...
      def burn(self, camera: int, algo_path: str, data_path: str,
               progress_cb: Callable[[int, int], None] | None = None
               ) -> NvcmResult:
          """Burn one camera (1-8). progress_cb(done, total)."""
  ```

- `burn()` ensures the target camera is powered
  (`enable_camera_power(1 << (camera-1))`, short settle) and selected
  (`switch_camera`), then runs `isp_entry_point` with a counting driver.
- Progress: the replay is deterministic, so a simulation pass over the same
  files yields the exact transaction total up front; the hardware driver
  counts transactions and fires `progress_cb(done, total)` (throttled to
  ~1 Hz / 1% steps).
- Failure surfaces as `NvcmResult(success=False, error=...)` using the
  existing `ERR_MESSAGES`. An already-programmed (Done-set) part is rejected
  fast by the algorithm's own status check (VERIFY FAIL within seconds) — the
  result message notes this likely cause when failure occurs in the first
  ~100 transactions.
- **Bundled algorithm/data files:** `omotion/nvcm/impl1_algo.iea` and
  `omotion/nvcm/impl1_data.ied`, copied from
  `openmotion-camera-fpga/HistoFPGAFw/impl1/` (current files, June 8 2026
  build), plus `omotion/nvcm/README.md` recording provenance and how to
  update. Shipped as package data in the wheel — same mechanism that already
  bundles the `dfu-util` binaries. `burn()`'s `algo_path`/`data_path`
  arguments become optional, defaulting to the bundled files (resolved via
  `importlib.resources` / path-relative lookup like `_bundled_dfu_util()`).
- `scripts/test_factory_prog.py` becomes a thin CLI wrapper importing the
  class (behavior-compatible: same args, same PASS/FAIL output; the file
  arguments become optional, defaulting to the bundled pair).
- Unit test (`tests/test_nvcm_programmer.py`): scripted mock sensor verifies
  transaction grouping (write / write-read / read dispatch), progress
  callback monotonicity, error mapping, and that the bundled default files
  resolve and parse. No hardware.

## Part 2 — Test app

The app carries **no NVCM assets**: it calls `burn()` with default paths.
One packaging check: confirm PyInstaller collects the `omotion/nvcm/`
package data into the .exe the same way it already collects the bundled
`dfu-util` binaries.

Follow-up (out of scope): source the files from `openmotion-camera-fpga`
GitHub releases once that repo publishes `.iea`/`.ied` assets.

### Connector (`motion_connector.py`)

- `_NvcmFlashThread(QThread)` modeled on `_DeviceFirmwareFlashThread`:
  - ctor: connector, `sensor_tag` ("left"/"right"), `cameras: list[int]`
  - signals: `progress(int percent, str message)`,
    `cameraDone(int camera, bool ok, str error)`, `failed(str)`,
    `finished_ok()`
  - Burns cameras sequentially under the sensor mutex
    (`_get_sensor_mutex(sensor_tag)`); continues to the next camera if one
    fails, reporting per-camera results.
- Slots/signals on `MOTIONConnector`:
  - `@pyqtSlot(str, int) flashNvcm(sensor_tag, camera_mask)` — refuses to
    start if a flash is already running; spawns the thread (file paths come
    from the SDK's bundled defaults).
  - `nvcmFlashProgress(int, str)`, `nvcmFlashCameraDone(int, bool, str)`,
    `nvcmFlashFinished(bool, str)` (overall ok + summary text),
    `nvcmFlashRunning` property for QML enable-state.

### UI (`pages/Sensor.qml`, Camera Tests pane)

- "Flash (permanent)" button directly under the existing "Flash" (SRAM)
  button, same dimensions, warning accent (red/orange border) to signal
  permanence.
- Enabled when the selected sensor is connected and no NVCM flash is running.
- Click → confirmation dialog:
  - Title "Permanent NVCM Flash".
  - Body: sensor side, camera list, and duration estimate
    ("~5 minutes per camera; All Cameras takes ~40 minutes"), plus
    "This permanently programs the FPGA's one-time-programmable memory and
    CANNOT be undone."
  - Buttons: Cancel (default) / Flash.
- During the burn: button disabled, progress text under it
  ("Camera 3 — 42% (12,345/29,500)"), driven by `nvcmFlashProgress`.
- On completion: summary dialog listing per-camera PASS/FAIL with error text
  for failures.

## Part 3 — Verification

1. **SDK-compat gate (before feature work):** run the test app with the
   local editable SDK (`python main.py --debug`); confirm clean startup,
   console + both sensors connect, Sensors page camera power toggles work,
   no errors in debug.log.
2. **SDK unit tests:** `pytest tests/test_nvcm_programmer.py` plus the
   existing `test_i2c_parser_verify.py` suite.
3. **CLI regression:** `test_factory_prog.py` still passes `--help` /
   argument handling; burn path exercised implicitly by (4).
4. **End-to-end hardware test:** use the app button to burn **right sensor
   camera 2** (USART3, blank, sacrificial per owner). Expect: confirmation
   dialog → progress → PASS summary; then `nvcm_probe.py` verdict NVCM
   PROGRAMMED and firmware detect `clk=0 data=0` → SRAM load skipped.
5. **Negative test:** click the button for an already-burned camera (e.g.
   right camera 1). Expect fast, clearly-reported failure; no hang, no
   corruption of app state.

## Out of scope

- Fetching `.iea`/`.ied` from GitHub releases.
- Bloodflow-app integration.
- Abort/cancel of an in-progress burn (matches existing DFU behavior; the
  dialog's duration warning compensates).
- Parallel multi-camera burning (bus is shared; sequential only).

## Risks

- **OTP mistakes are permanent.** Mitigations: confirmation dialog with
  duration + permanence warning, the parser's blank-check fails fast on
  non-blank parts, and burns only run with verification enabled.
- **Wheel coupling:** the app needs an SDK build containing
  `NvcmProgrammer`. Dev uses the editable install; CI/packaged builds need
  an SDK release after PR #66 + this work merge.
- **Long-running thread vs. connection monitor:** burns hold the sensor
  mutex for minutes; other sensor slots will queue. Same trade-off the DFU
  path already makes.
