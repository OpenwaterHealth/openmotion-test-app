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

Where the child comes from: **the app runs only the procedures in the omotion
it was built with, and cannot be pointed at another copy.** A released build
re-executes itself (``--run-procedure``, see ``utils/procedure_runner.py``)
and imports from its own bundle; a source run launches ``python -m <module>``
under the app's own interpreter. Nothing on the bench — no env var, no
PYTHONPATH, no stray checkout — can substitute different procedure code into
a release, so the app version pins the procedure version for the evidence a
run produces. To run a different copy, run it from the SDK directly.

Device handling: the child needs exclusive access to the console COM port and
the sensors' USB interfaces, so Start releases the app's device handles
(monitor stopped + explicit disconnects) and completion reacquires them. As a
safety backstop, after any stop/kill the console is reconnected and issued a
``stop_trigger`` — a killed child cannot run its own cleanup, and the laser
must never be left firing.

Prompt detection: when the app's omotion ships ``omotion.scripts.
framed_prompts`` (SDK #241), procedures are launched through it, so every
``input()`` prompt arrives as one complete sentinel-tagged JSON line and is
recognized exactly; an unterminated stdout tail is then just partial output,
flushed after a quiet interval without arming the operator input. With an
older omotion the pane falls back to heuristics: ``input()`` prompts arrive
on stdout without a trailing newline, so an unterminated tail ending with
": " is flushed and shown immediately, and any other tail is flushed after a
short quiet interval - both arming the input field. Either way, a prompt
ending with a small option group - ``(left/right)``, ``(yes/no)``,
``[y/N]``, ``(single-left/single-right/dual)`` - additionally renders one
answer button per option; clicking sends that option to the child's stdin
verbatim.

Audit logging: every line the pane terminal records - child output, operator
answers (button presses and typed sends both funnel through
``answerPrompt``), pane status messages, and the final PASS/FAIL verdict -
is also mirrored to the application log via this module's logger, regardless
of the Verbose display filter. Pane opening and procedure selection are
logged too, so the app's session logfile carries a complete audit trail of
procedure activity.
"""

from __future__ import annotations

import csv
import getpass
import importlib.util
import io
import json
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
from utils.procedure_runner import RUN_PROCEDURE_FLAG

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

# Non-verbose ("factory") mode hides only log-record-shaped noise (SDK/library
# logging that leaks onto the child's stderr); every other child line is
# operator output and must stay visible - a hidden instruction once left an
# operator waiting on a power-cycle prompt that never appeared. The full log
# is always retained - the filter is display-only and applies retroactively
# when toggled.
_LOG_RECORD_LINE = re.compile(
    r"^\d{4}-\d{2}-\d{2} [\d:,.]+ - \S+ - (DEBUG|INFO|WARNING|ERROR|CRITICAL)"
    r"|^(DEBUG|INFO|WARNING|ERROR|CRITICAL)[ :]"
)


def _display_line(line: str, verbose: bool) -> str | None:
    """What (if anything) to show for a raw log line."""
    if verbose:
        return line
    s = line.strip()
    if not s or _LOG_RECORD_LINE.match(s):
        return None
    return line


# A prompt whose trailing group is a short slash-separated list - e.g.
# "(left/right): ", "[y/N]: ", "(single-left/single-right/dual): " - gets
# one answer button per option in addition to the free-text field.
_OPTION_GROUP = re.compile(r"[(\[]([^()\[\]]+)[)\]]\s*:?\s*$")


def _prompt_options(prompt: str) -> list[str]:
    match = _OPTION_GROUP.search(prompt.strip())
    if match is None:
        return []
    options = [part.strip() for part in match.group(1).split("/")]
    if not 2 <= len(options) <= 4:
        return []
    if any(not option or len(option) > 20 or " " in option
           for option in options):
        return []
    return options


# Where procedure evidence lands on this bench: a stable per-user location,
# independent of any checkout. Each run prints its exact evidence paths.
_PROCEDURE_OUTPUT_ROOT = os.path.join(
    os.path.expanduser("~"), "Documents", "OpenMotion")


# Name suffix of the console-subsystem exe built alongside the windowed one
# (openwater.spec: APP_NAME vs APP_NAME + this).
_CONSOLE_SUFFIX = "_console"


def _bundled_procedure_runner() -> str | None:
    """This frozen build re-executed as a procedure runner, or None.

    A PyInstaller bundle ships no ``python.exe`` - ``sys.executable`` is the
    app - so the child the pane needs can only be the app itself, told to be
    a runner rather than the GUI. That is all ``--run-procedure`` is: the
    signal that distinguishes the two (see ``utils/procedure_runner.py``).
    The procedure code, the omotion it drives and the Python running both
    then come from the release, with nothing installed on the bench.
    """
    if not getattr(sys, "frozen", False):
        return None
    stem, ext = os.path.splitext(sys.executable)
    # The build ships a windowed exe and a console-subsystem twin. Prefer the
    # twin: its stdio is a plain console stream, which is what the pane's
    # pipes expect, whereas the windowed exe starts with its streams detached.
    twin = f"{stem}{_CONSOLE_SUFFIX}{ext}"
    if not stem.endswith(_CONSOLE_SUFFIX) and os.path.isfile(twin):
        return twin
    return sys.executable


def _has_module(module: str) -> bool:
    """Can the child import ``module``? Answered from this process.

    Sound for both launch shapes, because both give the child exactly this
    process's imports: a release re-executes its own bundle, and a source run
    spawns this same interpreter, which inherits this ``sys.path`` through the
    environment. So no probe subprocess is needed - and none is possible in a
    release, where there is no other interpreter to probe.
    """
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def _omotion_location() -> str:
    try:
        import omotion
        return os.path.dirname(os.path.abspath(omotion.__file__))
    except Exception:
        return "unavailable"


# Host-facing runner shipped with the procedures (SDK #241): executes a
# procedure module with every ``input()`` prompt announced as one complete
# sentinel-tagged JSON stdout line, so the pane recognizes prompts exactly
# instead of sniffing unterminated output tails.
_FRAMED_RUNNER = "omotion.scripts.framed_prompts"


def _framed_sentinel() -> str | None:
    """The prompt-frame sentinel, if this omotion ships the framed runner.

    The import doubles as the availability probe, on the same argument as
    ``_has_module``: both launch shapes give the child exactly this process's
    imports, so what this process can import is what the child will run.
    """
    try:
        from omotion.scripts.framed_prompts import PROMPT_SENTINEL
    except ImportError:
        return None
    return PROMPT_SENTINEL


def _framed_prompt(line: str, sentinel: str | None) -> str | None:
    """The prompt text if ``line`` is a well-formed prompt frame, else None."""
    if not sentinel or not line.startswith(sentinel):
        return None
    try:
        prompt = json.loads(line[len(sentinel):])["prompt"]
    except (ValueError, TypeError, KeyError):
        return None
    return prompt if isinstance(prompt, str) else None


def _sdk_module_procedure(name: str, module: str,
                          args: list[str] | None = None) -> dict:
    """Registry entry for an SDK procedure module.

    The procedures ship inside the ``omotion`` package (``omotion.scripts``,
    see docs/WI15Procedures.md in the SDK), and the app runs **only** the copy
    it was built with:

    * **Released build** - re-executes itself as a runner, importing from its
      own bundle. Nothing on the bench can redirect it.
    * **From source** - ``python -m <module>`` under the app's own
      interpreter, against the omotion that interpreter imports.

    There is deliberately no override. A release that could be pointed at some
    other checkout's procedures would produce calibration evidence whose
    provenance the app cannot vouch for; to run a different copy, run it from
    the SDK directly.

    When the omotion also ships the framed runner (``_FRAMED_RUNNER``), the
    module is launched through it so prompts arrive framed; otherwise the
    module is launched directly and the pane falls back to prompt heuristics.
    The entry's ``sentinel`` records which (the frame marker, or None).
    """
    runner = _bundled_procedure_runner()
    sentinel = _framed_sentinel()
    target = [_FRAMED_RUNNER, module] if sentinel else [module]
    if runner is not None:
        program, argv = runner, [RUN_PROCEDURE_FLAG, *target]
        source = "bundled with this build of the app"
        remedy = "install a release built against an SDK that ships it"
    else:
        program, argv = sys.executable, ["-u", "-m", *target]
        source = _omotion_location()
        remedy = (f"install an openmotion-sdk that ships {module} into "
                  f"{program}")
    return {
        "name": name,
        "program": program,
        "args": [*argv, *(args or [])],
        "cwd": _PROCEDURE_OUTPUT_ROOT,
        "source": source,
        "sentinel": sentinel,
        "missing": (None if _has_module(module)
                    else f"{module} is not available - {remedy}"),
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
    common = [
        *_bench_identity_args(),
        "--output-dir", os.path.join(_PROCEDURE_OUTPUT_ROOT, "wi15_out"),
    ]
    return [
        _sdk_module_procedure(
            "WI-00015 Single-Sensor Laser Calibration",
            "omotion.scripts.wi15_single_sensor_laser_calibration",
            common,
        ),
        _sdk_module_procedure(
            "WI-00015 Dual-Sensor Laser Calibration",
            "omotion.scripts.wi15_dual_sensor_laser_calibration",
            common,
        ),
        _sdk_module_procedure(
            "WI-00015 Safety Calibration",
            "omotion.scripts.wi15_safety_calibration",
            common,
        ),
        _sdk_module_procedure(
            "WI-00015 Measurement Calibration (one sensor)",
            "omotion.scripts.wi15_measurement_calibration",
            common,
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
        self._prompt_options: list[str] = []
        self._lines: list[str] = []
        self._linebuf = ""
        self._process: QProcess | None = None
        self._sentinel: str | None = None  # the running child's frame marker
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

    @pyqtProperty("QVariantList", notify=promptChanged)
    def promptOptions(self):
        return list(self._prompt_options)

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
    @pyqtSlot()
    def paneOpened(self) -> None:
        """QML calls this when the Procedures page is instantiated."""
        logger.info("Procedures pane opened")

    @pyqtSlot(int)
    def selectProcedure(self, index: int) -> None:
        """Changing procedure resets the status indicator (and prompt)."""
        if self._status == STATUS_RUNNING:
            return  # UI disables the combo while running; belt and braces
        if 0 <= index < len(self._procedures):
            logger.info(
                "procedure selected: %s", self._procedures[index]["name"]
            )
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
        # The working directory is the evidence root, not a code location -
        # the procedure modules ship inside omotion itself.
        if proc.get("cwd"):
            try:
                os.makedirs(proc["cwd"], exist_ok=True)
            except OSError as e:
                self._log(f"! cannot create {proc['cwd']}: {e}")
            self._process.setWorkingDirectory(proc["cwd"])
        # Nothing is added to the child's import path: it imports what this
        # process imports, by construction. A frozen child could not be
        # steered anyway - PyInstaller starts it isolated, so it ignores
        # PYTHONPATH (and PYTHONUNBUFFERED/PYTHONUTF8 above, which is why the
        # runner reconfigures its own streams).
        self._process.setProcessEnvironment(env)
        self._process.readyReadStandardOutput.connect(self._on_stdout)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)

        self._sentinel = proc.get("sentinel")
        self._log(f"launching: {proc['program']} {' '.join(proc['args'])}")
        self._log("procedure source: "
                  + (proc.get("source") or "interpreter's installed package"))
        self._log("prompt framing: "
                  + ("structured" if self._sentinel
                     else "heuristic (this omotion has no framed runner)"))
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
            line = line.rstrip("\r")
            prompt = _framed_prompt(line, self._sentinel)
            if prompt is not None:
                self._show_prompt(prompt)
            else:
                self._emit_line(line)
        # Heuristic children: input() prompts arrive without a trailing
        # newline, so a tail ending with ": " is certainly a prompt - flush
        # it at once - and anything else unterminated is flushed after a
        # quiet interval, so unusual prompt shapes still surface instead of
        # leaving the pane looking hung. A framed child's prompts are
        # complete frame lines, never tails, so its quiet flush is display
        # only and must not arm the operator input.
        self._prompt_timer.stop()
        if not self._linebuf:
            return
        if self._sentinel is None and self._linebuf.endswith(": "):
            self._flush_prompt_tail()
        else:
            self._prompt_timer.start()

    def _flush_prompt_tail(self) -> None:
        if not self._linebuf or self._process is None:
            return
        tail = self._linebuf
        self._linebuf = ""
        if self._sentinel is not None:
            self._emit_line(tail)  # partial output of a framed child
            return
        self._show_prompt(tail)

    def _show_prompt(self, prompt: str) -> None:
        self._emit_line(prompt, force_show=True)
        self._set_prompt("text", options=_prompt_options(prompt))

    def _emit_line(self, line: str, force_show: bool = False) -> None:
        self._lines.append(line)
        if len(self._lines) > MAX_LOG_LINES:
            del self._lines[: len(self._lines) - MAX_LOG_LINES]
        # Mirror every terminal line into the application log (audit trail:
        # child output, operator answers, status, final verdict). The
        # Verbose checkbox gates only the on-screen stream, never the log.
        logger.info("%s", line)
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

    def _set_prompt(self, prompt: str, options: list[str] | None = None) -> None:
        options = options or []
        if prompt != self._prompt or options != self._prompt_options:
            self._prompt = prompt
            self._prompt_options = options
            self.promptChanged.emit()
