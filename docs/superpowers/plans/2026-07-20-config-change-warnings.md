# Device-Configuration Change Warnings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a warranty warning that blocks the whole UI at app launch until the user accepts, plus a "permanent damage" confirmation on the device-configuration Save.

**Architecture:** A `QSettings`-backed `WarrantyAck` QObject registered as a QML singleton holds the once-per-install acceptance flag. Two new self-contained QML dialog components — `WarrantyGateDialog` (gate 1, hosted by `main.qml`) and a reusable `ConfirmDialog` (gate 2, used by `pages/Settings.qml`) — both parent to `Overlay.overlay` so they centre on the frameless window. `motion_connector.py` is not touched.

**Tech Stack:** Python 3.13, PyQt6 6.8, QtQuick/QtQuick.Controls 6.0, pytest.

Spec: `docs/superpowers/specs/2026-07-20-config-change-warnings-design.md`
Issue: [openmotion-test-app#47](https://github.com/OpenwaterHealth/openmotion-test-app/issues/47)
Branch: `feature/47-config-change-warnings` (already created off `next`)

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `utils/warranty_ack.py` | Create | `WarrantyAck` QObject: read/persist the acceptance flag via `QSettings`. No QML, no hardware. |
| `tests/test_warranty_ack.py` | Create | Unit tests for the above against a temp INI store. |
| `components/ConfirmDialog.qml` | Create | Reusable two-button confirmation. Configurable title/body/button text, emits `confirmed()`. |
| `components/WarrantyGateDialog.qml` | Create | Gate 1 modal: warning text, exclusive Accept/Decline checkboxes, OK button. Emits `acceptedWarranty()` / `declinedWarranty()`. |
| `main.py` | Modify | Set org/app name for `QSettings`; construct and register the `WarrantyAck` singleton. |
| `main.qml` | Modify | Host `WarrantyGateDialog`; open it on startup when not yet accepted. |
| `pages/Settings.qml` | Modify | Import `../components`; route the user-config Save through a `ConfirmDialog`. |

**Pre-verified during planning** (2026-07-20, PyQt6 6.8, offscreen) — do not re-litigate these:

- `parent: Overlay.overlay` + `anchors.centerIn: parent` centres correctly on the frameless window, including for a dialog declared inside a nested inset page.
- `QSettings.value(key, type=bool)` converts the string `"garbage"` to `True` (fails open). The plan reads the raw value instead.
- The INI backend returns a Python `bool`; the Windows registry backend returns the string `"true"`. Both must be handled.

---

### Task 1: `WarrantyAck` persistence model

**Files:**
- Create: `utils/warranty_ack.py`
- Test: `tests/test_warranty_ack.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_warranty_ack.py`:

```python
"""Unit tests for utils.warranty_ack.WarrantyAck.

Run from the repo root with:  python -m pytest tests/test_warranty_ack.py
(`python -m pytest` puts the repo root on sys.path so `import utils.*` works.)

Every test injects a QSettings backed by a file under tmp_path, so the
developer's real registry (HKCU\\Software\\Openwater\\...) is never touched.
"""
import os

import pytest
from PyQt6.QtCore import QSettings

from utils.warranty_ack import SETTINGS_KEY, WarrantyAck


@pytest.fixture
def ini_path(tmp_path):
    return str(tmp_path / "settings.ini")


def _settings(path):
    """A fresh QSettings over the given INI file."""
    return QSettings(path, QSettings.Format.IniFormat)


def test_defaults_to_not_accepted(ini_path):
    assert WarrantyAck(_settings(ini_path)).accepted is False


def test_accept_persists_to_disk(ini_path):
    WarrantyAck(_settings(ini_path)).accept()

    assert os.path.exists(ini_path)
    assert "accepted=true" in open(ini_path).read()


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
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python -m pytest tests/test_warranty_ack.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'utils.warranty_ack'`.

- [ ] **Step 3: Write the implementation**

Create `utils/warranty_ack.py`:

```python
"""Persistent record of the user's acceptance of the warranty warning.

Issue #47: the test app exposes every hardware surface — FPGA register R/W,
firmware update for three targets, TEC config, camera power, NVCM flash — so
it shows a warranty warning at launch that blocks the UI until the user
accepts. Acceptance is remembered once per install; declining persists
nothing, so a decline re-prompts next launch.

Kept out of ``motion_connector`` on purpose: this is UI consent state, not
hardware state, and that module is already ~3700 lines.

Fails closed. Any problem reading the store, or any value we could not have
written ourselves, leaves ``accepted`` false — the worst case is re-prompting,
never a silently skipped warning.
"""
import logging

from PyQt6.QtCore import QObject, QSettings, pyqtProperty, pyqtSignal, pyqtSlot

logger = logging.getLogger(__name__)

# QSettings scope. On Windows this resolves to
# HKCU\Software\Openwater\Open-MOTION Engineering App.
ORGANIZATION = "Openwater"
APPLICATION = "Open-MOTION Engineering App"

# Key holding the acceptance flag.
SETTINGS_KEY = "warranty/accepted"


def _coerce_accepted(raw) -> bool:
    """True only for a value we ourselves could have written.

    The two backends disagree on type: the INI format hands back a real bool,
    the Windows registry hands back the string "true". Anything else — None,
    a number, a corrupt string — is not an acceptance.

    Deliberately does NOT use ``QSettings.value(key, type=bool)``: Qt's
    QVariant conversion turns the string "garbage" into True, which would fail
    open. Verified on PyQt6 6.8, 2026-07-20.
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "1")
    return False


class WarrantyAck(QObject):
    """QSettings-backed "user accepted the warranty warning" flag.

    Exposed to QML as the ``WarrantyAck`` singleton (see main.py).
    """

    acceptedChanged = pyqtSignal()

    def __init__(self, settings: QSettings | None = None, parent=None):
        """`settings` is injectable so tests can supply a temp INI store
        instead of touching the real registry."""
        super().__init__(parent)
        self._settings = (
            settings if settings is not None else QSettings(ORGANIZATION, APPLICATION)
        )
        self._accepted = self._read()

    def _read(self) -> bool:
        try:
            return _coerce_accepted(self._settings.value(SETTINGS_KEY))
        except Exception as exc:
            logger.warning("Could not read warranty acceptance (%s); will prompt.", exc)
            return False

    @pyqtProperty(bool, notify=acceptedChanged)
    def accepted(self) -> bool:
        return self._accepted

    @pyqtSlot()
    def accept(self) -> None:
        """Record acceptance.

        A failed write is logged, not raised: the user did accept, so this
        session proceeds, and the dialog simply reappears on the next launch.
        """
        try:
            self._settings.setValue(SETTINGS_KEY, True)
            self._settings.sync()
            if self._settings.status() != QSettings.Status.NoError:
                logger.warning(
                    "Warranty acceptance not persisted (QSettings status %s).",
                    self._settings.status(),
                )
        except Exception as exc:
            logger.warning("Could not persist warranty acceptance: %s", exc)

        if not self._accepted:
            self._accepted = True
            self.acceptedChanged.emit()
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python -m pytest tests/test_warranty_ack.py -v
```

Expected: 7 passed. One `WARNING` line about `Status.AccessError` is printed by the unwritable-store test — that is the code under test logging correctly, not a failure.

- [ ] **Step 5: Run the whole suite and the linter**

```bash
python -m pytest tests/ -q
python -m flake8 utils/warranty_ack.py tests/test_warranty_ack.py --max-line-length=120
```

Expected: 28 passed (21 pre-existing + 7 new); flake8 silent.

- [ ] **Step 6: Commit**

```bash
git add utils/warranty_ack.py tests/test_warranty_ack.py
git commit -m "feat: add WarrantyAck persistence model for the config warning gate

Refs #47"
```

---

### Task 2: Register the singleton in `main.py`

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add the import**

In `main.py`, alongside the existing `from utils.log_setup import configure_app_logging` (~line 24), add:

```python
from utils.warranty_ack import APPLICATION, ORGANIZATION, WarrantyAck
```

- [ ] **Step 2: Set the QSettings scope and register the singleton**

In `main()`, find this existing block:

```python
    app = QGuiApplication(sys.argv)

    # Set the global application icon
    app.setWindowIcon(QIcon("assets/images/favicon.png"))
    engine = QQmlApplicationEngine()
```

Replace it with:

```python
    app = QGuiApplication(sys.argv)

    # QSettings resolves its storage location from these. Must be set before
    # any QSettings() is constructed — WarrantyAck below depends on it.
    app.setOrganizationName(ORGANIZATION)
    app.setApplicationName(APPLICATION)

    # Set the global application icon
    app.setWindowIcon(QIcon("assets/images/favicon.png"))
    engine = QQmlApplicationEngine()
```

Then find the existing registration block:

```python
    connector = MOTIONConnector(log_level=log_level, github_disabled=args.no_github)
    qmlRegisterSingletonInstance("OpenMotion", 1, 0, "MOTIONInterface", connector)
```

and add the warranty singleton immediately after it:

```python
    connector = MOTIONConnector(log_level=log_level, github_disabled=args.no_github)
    qmlRegisterSingletonInstance("OpenMotion", 1, 0, "MOTIONInterface", connector)

    # Warranty acknowledgement gate (issue #47). Held in a local so Python
    # keeps a reference alive for the lifetime of the app — qmlRegisterSingletonInstance
    # does not take ownership.
    warranty_ack = WarrantyAck()
    qmlRegisterSingletonInstance("OpenMotion", 1, 0, "WarrantyAck", warranty_ack)
    logger.info("Warranty warning previously accepted: %s", warranty_ack.accepted)
```

- [ ] **Step 3: Verify the app still starts and the singleton is registered**

```bash
QT_QPA_PLATFORM=offscreen python main.py --no-github
```

Expected: the app starts, the log line `Warranty warning previously accepted: False` (or `True`) appears, and no QML errors are printed. Press Ctrl+C to stop.

- [ ] **Step 4: Lint**

```bash
python -m flake8 main.py --max-line-length=120
```

Expected: silent.

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat: expose WarrantyAck to QML and scope QSettings to the app

Refs #47"
```

---

### Task 3: `ConfirmDialog` component

**Files:**
- Create: `components/ConfirmDialog.qml`

No unit test — the repo has no QML test harness. This component is exercised end-to-end in Task 4 and verified in Task 6.

- [ ] **Step 1: Create the component**

Create `components/ConfirmDialog.qml` exactly as below. This markup was prototyped and measured offscreen during planning: at `width: 520` the long gate-2 warning wraps to 8 lines with `truncated=false`, and implicit height sizing gives a 265 px dialog with no clipping.

```qml
import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0

// Reusable two-button confirmation, styled to match the hand-rolled dialogs
// elsewhere in the app (see the odometer reset in pages/Console.qml).
//
//   ConfirmDialog {
//       id: saveConfirm
//       title: "Confirm Configuration Change"
//       warningText: "Warning: ..."
//       onConfirmed: doTheThing()
//   }
//   ... saveConfirm.open()
Dialog {
    id: control

    // Bold orange lead paragraph.
    property string warningText: ""
    // Plain follow-up question below it.
    property string questionText: "Are you sure you want to proceed?"
    property string declineText: "No"
    property string confirmText: "Yes"

    // Emitted when the user picks the confirm button. Named `confirmed` rather
    // than `accepted` because Dialog already defines an `accepted` signal.
    signal confirmed()

    width: 520
    modal: true

    // Parent to the window-wide overlay so the dialog centres on the whole
    // window. main.qml is a frameless ApplicationWindow and pages are inset,
    // so centring on the enclosing page item would be off-centre.
    parent: Overlay.overlay
    anchors.centerIn: parent

    padding: 20

    background: Rectangle {
        color: "#2C2F36"
        radius: 8
        border.color: "#3A3F4B"
    }

    header: Label {
        text: control.title
        visible: control.title.length > 0
        color: "#E0E0E0"
        font.pixelSize: 16
        font.bold: true
        padding: 20
        bottomPadding: 0
    }

    contentItem: ColumnLayout {
        spacing: 16

        Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: control.warningText
            visible: text.length > 0
            color: "#E67E22"
            font.pixelSize: 14
            font.bold: true
        }

        Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: control.questionText
            visible: text.length > 0
            color: "#BDC3C7"
            font.pixelSize: 13
        }

        RowLayout {
            Layout.alignment: Qt.AlignRight
            Layout.topMargin: 4
            spacing: 10

            Button {
                text: control.declineText
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
                onClicked: control.close()
            }

            Button {
                text: control.confirmText
                Layout.preferredWidth: 120
                Layout.preferredHeight: 32
                background: Rectangle {
                    color: parent.hovered ? "#E67E22" : "#3A3F4B"
                    radius: 4
                    border.color: "#E67E22"
                }
                contentItem: Text {
                    text: parent.text
                    color: "#E67E22"
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    font.bold: true
                }
                onClicked: {
                    control.close()
                    control.confirmed()
                }
            }
        }
    }
}
```

- [ ] **Step 2: Commit**

```bash
git add components/ConfirmDialog.qml
git commit -m "feat: add reusable ConfirmDialog component

Refs #47"
```

---

### Task 4: Gate 2 — confirm the user-config Save

**Files:**
- Modify: `pages/Settings.qml` (imports at line 1-5; Save button at ~line 1204-1216)

- [ ] **Step 1: Add the components import**

At the top of `pages/Settings.qml`, the existing imports are:

```qml
import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0
import QtQuick.Dialogs 6.2
import OpenMotion 1.0
```

Add the components directory below them:

```qml
import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0
import QtQuick.Dialogs 6.2
import OpenMotion 1.0

import "../components"
```

- [ ] **Step 2: Change the Save button to open the confirmation**

Find this block (~line 1204):

```qml
                            Rectangle {
                                width: 110; height: 32; radius: 6
                                color: saveJsonMA.containsMouse ? "#27AE60" : "#2ECC71"
                                Behavior on color { ColorAnimation { duration: 120 } }
                                Text { anchors.centerIn: parent; text: "Save"; color: "white"; font.pixelSize: 13; font.bold: true }
                                MouseArea {
                                    id: saveJsonMA; anchors.fill: parent; hoverEnabled: true
                                    onClicked: {
                                        MOTIONInterface.setUserConfigJson(userConfigJsonArea.text)
                                        jsonStatus.text = "Saving..."
                                    }
                                }
                            }
```

Replace only the `onClicked` body so the click opens the gate instead of writing:

```qml
                            Rectangle {
                                width: 110; height: 32; radius: 6
                                color: saveJsonMA.containsMouse ? "#27AE60" : "#2ECC71"
                                Behavior on color { ColorAnimation { duration: 120 } }
                                Text { anchors.centerIn: parent; text: "Save"; color: "white"; font.pixelSize: 13; font.bold: true }
                                MouseArea {
                                    id: saveJsonMA; anchors.fill: parent; hoverEnabled: true
                                    // Gate 2 (issue #47): confirm before writing config to the device.
                                    onClicked: userConfigSaveConfirmDialog.open()
                                }
                            }
```

- [ ] **Step 3: Add the dialog instance**

Add the dialog at the root level of `pages/Settings.qml`, immediately before the file's final closing brace. The root element is `Rectangle { id: page1 ... }`, so this goes as its last child — alongside the other `Dialog` declarations already in this file.

```qml
    // Gate 2 (issue #47): second confirmation before a configuration write.
    // Fires on every Save; not suppressed by the startup warranty acceptance,
    // which guards a different thing.
    ConfirmDialog {
        id: userConfigSaveConfirmDialog
        title: "Confirm Configuration Change"
        warningText: "Warning: Modifying your hardware configuration can cause " +
                     "permanent damage, system instability, or render your device " +
                     "completely unusable. If you are unsure about a configuration " +
                     "change, please contact technical support for assistance."
        questionText: "Are you sure you want to proceed?"
        onConfirmed: {
            MOTIONInterface.setUserConfigJson(userConfigJsonArea.text)
            jsonStatus.text = "Saving..."
        }
    }
```

- [ ] **Step 4: Verify the QML loads without warnings**

```bash
QT_QPA_PLATFORM=offscreen python main.py --no-github
```

Expected: no QML warnings printed (`main.py` wires `engine.warnings` to a print). In particular, no `ConfirmDialog is not a type` and no `Unable to assign` errors. Ctrl+C to stop.

- [ ] **Step 5: Commit**

```bash
git add pages/Settings.qml
git commit -m "feat: confirm before writing user config to the device

Refs #47"
```

---

### Task 5: Gate 1 — startup warranty warning

**Files:**
- Create: `components/WarrantyGateDialog.qml`
- Modify: `main.qml`

- [ ] **Step 1: Create the gate component**

Create `components/WarrantyGateDialog.qml` exactly as below. Prototyped and measured offscreen during planning: centres at 560×230 on a 1200×800 window, checkboxes are mutually exclusive in both directions, and OK is disabled with zero or two boxes checked.

```qml
import QtQuick 6.0
import QtQuick.Controls 6.0
import QtQuick.Layouts 6.0

// Gate 1 (issue #47): warranty warning shown at launch, blocking the whole UI
// until the user explicitly accepts or declines. Controls follow the ticket
// mockup: mutually-exclusive Accept / Decline checkboxes plus an OK button
// that stays disabled until exactly one is picked.
Dialog {
    id: control

    // Named with the `Warranty` suffix to avoid clashing with Dialog's own
    // built-in `accepted` / `rejected` signals.
    signal acceptedWarranty()
    signal declinedWarranty()

    title: "Warning"
    width: 560
    modal: true

    // Cannot be dismissed by Esc or by clicking outside — the user must choose.
    closePolicy: Popup.NoAutoClose

    // Parent to the window-wide overlay: main.qml is a frameless
    // ApplicationWindow, so this is what centres the dialog on the window.
    parent: Overlay.overlay
    anchors.centerIn: parent

    padding: 20

    background: Rectangle {
        color: "#2C2F36"
        radius: 8
        border.color: "#3A3F4B"
    }

    header: Label {
        text: control.title
        color: "#E0E0E0"
        font.pixelSize: 16
        font.bold: true
        padding: 20
        bottomPadding: 0
    }

    contentItem: ColumnLayout {
        spacing: 18

        Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: "Warning: Unofficial changes to the device will void your " +
                  "warranty. Proceed at Own Risk."
            color: "#E67E22"
            font.pixelSize: 14
            font.bold: true
        }

        RowLayout {
            spacing: 24

            CheckBox {
                id: acceptBox
                text: "Accept"
                contentItem: Text {
                    text: parent.text
                    color: "#BDC3C7"
                    font.pixelSize: 13
                    leftPadding: parent.indicator.width + parent.spacing
                    verticalAlignment: Text.AlignVCenter
                }
                // Mutually exclusive with declineBox. The `checked` guard stops
                // the two handlers bouncing off each other.
                onCheckedChanged: if (checked && declineBox.checked) declineBox.checked = false
            }

            CheckBox {
                id: declineBox
                text: "Decline"
                contentItem: Text {
                    text: parent.text
                    color: "#BDC3C7"
                    font.pixelSize: 13
                    leftPadding: parent.indicator.width + parent.spacing
                    verticalAlignment: Text.AlignVCenter
                }
                onCheckedChanged: if (checked && acceptBox.checked) acceptBox.checked = false
            }

            Item { Layout.fillWidth: true }
        }

        RowLayout {
            Layout.alignment: Qt.AlignRight
            spacing: 10

            Button {
                id: okButton
                text: "OK"
                Layout.preferredWidth: 120
                Layout.preferredHeight: 32
                // Exactly one box must be checked.
                enabled: acceptBox.checked !== declineBox.checked
                background: Rectangle {
                    color: okButton.enabled ? (okButton.hovered ? "#E67E22" : "#3A3F4B") : "#2C2F36"
                    radius: 4
                    border.color: okButton.enabled ? "#E67E22" : "#555"
                }
                contentItem: Text {
                    text: okButton.text
                    color: okButton.enabled ? "#E67E22" : "#7F8C8D"
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    font.bold: true
                }
                onClicked: {
                    if (acceptBox.checked) {
                        control.close()
                        control.acceptedWarranty()
                    } else {
                        // Leave the dialog up; the app is about to quit.
                        control.declinedWarranty()
                    }
                }
            }
        }
    }
}
```

- [ ] **Step 2: Host the gate in `main.qml`**

`main.qml` already has `import "components"` and ends with:

```qml
    // JavaScript function to handle sidebar button clicks
    function handleSidebarClick(index) {
        activeMenu = index; // Update the activeMenu property
        // console.log("Button clicked with index:", index);
    }

    Connections {
        target: MOTIONInterface
    }
}
```

Add the gate between `handleSidebarClick` and `Connections`:

```qml
    // JavaScript function to handle sidebar button clicks
    function handleSidebarClick(index) {
        activeMenu = index; // Update the activeMenu property
        // console.log("Button clicked with index:", index);
    }

    // Gate 1 (issue #47): warranty warning. Blocks the whole UI on launch
    // until accepted. Acceptance is remembered once per install, so this only
    // appears on the first run after install. Declining quits the app.
    WarrantyGateDialog {
        id: warrantyGate
        onAcceptedWarranty: WarrantyAck.accept()
        onDeclinedWarranty: Qt.quit()
    }

    Component.onCompleted: {
        if (!WarrantyAck.accepted)
            warrantyGate.open()
    }

    Connections {
        target: MOTIONInterface
    }
}
```

`WarrantyAck` resolves through the existing `import OpenMotion 1.0` at the top of `main.qml` — no new import needed.

- [ ] **Step 3: Verify the QML loads without warnings**

```bash
QT_QPA_PLATFORM=offscreen python main.py --no-github
```

Expected: no QML warnings. The log line from Task 2 reports the current acceptance state. Ctrl+C to stop.

- [ ] **Step 4: Commit**

```bash
git add components/WarrantyGateDialog.qml main.qml
git commit -m "feat: warranty warning gate blocks the UI until accepted

Refs #47"
```

---

### Task 6: Manual verification

**Files:** none — this task only runs the app and records results.

The repo has no QML test harness, so the two gates are verified by hand. Run on Windows with a real display (not offscreen).

- [ ] **Step 1: Clear any existing acceptance**

```powershell
Remove-Item -Path "HKCU:\Software\Openwater\Open-MOTION Engineering App" -Recurse -Force -ErrorAction SilentlyContinue
```

- [ ] **Step 2: Verify gate 1 appearance and controls**

```powershell
python main.py --no-github
```

Check each of these and record the result:

1. The warranty dialog appears on top of the UI, centred on the window.
2. The full warning text is visible and not clipped.
3. `OK` is greyed out with neither box checked.
4. Checking `Accept` enables `OK`.
5. Checking `Decline` unchecks `Accept`, and `OK` stays enabled.
6. Re-checking `Accept` unchecks `Decline`.
7. Unchecking both disables `OK` again.
8. Pressing `Esc` does **not** dismiss the dialog.
9. Clicking outside the dialog does **not** dismiss it.
10. The UI behind the dialog is not clickable.

- [ ] **Step 3: Verify Decline quits without persisting**

With `Decline` checked, click `OK`. Expected: the app exits.

Then confirm nothing was written:

```powershell
Get-ItemProperty -Path "HKCU:\Software\Openwater\Open-MOTION Engineering App\warranty" -ErrorAction SilentlyContinue
```

Expected: no output (key absent).

- [ ] **Step 4: Verify Accept proceeds and persists**

```powershell
python main.py --no-github
```

Expected: the dialog appears again (the decline was not remembered). Check `Accept`, click `OK` — the dialog closes and the UI is usable.

Confirm it was written:

```powershell
Get-ItemProperty -Path "HKCU:\Software\Openwater\Open-MOTION Engineering App\warranty"
```

Expected: `accepted : true`.

- [ ] **Step 5: Verify the gate does not reappear**

Close the app and relaunch:

```powershell
python main.py --no-github
```

Expected: no warranty dialog; the app goes straight to the UI. The startup log reads `Warranty warning previously accepted: True`.

- [ ] **Step 6: Verify gate 2**

Navigate to the Settings page and find the user-config JSON panel.

1. Click `Load` to populate the text area.
2. Edit the JSON text.
3. Click `Save` — the "permanent damage" confirmation appears, centred, text not clipped.
4. Click `No` — the dialog closes, `jsonStatus` does **not** change to "Saving...", and nothing is sent to the device.
5. Click `Save` again, then `Yes` — `jsonStatus` shows "Saving..." and the write proceeds.
6. Click `Save` again — the confirmation appears again (it is not suppressed after the first Yes).

> Steps 6.1–6.6 need a connected device for the write to succeed. Without hardware, still verify 6.3–6.4 and that `Yes` produces the "Saving..." status.

- [ ] **Step 7: Record the results on the issue**

Post a comment on [#47](https://github.com/OpenwaterHealth/openmotion-test-app/issues/47) listing which checks passed and anything that needed follow-up.

---

### Task 7: Open the PR

- [ ] **Step 1: Run the full suite and linter one more time**

```bash
python -m pytest tests/ -q
python -m flake8 main.py utils/ tests/ --max-line-length=120
```

Expected: 28 passed; flake8 silent.

- [ ] **Step 2: Push and open the PR against `next`**

Note the body uses `Refs #47`, **not** `Closes #47` — per the cross-repo CLAUDE.md, merge must not auto-close the issue, because the ticket lives on through pre-release validation.

```bash
git push -u origin feature/47-config-change-warnings
gh pr create -R OpenwaterHealth/openmotion-test-app --base next \
  --title "feat: warranty warning gate and config-save confirmation" \
  --body-file - <<'EOF'
Adds the two warning prompts from #47.

**Gate 1 — warranty warning at launch.** A modal blocks the entire UI on
startup until the user explicitly accepts or declines. It gates the whole app
rather than a single page: the test app exposes every hardware surface (FPGA
register R/W, firmware update for three targets, TEC config, camera power,
NVCM flash), so gating one page would leave most of the dangerous surface open.
Controls follow the ticket mockup — mutually-exclusive Accept/Decline
checkboxes with OK disabled until exactly one is picked. Accept is remembered
once per install via `QSettings`; Decline persists nothing and quits, so it
re-prompts next launch.

**Gate 2 — configuration Save confirmation.** The user-config Save in
`pages/Settings.qml` now routes through a "permanent damage" confirmation.
It fires on every Save and is not suppressed by the gate-1 acceptance — they
guard different things.

**Implementation notes**

- New `utils/warranty_ack.py` holds the acceptance flag. It deliberately avoids
  `QSettings.value(key, type=bool)`, which converts the string `"garbage"` to
  `True` and would fail *open* on a corrupt value; it reads the raw value and
  accepts only a real `True` or the strings `"true"`/`"1"`. Both forms occur —
  the INI backend returns a bool, the Windows registry returns a string.
- Both dialogs use `parent: Overlay.overlay`, which is what centres them
  correctly on the frameless `ApplicationWindow`.
- `motion_connector.py` is untouched — this is UI consent state, not hardware
  state, and that module is already ~3700 lines.
- `components/ConfirmDialog.qml` is reusable. FPGA register writes, firmware
  updates, FPGA programming, serial-number edits, TEC setpoints and NVCM flash
  are deliberately **not** wrapped in this PR; several are flagged in CLAUDE.md
  as "fires immediately, no confirmation" and deserve their own ticket.

**Testing**

- 7 new unit tests in `tests/test_warranty_ack.py` covering the default, the
  round-trip to disk, single-emission/idempotent `accept()`, corrupt-value
  fail-closed, the registry string form, and an unwritable store.
- Manual verification of both dialogs — see the results comment on #47.

Refs #47
EOF
```

Paste the Task 6 manual-verification results into the PR body before opening it, or add them as a follow-up comment.

- [ ] **Step 3: Move the board item to In review**

```bash
gh project item-edit --id PVTI_lADOAif52c4BVgTuzgw2rXI \
  --project-id PVT_kwDOAif52c4BVgTu \
  --field-id PVTSSF_lADOAif52c4BVgTuzhQ7qcU \
  --single-select-option-id 5ef0dc97
```

---

## Notes for the implementer

- **Do not touch `motion_connector.py`.** It is ~3700 lines and none of this work is hardware state.
- **Do not add confirmations** to FPGA register writes, firmware updates, FPGA programming, serial-number edits, TEC setpoints, or NVCM flash. Those are deliberately out of scope for this PR and are tracked separately; `ConfirmDialog.qml` exists so they are cheap to add later.
- **`Overlay.overlay` reads 0×0 during `Component.onCompleted`.** If you find yourself measuring dialog geometry, measure after layout (e.g. from a `Timer`), or you will get misleading zeros.
