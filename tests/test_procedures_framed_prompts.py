"""Tests for the Procedures pane's structured prompt framing (issue #105).

With an omotion that ships ``omotion.scripts.framed_prompts`` (SDK #241),
procedures launch through the framed runner and every prompt arrives as one
sentinel-tagged JSON line, recognized exactly; the tail heuristics then never
arm the operator input. Without it, launch shape and heuristics stay exactly
as before.

The end-to-end test drives a real ``QProcess`` child — a hardware-free stub
that frames prompts the way the SDK runner does — through the controller's
actual pipe -> parse -> prompt -> answer -> exit path. No bench hardware is
touched anywhere here.
"""

import json
import os
import sys
import time

import pytest
from PyQt6.QtCore import QCoreApplication, QProcess

import procedures_controller as pc


SENTINEL = "@OW-PROMPT@ "
MODULE = "omotion.scripts.wi15_safety_calibration"
STUB = os.path.join(os.path.dirname(__file__), "_framed_stub_child.py")


# ------------------------------------------------------------ launch shape

def test_source_launch_interposes_the_framed_runner(monkeypatch):
    monkeypatch.delattr(pc.sys, "frozen", raising=False)
    monkeypatch.setattr(pc, "_has_module", lambda module: True)
    monkeypatch.setattr(pc, "_framed_sentinel", lambda: SENTINEL)

    entry = pc._sdk_module_procedure("name", MODULE)

    assert entry["program"] == sys.executable
    assert entry["args"][:4] == ["-u", "-m", pc._FRAMED_RUNNER, MODULE]
    assert entry["sentinel"] == SENTINEL
    assert entry["missing"] is None


def test_frozen_launch_interposes_the_framed_runner(tmp_path, monkeypatch):
    exe = tmp_path / "TestApp.exe"
    exe.write_text("", encoding="ascii")
    monkeypatch.setattr(pc.sys, "frozen", True, raising=False)
    monkeypatch.setattr(pc.sys, "executable", str(exe))
    monkeypatch.setattr(pc, "_has_module", lambda module: True)
    monkeypatch.setattr(pc, "_framed_sentinel", lambda: SENTINEL)

    entry = pc._sdk_module_procedure("name", MODULE, ["--output-dir", "out"])

    assert entry["args"] == [
        pc.RUN_PROCEDURE_FLAG, pc._FRAMED_RUNNER, MODULE,
        "--output-dir", "out",
    ]
    assert entry["sentinel"] == SENTINEL


def test_older_omotion_without_the_runner_launches_directly(monkeypatch):
    """The fallback: today's direct launch shape, heuristics stay in force."""
    monkeypatch.delattr(pc.sys, "frozen", raising=False)
    monkeypatch.setattr(pc, "_has_module", lambda module: True)
    monkeypatch.setattr(pc, "_framed_sentinel", lambda: None)

    entry = pc._sdk_module_procedure("name", MODULE)

    assert entry["args"][:3] == ["-u", "-m", MODULE]
    assert entry["sentinel"] is None


# ---------------------------------------------------------- frame parsing

def test_frame_parsing_accepts_only_wellformed_frames():
    line = SENTINEL + json.dumps({"prompt": "Fixture ID: "})

    assert pc._framed_prompt(line, SENTINEL) == "Fixture ID: "
    assert pc._framed_prompt(line, None) is None  # unframed child
    assert pc._framed_prompt("Fixture ID: ", SENTINEL) is None
    assert pc._framed_prompt(SENTINEL + "not json", SENTINEL) is None
    assert pc._framed_prompt(SENTINEL + '{"other": 1}', SENTINEL) is None
    assert pc._framed_prompt(SENTINEL + '{"prompt": 3}', SENTINEL) is None


def test_frame_parsing_is_lossless_for_awkward_text():
    prompt = 'Confirm "läser" path C:\\x?\n(yes/no): '
    line = SENTINEL + json.dumps({"prompt": prompt})

    assert pc._framed_prompt(line, SENTINEL) == prompt


# ----------------------------------------------- stdout handling, no child

class _FakeProcess:
    """Just enough QProcess for _on_stdout: hand back fed bytes once."""

    def __init__(self):
        self._pending = b""

    def feed(self, data: bytes):
        self._pending = data

    def readAllStandardOutput(self):
        data, self._pending = self._pending, b""
        return data


@pytest.fixture
def controller(monkeypatch):
    QCoreApplication.instance() or QCoreApplication([])
    monkeypatch.setattr(pc, "_build_procedures", lambda: [])
    return pc.ProceduresController()


def _feed(controller, fake, data: bytes):
    fake.feed(data)
    controller._on_stdout()


def test_framed_prompts_arm_exactly_and_tails_never_do(controller):
    fake = _FakeProcess()
    controller._process = fake
    controller._sentinel = SENTINEL

    _feed(controller, fake, b"starting\n")
    assert controller.promptType == ""

    frame = SENTINEL + json.dumps({"prompt": "Operator: "})
    _feed(controller, fake, frame.encode("utf-8") + b"\n")
    assert controller.promptType == "text"
    assert "Operator: " in controller.fullLog
    # the raw frame line itself never reaches the terminal
    assert SENTINEL not in controller.fullLog

    controller._set_prompt("")
    # A prompt-shaped unterminated tail must NOT arm on a framed child —
    # not immediately, and not when the quiet timer flushes it (simulated
    # by calling the flush directly).
    _feed(controller, fake, b"loading calibration table: ")
    assert controller.promptType == ""
    controller._flush_prompt_tail()
    assert controller.promptType == ""
    assert "loading calibration table: " in controller.fullLog


def test_framed_prompt_with_option_group_renders_buttons(controller):
    fake = _FakeProcess()
    controller._process = fake
    controller._sentinel = SENTINEL

    frame = SENTINEL + json.dumps(
        {"prompt": "Which sensor is installed? (left/right): "})
    _feed(controller, fake, frame.encode("utf-8") + b"\n")

    assert controller.promptType == "text"
    assert controller.promptOptions == ["left", "right"]


def test_heuristic_child_keeps_todays_tail_behavior(controller):
    fake = _FakeProcess()
    controller._process = fake
    controller._sentinel = None

    _feed(controller, fake, b"Operator: ")

    assert controller.promptType == "text"  # ": " tail arms immediately


# ------------------------------------------------------------- end to end

def test_end_to_end_framed_child_pipe_to_prompt_to_answer(monkeypatch):
    """A real QProcess stub child: frames arm the input, answers reach the
    child's stdin, the held-open prompt-shaped tail never arms, and the
    verdict lands as PASS.

    Driven through QProcess's blocking ``waitFor*`` API instead of an event
    loop: under pytest on this Windows stack, a ``QCoreApplication`` exec
    loop stops dispatching events once this controller's child starts
    streaming (environment quirk — the identical code passes standalone,
    and the real app runs a GUI event loop, where the pane drives real
    procedures). ``waitForReadyRead``/``waitForFinished`` emit the same
    signals synchronously, so the controller code under test is identical;
    only the pump differs. The quiet-timer flush cannot fire without a
    loop, so the pump calls ``_flush_prompt_tail`` directly — its framed
    no-arm behavior has its own unit test above.
    """
    QCoreApplication.instance() or QCoreApplication([])
    monkeypatch.setattr(pc, "_build_procedures", lambda: [{
        "name": "Framed Stub",
        "program": sys.executable,
        "args": ["-u", STUB],
        "cwd": None,
        "sentinel": SENTINEL,
        "missing": None,
    }])
    controller = pc.ProceduresController()
    monkeypatch.setattr(controller, "_reacquire_devices", lambda: None)

    answers = iter(["left", "yes"])
    armed = []

    def on_prompt_changed():
        if controller.promptType == "text":
            armed.append(list(controller.promptOptions))

    controller.promptChanged.connect(on_prompt_changed)
    controller._spawn_procedure(0)
    process = controller._process
    assert process is not None and process.waitForStarted(5000)

    try:
        deadline = time.monotonic() + 20
        while (controller.status == pc.STATUS_RUNNING
               and time.monotonic() < deadline):
            if process.state() == QProcess.ProcessState.NotRunning:
                # deliver the queued finished() -> _on_finished
                process.waitForFinished(2000)
                continue
            process.waitForReadyRead(100)  # readyRead -> _on_stdout, sync
            if controller._linebuf:
                controller._flush_prompt_tail()  # stands in for the timer
            if controller.promptType == "text":
                controller.answerPrompt(next(answers, "unexpected-arm"))
                process.waitForBytesWritten(1000)
    finally:
        if controller._process is not None:
            controller._process.kill()
            controller._process.waitForFinished(2000)

    assert controller.status == pc.STATUS_PASS, controller.fullLog
    # exactly the two real prompts armed, each with its option buttons —
    # the held-open tail armed nothing
    assert armed == [["left", "right"], ["yes", "no"]]
    assert "side recorded: left" in controller.fullLog
    assert "answer recorded: yes" in controller.fullLog
    # the tail was still flushed for visibility
    assert "loading calibration table: " in controller.fullLog
    assert SENTINEL not in controller.fullLog
