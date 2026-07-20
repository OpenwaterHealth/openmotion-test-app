# Device-configuration change warnings — design

Date: 2026-07-20
Issue: [openmotion-test-app#47](https://github.com/OpenwaterHealth/openmotion-test-app/issues/47)
Branch: `feature/47-config-change-warnings`

## Problem

The test app exposes every hardware surface — FPGA register R/W, firmware update for
three targets, TEC config, camera power, NVCM flashing. Nothing warns the user that
these are unofficial changes that void the warranty, and nothing confirms a
configuration write before it is sent to the device.

Issue #47 asks for two prompts:

1. A warranty warning gating access to the configuration surface.
2. A "permanent damage" confirmation when the user saves a configuration change.

## Scope

Two gates, decided with the issue author:

- **Gate 1** fires **at application launch** and blocks the entire UI. The whole
  test app is a device-configuration tool, so gating a single page would leave most
  of the dangerous surface ungated.
- **Gate 2** wraps **only** the user-config Save in `pages/Settings.qml` — the
  literal "update the configuration → click Save" flow described in the issue.

Explicitly **out of scope**: FPGA register writes, console/sensor firmware updates,
FPGA programming, serial-number edits, TEC setpoints, NVCM flash. `ConfirmDialog.qml`
is built reusable so any of these can be wrapped later in a few lines.

## Architecture

Three new files. `motion_connector.py` is deliberately untouched — it is already
~171 KB / 3681 lines and CLAUDE.md flags it as a hotspot, and the acknowledgement
state has nothing to do with hardware.

| File | Role |
|---|---|
| `utils/warranty_ack.py` | `QObject` wrapping `QSettings`. Exposes an `accepted` bool property (default `false`) and an `accept()` slot. Takes an optional `QSettings` in its constructor so tests can inject a temp store. No QML dependency, so it is unit-testable. |
| `components/WarrantyGateDialog.qml` | Gate 1 modal. Self-contained; emits `accepted()` / `declined()`. |
| `components/ConfirmDialog.qml` | Reusable Yes/No confirmation with configurable title and body. |

Modified:

- `main.py` — set `organizationName` / `applicationName` on the `QGuiApplication`
  so `QSettings` resolves to a stable location, and register the singleton.
- `main.qml` — host `WarrantyGateDialog` and open it on startup when needed.
- `pages/Settings.qml` — route the user-config Save through `ConfirmDialog`.

### Persistence

`QSettings` with organization `Openwater` and application
`Open-MOTION Engineering App`, which on Windows resolves to
`HKCU\Software\Openwater\Open-MOTION Engineering App`. Key: `warranty/accepted`
(bool).

Acceptance is remembered **once per install**. Declining persists nothing, so a
decline re-prompts on the next launch. There is no in-app reset — re-arm the
dialog by deleting the registry key. (Deliberate YAGNI; add a CLI flag later if
QA asks for one.)

## Gate 1 — startup warranty warning

Opened from `main.qml`'s `Component.onCompleted` when `!WarrantyAck.accepted`.

- `modal: true`, `closePolicy: Popup.NoAutoClose` — Esc and click-outside cannot
  dismiss it. The user must make an explicit choice.
- Body text, verbatim from the issue:

  > **Warning: Unofficial changes to the device will void your warranty. Proceed at
  > Own Risk.**

- Controls, matching the issue's mockup: two checkboxes, `Accept` and `Decline`,
  plus an `OK` button.
  - The checkboxes are mutually exclusive — each one's `onCheckedChanged` clears
    the other. Guard the handlers against re-entrancy so clearing one does not
    bounce back and clear the other.
  - `OK` is `enabled` only when exactly one box is checked.
- `OK` with **Accept** checked → call `WarrantyAck.accept()` (persists), close the
  dialog, UI becomes usable.
- `OK` with **Decline** checked → `Qt.quit()`. Nothing is persisted.

If `WarrantyAck.accepted` is already true, the dialog never opens and the app
starts normally.

## Gate 2 — configuration Save confirmation

At `pages/Settings.qml:1208`, the Save button's `MouseArea.onClicked` currently
calls `MOTIONInterface.setUserConfigJson(userConfigJsonArea.text)` directly. It
will instead open a `ConfirmDialog` instance.

- Body text, verbatim from the issue:

  > **Warning**: Modifying your hardware configuration can cause permanent damage,
  > system instability, or render your device completely unusable. If you are unsure
  > about a configuration change, please contact technical support for assistance.
  >
  > Are you sure you want to proceed?

- Buttons: `No` (left, neutral) and `Yes` (right, orange/destructive).
- `Yes` → runs the original call plus the `jsonStatus.text = "Saving..."` update.
- `No` → closes; nothing is sent to the device.

This confirmation fires on every Save. It is not persisted and is not suppressed
by the gate-1 acceptance — they guard different things.

## Styling

Both dialogs follow the existing hand-styled pattern used throughout the app; see
`pages/Console.qml:1570` (odometer reset confirmation) as the reference:

- Plain `Dialog` from `QtQuick.Controls`, not `MessageDialog` — no native dialogs.
- `modal: true`, centered via `x: (parent.width - width) / 2` and the `y` equivalent.
- Background `#3A3F4B`, body text `#BDC3C7`, warning/emphasis text `#E67E22`,
  destructive/proceed action bordered `#E67E22`, neutral hover `#4A90E2`.
- Explicit `contentItem` / `background` on buttons — the app does not rely on the
  Material theme for dialog button styling.

## Error handling

`utils/warranty_ack.py` **fails closed**. If `QSettings` cannot be read, or holds a
value we could not have written, it logs a warning via the module logger and
reports `accepted == false`. The worst case is the dialog appearing on every
launch, never a silently skipped warning.

**Do not use `QSettings.value(key, type=bool)`.** Qt's QVariant conversion turns
the string `"garbage"` into `True`, which fails *open* — exactly the wrong
direction for a safety gate. Verified 2026-07-20 on PyQt6 6.8. Instead read the
raw value and accept only a real `True` or the strings `"true"` / `"1"`. Both
forms occur in practice: the INI backend returns a Python `bool`, the Windows
registry backend returns the string `"true"`.

A failed *write* is logged but not surfaced to the user. The in-session `accepted`
still flips to true — the user did accept, and re-prompting mid-session would be
wrong — and the dialog simply reappears on the next launch.

## Testing

**Unit tests** — `tests/test_warranty_ack.py`, pytest. To keep the developer's real
registry untouched, `WarrantyAck.__init__` accepts an optional pre-built `QSettings`
instance (default: the org/app-scoped one described above), and the tests pass a
`QSettings(path, QSettings.Format.IniFormat)` backed by a `tmp_path` file:

- `accepted` is `false` on a fresh store.
- `accept()` persists (the INI file on disk contains `accepted=true`) and a newly
  constructed instance reads `true`.
- `accept()` emits `acceptedChanged` once, and is idempotent on a second call.
- A corrupt stored value (e.g. the string `garbage`) reads as `false`.
- An unwritable backend does not raise. The in-session `accepted` still becomes
  `true` — the user did accept, and re-prompting mid-session would be wrong — but
  nothing is persisted, so the next launch prompts again.

**Manual verification** — the repo has no QML test harness, so the dialogs are
checked by running the app:

1. Fresh state (registry key deleted) → gate 1 appears, `OK` disabled.
2. Checking Accept clears Decline and vice versa; `OK` enables with exactly one.
3. Decline + OK → app exits.
4. Relaunch → gate 1 appears again (decline was not persisted).
5. Accept + OK → dialog closes, UI usable.
6. Relaunch → no dialog.
7. Settings → edit user config → Save → gate 2 appears.
8. `No` → nothing sent, status text unchanged.
9. `Yes` → config written, status shows `Saving...`.

## Risks

- ~~**Frameless window.**~~ **Resolved 2026-07-20.** `main.qml` uses
  `Qt.FramelessWindowHint`, so dialog centring needed checking. Setting
  `parent: Overlay.overlay` with `anchors.centerIn: parent` centres correctly on
  the full window — verified offscreen both at the window root and from a dialog
  declared inside a nested, inset page (the `pages/Settings.qml` case): the dialog
  landed at exactly the computed centre of the 1200×800 overlay. Note the overlay
  reads 0×0 during `Component.onCompleted`; measure after layout.
- **Startup ordering.** `motion_interface.start()` runs in `main.py` after
  `engine.load()`, so hardware monitoring begins regardless of the gate. A decline
  calls `Qt.quit()` and the existing `aboutToQuit` handler stops the monitor
  cleanly. Accepted as-is — the gate protects against user action, not against the
  monitor connecting.
