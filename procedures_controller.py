"""Procedures pane backend — runs guided WI procedures as subprocesses.

Design: a procedure is an external script (today: the SDK's guided WI-00015
runner) executed as a child process with its stdout piped into the pane's
terminal and its interactive prompts answered through the UI. Running out of
process keeps the hardware-validated runner byte-identical to what a headless
factory rig executes, and gives Stop a hard boundary.

Device handling: the child needs exclusive access to the console COM port and
the sensors' USB interfaces, so Start releases the app's device handles
(monitor stopped + explicit disconnects) and completion reacquires them. As a
safety backstop, after any stop/kill the console is reconnected and issued a
``stop_trigger`` — a killed child cannot run its own cleanup, and the laser
must never be left firing.

Prompt protocol (matches scripts/wi15_guided.py in openmotion-sdk):
  - a line containing "[y to continue"  -> Continue button ("y")
  - a line containing "[left/right]"    -> Left / Right buttons
"""

from __future__ import annotations

import logging
import os
import sys

from PyQt6.QtCore import (
    QObject,
    QProcess,
    QProcessEnvironment,
    QSettings,
    QTimer,
    pyqtProperty,
    pyqtSignal,
    pyqtSlot,
)

from motion_singleton import motion_interface

logger = logging.getLogger(__name__)

STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_PASS = "pass"
STATUS_FAIL = "fail"

MAX_LOG_LINES = 5000

# Non-verbose ("factory") mode shows only operator-facing lines: the guided
# runner's instructions and gates, phase banners, and outcomes. Everything
# else (measurements, register dumps, engine chatter) is technician detail
# behind the Verbose checkbox. The full log is always retained - the filter
# is display-only and applies retroactively when toggled.
_INSTRUCTION_PREFIXES = (
    ">>>",            # operator instructions from the guided runner
    "===",            # phase banners
    "!",              # errors / warnings raised by this controller
    "[operator]",     # answers echoed back
    "procedure ",     # start/stop/completion lines
    "WI-00015",       # run header
    "report:",        # emitted PDF paths
    "outcome:",       # phase outcomes
    "persistence:",   # power-cycle verification verdict
    "NEXT:",          # flow hints
)


def _is_operator_line(line: str) -> bool:
    s = line.strip()
    return (s.startswith(_INSTRUCTION_PREFIXES)
            or "PASSED" in s or "FAILED" in s or "REFUSED" in s)


_SIMPLE_TAG = "@@SIMPLE "


def _display_line(line: str, verbose: bool, state: dict) -> str | None:
    """What (if anything) to show for a raw log line.

    ``state`` carries ``simple_active`` between lines: when the guided
    runner emits an @@SIMPLE plain-language instruction, factory mode shows
    it INSTEAD of the detailed ``>>>`` prompt that follows. CLI-only hint
    lines ("[y to continue...]", "[left/right]") are never shown in factory
    mode - the pane's buttons replace them.
    """
    s = line.strip()
    if s.startswith(_SIMPLE_TAG):
        if verbose:
            return None  # verbose users read the detailed prompt instead
        state["simple_active"] = True
        return s[len(_SIMPLE_TAG):]
    if verbose:
        return line
    if s.startswith("===") or s.startswith("[operator]"):
        state["simple_active"] = False
        return line
    if s.startswith(">>>"):
        return None if state.get("simple_active") else line
    if "[y to continue" in s or "[left/right]" in s:
        return None
    return line if _is_operator_line(line) else None


def _find_wi15_guided() -> str | None:
    """Locate the SDK's guided WI-00015 runner.

    Resolution order: OPENMOTION_WI15_GUIDED env var, then
    <sdk-repo>/scripts/wi15_guided.py relative to the imported omotion
    package (works for PYTHONPATH / editable installs; wheels do not ship
    scripts/).
    """
    env = os.environ.get("OPENMOTION_WI15_GUIDED")
    if env and os.path.isfile(env):
        return env
    try:
        import omotion
        root = os.path.dirname(os.path.dirname(os.path.abspath(omotion.__file__)))
        cand = os.path.join(root, "scripts", "wi15_guided.py")
        if os.path.isfile(cand):
            return cand
    except Exception:
        pass
    return None


class ProceduresController(QObject):
    logLine = pyqtSignal(str, arguments=["line"])
    statusChanged = pyqtSignal()
    promptChanged = pyqtSignal()
    logCleared = pyqtSignal()
    verboseChanged = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._status = STATUS_IDLE
        self._prompt = ""          # "", "continue", "side"
        self._lines: list[str] = []
        self._linebuf = ""
        self._process: QProcess | None = None
        self._stop_requested = False
        self._current_index = 0
        self._settings = QSettings()
        self._verbose = self._settings.value(
            "procedures/verbose", True, type=bool)
        self._filter_state: dict = {}

        # Laser tuning (WI sections 4.1-4.5) and BFI/BVI calibration (4.6)
        # are deliberately separate procedures, mirroring the SDK-side
        # segmentation of the two flows.
        script = _find_wi15_guided()
        self._procedures = [
            {
                "name": "WI-00015 Laser Tuning (4.1-4.5)",
                "script": script,
                "args": ["--fresh", "--skip-calibration"],
            },
            {
                "name": "WI-00015 Laser Tuning - dev window 100-200 uJ",
                "script": script,
                "args": ["--fresh", "--skip-calibration",
                         "--window", "100", "200"],
            },
            {
                "name": "WI-00015 Laser Calibration (4.6)",
                "script": script,
                "args": ["--fresh", "--skip-tuning"],
            },
            {
                "name": "WI-00015 Laser Calibration - dev, dim laser OK",
                "script": script,
                "args": ["--fresh", "--skip-tuning", "--bench-thresholds"],
            },
        ]

    # ------------------------------------------------------------------ QML
    @pyqtProperty("QVariantList", constant=True)
    def procedureNames(self):
        return [p["name"] for p in self._procedures]

    @pyqtProperty(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @pyqtProperty(bool, notify=statusChanged)
    def running(self) -> bool:
        return self._status == STATUS_RUNNING

    @pyqtProperty(str, notify=promptChanged)
    def promptType(self) -> str:
        return self._prompt

    @pyqtProperty(str, constant=False)
    def fullLog(self) -> str:
        return "\n".join(self._lines)

    @pyqtProperty(str, constant=False)
    def visibleLog(self) -> str:
        """The log as it should appear under the current verbosity."""
        state: dict = {}
        out = []
        for l in self._lines:
            d = _display_line(l, self._verbose, state)
            if d is not None:
                out.append(d)
        return "\n".join(out)

    def _get_verbose(self) -> bool:
        return self._verbose

    def _set_verbose(self, value: bool) -> None:
        if value != self._verbose:
            self._verbose = bool(value)
            self._settings.setValue("procedures/verbose", self._verbose)
            self.verboseChanged.emit()

    verbose = pyqtProperty(bool, fget=_get_verbose, fset=_set_verbose,
                           notify=verboseChanged)

    # ---------------------------------------------------------------- slots
    @pyqtSlot(int)
    def selectProcedure(self, index: int) -> None:
        """Changing procedure resets the status indicator (and prompt)."""
        if self._status == STATUS_RUNNING:
            return  # UI disables the combo while running; belt and braces
        self._current_index = index
        self._set_status(STATUS_IDLE)
        self._set_prompt("")

    @pyqtSlot(int)
    def startProcedure(self, index: int) -> None:
        if self._process is not None:
            self._log("! a procedure is already running")
            return
        if not 0 <= index < len(self._procedures):
            return
        proc = self._procedures[index]
        self._current_index = index
        if not proc["script"]:
            self._log("! cannot locate the guided runner script "
                      "(set OPENMOTION_WI15_GUIDED or run the app against an "
                      "SDK checkout that contains scripts/wi15_guided.py)")
            self._set_status(STATUS_FAIL)
            return

        self._lines.clear()
        self._linebuf = ""
        self._filter_state = {}
        self.logCleared.emit()
        self._stop_requested = False
        self._log(f"=== {proc['name']} ===")
        self._log(f"script: {proc['script']}")

        self._release_devices()

        self._process = QProcess(self)
        self._process.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        # Emoji in the @@SIMPLE operator lines require UTF-8 on the child's
        # pipes; the Windows default (cp1252) would crash the child's print.
        env.insert("PYTHONUTF8", "1")
        try:
            import omotion
            sdk_root = os.path.dirname(
                os.path.dirname(os.path.abspath(omotion.__file__)))
            prev = env.value("PYTHONPATH", "")
            env.insert("PYTHONPATH",
                       sdk_root + (os.pathsep + prev if prev else ""))
        except Exception:
            pass
        self._process.setProcessEnvironment(env)
        self._process.readyReadStandardOutput.connect(self._on_stdout)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)

        args = ["-u", proc["script"], *proc["args"]]
        self._log(f"launching: {sys.executable} {' '.join(args)}")
        self._set_status(STATUS_RUNNING)
        self._process.start(sys.executable, args)

    @pyqtSlot()
    def stopProcedure(self) -> None:
        if self._process is None:
            return
        self._stop_requested = True
        self._log("! stop requested - terminating procedure")
        self._process.kill()  # hard kill; safety stop_trigger runs on reacquire

    @pyqtSlot(str)
    def answerPrompt(self, text: str) -> None:
        if self._process is None:
            return
        self._log(f"[operator] {text}")
        self._process.write((text + "\n").encode("utf-8"))
        self._set_prompt("")

    # ------------------------------------------------------------- internals
    def _on_stdout(self) -> None:
        if self._process is None:
            return
        data = bytes(self._process.readAllStandardOutput()).decode(
            "utf-8", errors="replace")
        self._linebuf += data
        while "\n" in self._linebuf:
            line, self._linebuf = self._linebuf.split("\n", 1)
            self._emit_line(line.rstrip("\r"))
        # input() prompts arrive without a trailing newline - flush them so
        # the operator can see what is being asked, and arm the answer UI.
        tail = self._linebuf
        if "[y to continue" in tail:
            self._emit_line(tail)
            self._linebuf = ""
            self._set_prompt("continue")
        elif "[left/right]" in tail:
            self._emit_line(tail)
            self._linebuf = ""
            self._set_prompt("side")

    def _emit_line(self, line: str) -> None:
        self._lines.append(line)
        if len(self._lines) > MAX_LOG_LINES:
            del self._lines[: len(self._lines) - MAX_LOG_LINES]
        # The stored log is always complete; only the display stream is
        # gated by the Verbose checkbox / @@SIMPLE protocol.
        shown = _display_line(line, self._verbose, self._filter_state)
        if shown is not None:
            self.logLine.emit(shown)

    def _log(self, line: str) -> None:
        self._emit_line(line)

    def _on_error(self, err) -> None:
        self._log(f"! process error: {err}")

    def _on_finished(self, exit_code: int, exit_status) -> None:
        if self._linebuf:
            self._emit_line(self._linebuf)
            self._linebuf = ""
        self._process = None
        self._set_prompt("")
        if self._stop_requested:
            self._log("procedure stopped by operator")
            self._set_status(STATUS_IDLE)
        elif exit_code == 0:
            self._log("procedure completed: PASS")
            self._set_status(STATUS_PASS)
        else:
            self._log(f"procedure completed: FAIL (exit code {exit_code})")
            self._set_status(STATUS_FAIL)
        self._reacquire_devices()

    def _release_devices(self) -> None:
        """Hand the console + sensors to the child process."""
        self._log("releasing device handles to the procedure ...")
        try:
            motion_interface.stop()
            for handle in (motion_interface.console, motion_interface.left,
                           motion_interface.right):
                try:
                    handle.request_disconnect()
                except Exception as e:
                    logger.debug("request_disconnect: %s", e)
        except Exception as e:
            self._log(f"! device release problem: {e}")

    def _reacquire_devices(self) -> None:
        """Take the devices back and make the laser safe.

        The child normally stops the trigger itself (finally blocks), but a
        killed process cannot - so once the console reconnects, issue a
        stop_trigger unconditionally.
        """
        self._log("reacquiring device handles ...")
        try:
            motion_interface.start(wait=False)
        except Exception as e:
            self._log(f"! device reacquire problem: {e}")
            return

        attempts = {"n": 0}

        def _try_safe():
            attempts["n"] += 1
            try:
                if motion_interface.console.is_connected():
                    try:
                        motion_interface.console.stop_trigger()
                        self._log("console reconnected; trigger stop confirmed")
                    except Exception as e:
                        self._log(f"! stop_trigger after reacquire failed: {e}")
                    return
            except Exception:
                pass
            if attempts["n"] < 30:
                QTimer.singleShot(1000, _try_safe)
            else:
                self._log("! console did not reconnect within 30 s - "
                          "check connections")

        QTimer.singleShot(1000, _try_safe)

    def _set_status(self, status: str) -> None:
        if status != self._status:
            self._status = status
            self.statusChanged.emit()

    def _set_prompt(self, prompt: str) -> None:
        if prompt != self._prompt:
            self._prompt = prompt
            self.promptChanged.emit()
