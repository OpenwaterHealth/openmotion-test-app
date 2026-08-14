"""Procedures pane backend — runs operator procedures as subprocesses.

Design: a procedure is an external command (today: the SDK's WI-00015
calibration scripts) executed as a child process with its stdout piped into
the pane's terminal and its interactive prompts answered through a free-text
input that writes to the child's stdin. Running out of process keeps each
procedure byte-identical to what a headless bench run executes, and gives
Stop a hard boundary.

Adding a procedure means appending one registry entry in ``_build_procedures``
(name, program, args, working directory) — the pane itself is
procedure-agnostic: any script that prompts via ``input()`` and exits 0 on
pass / nonzero otherwise works unchanged.

Device handling: the child needs exclusive access to the console COM port and
the sensors' USB interfaces, so Start releases the app's device handles
(monitor stopped + explicit disconnects) and completion reacquires them. As a
safety backstop, after any stop/kill the console is reconnected and issued a
``stop_trigger`` — a killed child cannot run its own cleanup, and the laser
must never be left firing.

Prompt detection: ``input()`` prompts arrive on stdout without a trailing
newline. An unterminated tail that ends with ": " is flushed and shown
immediately; any other tail is flushed after a short quiet interval. Both arm
the operator input field.
"""

from __future__ import annotations

import csv
import getpass
import io
import logging
import os
import re
import subprocess
import sys
import threading
import time

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

from omotion.connection_state import ConnectionState

from motion_singleton import motion_interface

logger = logging.getLogger(__name__)

STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_PASS = "pass"
STATUS_FAIL = "fail"

MAX_LOG_LINES = 5000

# How long an unterminated stdout tail may sit before it is treated as an
# interactive prompt and shown. Ordinary output is line-buffered
# (PYTHONUNBUFFERED), so only prompts linger without a newline.
PROMPT_QUIET_MS = 400

# Non-verbose ("factory") mode shows only operator-facing lines: prompts,
# echoed answers, this controller's own markers, and outcome/status lines.
# The full log is always retained - the filter is display-only and applies
# retroactively when toggled.
_OPERATOR_LINE = re.compile(
    r"(?i)\b(pass|passed|fail|failed|error|warning|status|reason|category|"
    r"report|evidence|canceled|cancelled|confirm|ncr)\b"
)
_ALWAYS_SHOW_PREFIXES = ("===", "!", "[operator]", "procedure ")


def _display_line(line: str, verbose: bool) -> str | None:
    """What (if anything) to show for a raw log line."""
    if verbose:
        return line
    s = line.strip()
    if s.startswith(_ALWAYS_SHOW_PREFIXES) or _OPERATOR_LINE.search(s):
        return line
    return None


def _sdk_root() -> str | None:
    """Locate the openmotion-sdk checkout the procedure scripts run from.

    Resolution order: OPENMOTION_SDK_ROOT env var (a repo root directory),
    then the parent of the imported omotion package (works for PYTHONPATH /
    editable installs; wheels do not ship scripts/).
    """
    env = os.environ.get("OPENMOTION_SDK_ROOT")
    if env and os.path.isdir(env):
        return env
    try:
        import omotion
        return os.path.dirname(os.path.dirname(os.path.abspath(omotion.__file__)))
    except Exception:
        return None


def _sdk_module_procedure(name: str, module: str,
                          args: list[str] | None = None) -> dict:
    """Registry entry for a ``python -m`` procedure inside the SDK checkout.

    The module form with the checkout as working directory guarantees the
    checkout's ``omotion`` package is the one the procedure imports (see
    scripts/WI15_PROCEDURES.md in the SDK).
    """
    root = _sdk_root()
    module_file = None
    if root:
        candidate = os.path.join(root, *module.split(".")) + ".py"
        if os.path.isfile(candidate):
            module_file = candidate
    return {
        "name": name,
        "program": sys.executable,
        "args": ["-u", "-m", module, *(args or [])],
        "cwd": root,
        "missing": (
            None if module_file else
            f"{module}.py not found under the SDK checkout "
            f"({root or 'no checkout located'}) - set OPENMOTION_SDK_ROOT to "
            "an openmotion-sdk checkout that contains it"
        ),
    }


def _wifi_mac() -> str | None:
    """MAC address of this machine's Wi-Fi adapter — the bench/rig identity.

    ``getmac`` is used rather than ``netsh wlan`` because the latter is gated
    behind Windows Location permissions.
    """
    try:
        out = subprocess.run(
            ["getmac", "/v", "/fo", "csv"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        for row in csv.reader(io.StringIO(out)):
            if len(row) < 3:
                continue
            name = f"{row[0]} {row[1]}".lower()
            if "wi-fi" in name or "wireless" in name:
                mac = row[2].strip()
                if re.fullmatch(r"([0-9A-Fa-f]{2}-){5}[0-9A-Fa-f]{2}", mac):
                    return mac
    except Exception:
        pass
    return None


def _bench_identity_args() -> list[str]:
    """Prefill operator and rig identity so the scripts skip those prompts.

    Operator is the logged-in username; the rig/fixture ID is the Wi-Fi
    adapter's MAC address (stable per bench PC). Anything that cannot be
    determined is omitted, so the script prompts for it instead of recording
    a wrong value.
    """
    args: list[str] = []
    try:
        user = getpass.getuser().strip()
        if user:
            args += ["--operator", user]
    except Exception:
        pass
    mac = _wifi_mac()
    if mac:
        args += ["--fixture-id", mac]
    return args


def _build_procedures() -> list[dict]:
    """The procedure registry. Append entries here to add procedures."""
    identity = _bench_identity_args()
    return [
        _sdk_module_procedure(
            "WI-00015 Single-Sensor Laser Calibration",
            "scripts.wi15_single_sensor_laser_calibration",
            identity,
        ),
        _sdk_module_procedure(
            "WI-00015 Dual-Sensor Laser Calibration",
            "scripts.wi15_dual_sensor_laser_calibration",
            identity,
        ),
        _sdk_module_procedure(
            "WI-00015 Safety Calibration",
            "scripts.wi15_safety_calibration",
            identity,
        ),
        _sdk_module_procedure(
            "WI-00015 Measurement Calibration (one sensor)",
            "scripts.wi15_measurement_calibration",
            identity,
        ),
    ]


class ProceduresController(QObject):
    logLine = pyqtSignal(str, arguments=["line"])
    statusChanged = pyqtSignal()
    promptChanged = pyqtSignal()
    logCleared = pyqtSignal()
    verboseChanged = pyqtSignal()
    # Internal: device release finished on the worker thread; queued back to
    # the main thread, which owns QProcess creation.
    _releaseFinished = pyqtSignal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._status = STATUS_IDLE
        self._prompt = ""          # "" or "text"
        self._lines: list[str] = []
        self._linebuf = ""
        self._process: QProcess | None = None
        self._stop_requested = False
        self._current_index = 0
        self._settings = QSettings()
        self._verbose = self._settings.value(
            "procedures/verbose", True, type=bool)
        self._releaseFinished.connect(self._spawn_procedure)
        self._prompt_timer = QTimer(self)
        self._prompt_timer.setSingleShot(True)
        self._prompt_timer.setInterval(PROMPT_QUIET_MS)
        self._prompt_timer.timeout.connect(self._flush_prompt_tail)
        self._procedures = _build_procedures()

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
        out = []
        for l in self._lines:
            d = _display_line(l, self._verbose)
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
        if proc.get("missing"):
            self._log(f"! cannot start: {proc['missing']}")
            self._set_status(STATUS_FAIL)
            return

        self._lines.clear()
        self._linebuf = ""
        self.logCleared.emit()
        self._stop_requested = False
        self._log(f"=== {proc['name']} ===")
        self._set_status(STATUS_RUNNING)

        # Device release must NOT run on the Qt main thread: the SDK's USB
        # teardown happens on the ConnectionMonitor's thread via submitted
        # UserStop events, and doing it synchronously here crashed the app
        # (0xc0000005 in libusb teardown, 2026-08-06). Order matters too:
        # disconnect the handles while the monitor is still alive to process
        # the events, wait for each DISCONNECTED transition, and only then
        # stop the monitor. QProcess creation returns to the main thread via
        # _releaseFinished.
        threading.Thread(target=self._release_devices_worker,
                         args=(index,), daemon=True,
                         name="procedures-release").start()

    def _release_devices_worker(self, index: int) -> None:
        self._log("releasing device handles to the procedure ...")
        try:
            for name, handle in (("left", motion_interface.left),
                                 ("right", motion_interface.right),
                                 ("console", motion_interface.console)):
                try:
                    if handle.is_connected():
                        handle.request_disconnect()
                        if handle.wait_for(ConnectionState.DISCONNECTED,
                                           timeout=10.0):
                            self._log(f"  {name} released")
                        else:
                            self._log(f"! {name} did not confirm disconnect "
                                      f"within 10 s - continuing")
                except Exception as e:
                    self._log(f"! {name} release problem: {e}")
            motion_interface.stop()
            time.sleep(1.0)  # let USB re-enumeration settle for the child
        except Exception as e:
            self._log(f"! device release problem: {e}")
        self._releaseFinished.emit(index)

    def _spawn_procedure(self, index: int) -> None:
        proc = self._procedures[index]
        if self._stop_requested:
            self._log("start aborted before launch")
            self._set_status(STATUS_IDLE)
            self._reacquire_devices()
            return
        self._process = QProcess(self)
        self._process.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        # The child's pipes must be UTF-8; the Windows default (cp1252) can
        # crash a child print that carries non-ASCII output.
        env.insert("PYTHONUTF8", "1")
        # The procedure's checkout wins the import race: its root goes ahead
        # of any inherited PYTHONPATH (the working directory itself is first
        # on sys.path for -m children).
        if proc.get("cwd"):
            self._process.setWorkingDirectory(proc["cwd"])
            prev = env.value("PYTHONPATH", "")
            env.insert("PYTHONPATH",
                       proc["cwd"] + (os.pathsep + prev if prev else ""))
        self._process.setProcessEnvironment(env)
        self._process.readyReadStandardOutput.connect(self._on_stdout)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)

        self._log(f"launching: {proc['program']} {' '.join(proc['args'])}")
        if proc.get("cwd"):
            self._log(f"working directory: {proc['cwd']}")
        self._set_status(STATUS_RUNNING)
        self._process.start(proc["program"], proc["args"])

    @pyqtSlot()
    def stopProcedure(self) -> None:
        if self._process is None:
            # May be pressed during the release phase, before the child
            # exists - flag it so _spawn_procedure aborts instead of launching.
            if self._status == STATUS_RUNNING:
                self._stop_requested = True
                self._log("! stop requested - aborting before launch")
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
        # input() prompts arrive without a trailing newline. A tail ending
        # with ": " is certainly a prompt - flush it at once. Anything else
        # unterminated is flushed after a quiet interval, so unusual prompt
        # shapes still surface instead of leaving the pane looking hung.
        self._prompt_timer.stop()
        if not self._linebuf:
            return
        if self._linebuf.endswith(": "):
            self._flush_prompt_tail()
        else:
            self._prompt_timer.start()

    def _flush_prompt_tail(self) -> None:
        if not self._linebuf or self._process is None:
            return
        self._emit_line(self._linebuf, force_show=True)
        self._linebuf = ""
        self._set_prompt("text")

    def _emit_line(self, line: str, force_show: bool = False) -> None:
        self._lines.append(line)
        if len(self._lines) > MAX_LOG_LINES:
            del self._lines[: len(self._lines) - MAX_LOG_LINES]
        # The stored log is always complete; only the display stream is
        # gated by the Verbose checkbox. Prompts are always shown.
        shown = line if force_show else _display_line(line, self._verbose)
        if shown is not None:
            self.logLine.emit(shown)

    def _log(self, line: str) -> None:
        self._emit_line(line)

    def _on_error(self, err) -> None:
        self._log(f"! process error: {err}")

    def _on_finished(self, exit_code: int, exit_status) -> None:
        self._prompt_timer.stop()
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
