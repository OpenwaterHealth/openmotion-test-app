# openmotion-test-app — Claude guide

PyQt6 + QML engineering / debug UI. Same Qt stack as `openmotion-bloodflow-app`, but every hardware surface is exposed — FPGA register R/W, firmware update for 3 targets, TEC config, RGB LED, per-fan PWM, etc. Used for bring-up, debugging, and manufacturing test, not clinical use.

Cross-repo context: [../CLAUDE.md](../CLAUDE.md). SDK details: [../openmotion-sdk/CLAUDE.md](../openmotion-sdk/CLAUDE.md).

## Run / build

```powershell
# Dev setup — installs omotion from the wheel published as a GitHub Release
pip install -r requirements.txt

python main.py              # run
python main.py --debug      # verbose logging to console + debug.log
python main.py --no-github  # factory/offline: skip firmware-release queries

python -m PyInstaller -y openwater.spec  # package .exe
```

- Tested on **Python 3.13.5**; README says 3.9+ (actual minimum untested — SDK is 3.12+).
- **omotion is installed from a wheel, not editable.** CI fetches the latest `openmotion-pylib` wheel from `openmotion-sdk` GitHub Releases; falls back to a git source build. To dev against local SDK changes, manually `pip install -e ../openmotion-sdk` over the wheel.
- `requirements.txt` pins PyQt6 6.8.0, PyQt6-Charts 6.8.0, numpy, pandas, PyInstaller 6.15.0, pytest 7.4.0, flake8 7.1.1.

## Layout

| Path | What lives here |
|---|---|
| `main.py` | 119 lines. Entry point + `--debug` / `--no-github` flags. |
| `motion_connector.py` | **3681 lines.** Single `MOTIONConnector` QObject, 136 `@pyqtSlot`/`@pyqtSignal` decorators. |
| `fpga_laser_config.py` | 391 lines. FPGA register model + laser-param loader. Used by `motion_connector.fpgaAddressModel` and `Settings.qml`. |
| `histogram_classifier.py` | 351 lines. Signal-processing for histogram analysis (thresholds appear hardcoded — inspect before relying on outputs). |
| `motion_singleton.py` | Singleton wrapper for the SDK `MOTIONInterface`. |
| `version.py` | Version string; updated from git tag in CI. |
| `rthook_libusb_paths.py` | PyInstaller runtime hook — points the bundle at vendored libusb DLLs at exe launch. |
| `dfu_driver.py` | ~120 lines. Windows DFU-driver preflight (#94): reads `HKLM\...\Enum\USB\VID_0483&PID_DF11` to verify a usable driver (WinUSB/libusbK) is recorded before any DFU entry. Blocks only on a positively-bad binding; never-seen passes. |
| `pages/Demo.qml` | **2242 lines.** Live monitoring — PDC tracking chart, per-camera telemetry, console updates. |
| `pages/Sensor.qml` | 1653 lines. Sensor telemetry, camera power, IMU/accel display. |
| `pages/Console.qml` | 1420 lines. Device info, fan control, RGB LED, safety limits. |
| `pages/Settings.qml` | 1992 lines. Firmware updates (console + sensor FW + FPGA), TEC config, laser params, FPGA register R/W. |
| `models/fpga_model.json` | FPGA register map — 4 modules (TA, Seed, Safety EE, Safety OPT), each on its own I2C channel via `mux_idx=1`. Per-register fields: `name`, `friendlyName`, `desc`, `start_address`, `data_size`, `direction` (RW/RD/WR), `unit`, `scale`. |
| `openwater.spec` | PyInstaller spec. |
| `.github/workflows/release-build.yml` | Single CI workflow — builds, packages, releases on tags / push / manual dispatch. |

## Working without hardware

- `--no-github` disables the firmware-release dropdowns, leaving `Upload File...` as the only entry (useful when network is locked down or you're on a factory floor without internet). All three flashing paths work from a local file in this mode: application firmware, FPGA `.jed`, and bootloader install from a `motion-*-production.bin` (#83). The production image is validated against the selected target first — the SDK only checks the filename for "production" and would otherwise let a sensor image be written to a console, which is irreversible over USB.
- For full mock-mode equivalent to bloodflow-app, point this app at a mocked SDK (the SDK exposes `OPENMOTION_DEMO=1` and `cameraFakeData` debug-flag pathways).

## Un-bootloadering a device (test loop)

"Irreversible" in the install warning means **over USB**. Over SWD/JTAG a converted device
is fully recoverable today, so bench units are reusable across bootloader-install tests.

`openmotion-bl` is ST's SBSFU, but it currently ships with `SECBOOT_DISABLE_SECURITY_IPS`
defined (`SBSFU/App/Inc/app_sfu.h`), which compiles out the whole protection block — no RDP,
no WRP, no PCROP, no DAP lock, no MPU isolation. Its SBOM records the same:
`boot:config = dev mode (...) — RDP/WRP/PCROP/MPU isolation not enforced`. The debug port
stays open, so recovery is a plain sector erase + reflash of a bare-metal image at
`0x08000000`. No RDP regression needed.

Flash layout (STM32H743, 2 MB, 128 KB sectors — `openmotion-bl/Core/Inc/memory_map.h`):

| Range | Sectors | Contents |
|---|---|---|
| `0x08000000-0x0801FFFF` | 0 | Bootloader |
| `0x08020000-0x0809FFFF` | 1–4 | App slot 1 (active) |
| `0x080A0000-0x0811FFFF` | 5–8 | App slot 2 (spare) |
| `0x08120000-0x081DFFFF` | 9–14 | Reserved |
| `0x081E0000-0x081FFFFF` | 15 | **User config — holds serial number** |

**Erase sectors 0–14, not `-e all`.** A mass erase also wipes sector 15, taking the device's
serial number with it.

**This is contingent on the dev-mode build.** If `SECBOOT_DISABLE_SECURITY_IPS` is ever
removed for production: RDP Level 1 gets set (recovery then means an RDP regression, which
mass-erases *everything* including the serial), and `SFU_DAP_PROTECT_ENABLE` disconnects the
debugger. Worse, if `SFU_FINAL_SECURE_LOCK_ENABLE` is ever enabled it sets **RDP Level 2 —
permanent, debug disabled forever, no recovery at all.** At that point every bootloader test
consumes a module. Check `app_sfu.h` before assuming a unit is recoverable.

## FPGA register interface

The historical `models/FpgaModel.js` is gone — current model is **`models/fpga_model.json`**.

To add or change a register:
1. Edit `models/fpga_model.json` (add entry under the right module: `TA`, `Seed`, `Safety EE`, `Safety OPT`).
2. If the read/write semantics need code support, add the slot in `motion_connector.py` and bind in `pages/Settings.qml`.

`pages/Sensor.qml` does **not** access FPGA registers directly — that's `Settings.qml`'s job. Sensor.qml is for telemetry, camera power, IMU.

## Gotchas

- **No confirmation UI on dangerous flashing commands.** `beginFpgaFirmwareUpdate` / `beginFpgaFirmwareFromLocal` (motion_connector.py) and sensor FW DFU don't gate behind a confirm dialog. Treat these as "fires immediately."
- **No abort path** for in-progress sensor DFU — progress is tracked in `Settings.qml` but there's no cancel slot wired up.
- **`histogram_classifier.py` thresholds are hardcoded** (351 lines, no visible config). Inspect before trusting outputs for new conditions.
- **Single TODO** in `motion_connector.py` (~line 2985): "replace stub with actual SDK query when available" — flag if you hit it.
- **Windows libusb** must be reachable at runtime; PyInstaller bundles it via `rthook_libusb_paths.py`. If exe enumeration fails, check the hook.
- **DFU driver preflight can refuse a flash before DFU entry** (#94). `startConsoleFirmwareUpdate` and `_start_bootloader_thread` call `dfu_driver.dfu_driver_issue()` and emit the existing error signals with a "install WinUSB via Zadig" message if Windows records the DFU device (0483:DF11) bound to no/an unusable driver. A machine that has never seen a DFU device passes the preflight — first-flash failures there still surface the old way (SDK-side, after DFU entry).

## Differences vs `openmotion-bloodflow-app`

Both use the same `motion_connector.py` pattern, but expose different surfaces:

| Feature | test-app | bloodflow-app |
|---|---|---|
| FPGA register R/W | ✓ (Settings.qml) | ✗ |
| Firmware updates | Console + Sensor FW + FPGA | Sensor FW only |
| TEC config | User-editable | Limited |
| RGB LED control | ✓ | ✗ |
| Live PDC chart | ✓ (Demo.qml) | ✗ |
| Per-camera power toggle | ✓ (cam_mask) | All-or-nothing |
| Per-fan PWM | ✓ | Basic on/off |
| SDK install | wheel from GitHub Release | editable from `../openmotion-sdk` |

## Branching and releases

- Default branch: `next` (current HEAD).
- Releases triggered by semver tags (e.g. `1.3.2`, `1.3.2-rc.1`); `*-rc.*` tags are marked pre-release on GitHub.
- `version.py._FALLBACK_VERSION` is the offline fallback if `git describe` fails; CI updates it from the tag before build.

## "Start here" by task

| Task | First files |
|---|---|
| Add a sensor or console diagnostic | Pick the right page (`Demo.qml` / `Sensor.qml` / `Console.qml`) → add a slot in `motion_connector.py` → call the relevant SDK method. |
| Add or change an FPGA register | `models/fpga_model.json` → slot in `motion_connector.py` → bind in `pages/Settings.qml`. |
| Tune a histogram classifier threshold | `histogram_classifier.py` — currently hardcoded; consider moving to config. |
| Add a CLI flag | `main.py` lines 42–51 (argparse). |
| Trigger firmware update | `motion_connector.py` `beginFpgaFirmwareUpdate` / sensor DFU path. **No confirmation UI** — wrap in a dialog if you're worried about misclicks. Bootloader install is the exception: `installBootloader` (release tag) and `installBootloaderFromLocal` (browsed `motion-*-production.bin`) both gate behind `bootloaderWarningDialog`. |
