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
    """True only for a value a QSettings backend could hand back for a stored True.

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

    GUI-thread only: ``accept()`` mutates ``_accepted`` and emits
    ``acceptedChanged`` without a lock, and QSettings itself is reentrant but
    not thread-safe. Do not call from a worker thread.
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
