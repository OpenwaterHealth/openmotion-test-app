"""Unit tests for the Procedures pane's launch resolution.

Run from the repo root with:  python -m pytest tests/test_procedures_launcher.py
(`python -m pytest` puts the repo root on sys.path so the controller imports.)

Covers only the pure resolution pieces - where the child procedure comes
from. A released build runs ``omotion.scripts.*`` out of its own bundle by
re-executing itself; a source run (or an explicit OPENMOTION_SDK_ROOT
override) launches ``python -m <module>`` against a checkout or an installed
wheel. No Qt objects are constructed.
"""

import logging
import os
import sys

import procedures_controller as pc
from utils import procedure_runner


MODULE = "omotion.scripts.wi15_safety_calibration"


def _sdk_checkout(tmp_path):
    root = tmp_path / "sdk"
    (root / "omotion" / "scripts").mkdir(parents=True)
    (root / "omotion" / "scripts" / "wi15_safety_calibration.py").write_text(
        "", encoding="ascii")
    return root


def test_env_checkout_override_wins_the_import_race(tmp_path, monkeypatch):
    root = _sdk_checkout(tmp_path)
    monkeypatch.setenv("OPENMOTION_SDK_ROOT", str(root))
    monkeypatch.setattr(pc, "_python_interpreter", lambda: "python-x")

    entry = pc._sdk_module_procedure("name", MODULE)

    # The env override outranks the app's own omotion source (also valid in
    # a dev tree), and the working directory is the evidence root - the
    # checkout is only an import source now.
    assert entry["import_root"] == str(root)
    assert entry["missing"] is None
    assert entry["args"][:3] == ["-u", "-m", MODULE]
    assert entry["cwd"] == pc._PROCEDURE_OUTPUT_ROOT


def test_installed_wheel_alone_is_sufficient(monkeypatch):
    monkeypatch.setattr(pc, "_candidate_import_roots", lambda: [])
    monkeypatch.setattr(pc, "_python_interpreter", lambda: "python-x")
    monkeypatch.setitem(pc._probe_cache, "python-x", True)

    entry = pc._sdk_module_procedure("name", MODULE)

    assert entry["missing"] is None
    assert entry["import_root"] is None


def test_unimportable_procedures_report_both_remedies(monkeypatch):
    monkeypatch.setattr(pc, "_candidate_import_roots", lambda: [])
    monkeypatch.setattr(pc, "_python_interpreter", lambda: "python-x")
    monkeypatch.setitem(pc._probe_cache, "python-x", False)

    entry = pc._sdk_module_procedure("name", MODULE)

    assert "omotion.scripts" in entry["missing"]
    assert "OPENMOTION_SDK_ROOT" in entry["missing"]


def test_frozen_app_never_offers_its_own_bundle_as_an_import_root(monkeypatch):
    """The _internal bundle ships the app Python's stdlib .pyds; putting it
    on another interpreter's PYTHONPATH shadows that interpreter's stdlib
    (QA bench, 2026-08-14: python313.dll conflict inside a 3.14 child)."""
    monkeypatch.delenv("OPENMOTION_SDK_ROOT", raising=False)
    monkeypatch.setattr(pc.sys, "frozen", True, raising=False)

    assert pc._candidate_import_roots() == []


def test_env_root_without_the_module_is_not_a_candidate(tmp_path, monkeypatch):
    (tmp_path / "omotion").mkdir()  # a checkout too old to have the module
    monkeypatch.setenv("OPENMOTION_SDK_ROOT", str(tmp_path))
    monkeypatch.setattr(pc, "_python_interpreter", lambda: "python-x")
    monkeypatch.setitem(pc._probe_cache, "python-x", False)

    entry = pc._sdk_module_procedure("name", "omotion.scripts.not_there")

    assert entry["import_root"] != str(tmp_path)
    assert entry["missing"] is not None


def _frozen_build(tmp_path, monkeypatch, *, console_twin=False):
    """A released layout: the windowed exe, optionally with its console twin."""
    monkeypatch.delenv("OPENMOTION_SDK_ROOT", raising=False)
    exe = tmp_path / "TestApp.exe"
    exe.write_text("", encoding="ascii")
    if console_twin:
        (tmp_path / "TestApp_console.exe").write_text("", encoding="ascii")
    monkeypatch.setattr(pc.sys, "frozen", True, raising=False)
    monkeypatch.setattr(pc.sys, "executable", str(exe))
    return exe


def test_release_runs_the_procedure_out_of_its_own_bundle(tmp_path, monkeypatch):
    """No Python and no SDK on the bench: the app is its own runner."""
    exe = _frozen_build(tmp_path, monkeypatch)
    monkeypatch.setattr(pc, "_bundle_has_module", lambda module: True)

    entry = pc._sdk_module_procedure("name", MODULE, ["--output-dir", "out"])

    assert entry["program"] == str(exe)
    assert entry["args"] == [
        pc.RUN_PROCEDURE_FLAG, MODULE, "--output-dir", "out"]
    assert entry["missing"] is None
    # Nothing may go onto the child's PYTHONPATH - it imports from the bundle.
    assert entry["import_root"] is None


def test_release_prefers_the_console_twin_for_working_child_stdio(
        tmp_path, monkeypatch):
    """The windowed exe starts with its streams detached; the twin does not."""
    _frozen_build(tmp_path, monkeypatch, console_twin=True)
    monkeypatch.setattr(pc, "_bundle_has_module", lambda module: True)

    entry = pc._sdk_module_procedure("name", MODULE)

    assert entry["program"] == str(tmp_path / "TestApp_console.exe")


def test_release_without_the_procedure_bundled_says_so(tmp_path, monkeypatch):
    """Built against an SDK predating the procedure - not a bench misconfig."""
    _frozen_build(tmp_path, monkeypatch)
    monkeypatch.setattr(pc, "_bundle_has_module", lambda module: False)

    entry = pc._sdk_module_procedure("name", MODULE)

    assert "not bundled in this build" in entry["missing"]
    assert "OPENMOTION_SDK_ROOT" in entry["missing"]


def test_release_checkout_override_opts_out_of_the_bundle(tmp_path, monkeypatch):
    """OPENMOTION_SDK_ROOT stays the escape hatch to a newer procedure."""
    root = _sdk_checkout(tmp_path)
    _frozen_build(tmp_path, monkeypatch, console_twin=True)
    monkeypatch.setenv("OPENMOTION_SDK_ROOT", str(root))
    monkeypatch.setattr(pc, "_python_interpreter", lambda: "python-x")

    entry = pc._sdk_module_procedure("name", MODULE)

    assert entry["program"] == "python-x"
    assert entry["import_root"] == str(root)
    assert entry["args"][:3] == ["-u", "-m", MODULE]


def test_source_run_never_uses_a_bundled_runner(monkeypatch):
    monkeypatch.delattr(pc.sys, "frozen", raising=False)

    assert pc._bundled_procedure_runner() is None


# ------------------------------------------------------- the runner itself

def _demo_procedure(tmp_path, monkeypatch, package, body):
    """A throwaway procedure package. Each test needs its own name: an
    imported package stays in sys.modules and would shadow the next one."""
    root = tmp_path / package
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="ascii")
    (root / "proc.py").write_text(body, encoding="ascii")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["TestApp.exe"])
    return f"{package}.proc"


def test_runner_runs_the_module_as_main_and_returns_its_exit_code(
        tmp_path, monkeypatch, capsys):
    module = _demo_procedure(tmp_path, monkeypatch, "demo_exit_code", (
        "import sys\n"
        "assert __name__ == '__main__', __name__\n"
        "print('argv:', sys.argv[1:])\n"
        "sys.exit(3)\n"
    ))

    code = procedure_runner.run_procedure([module, "--operator", "op"])

    assert code == 3
    # argv[0] is the module, so the procedure's own argparse sees its flags.
    assert "argv: ['--operator', 'op']" in capsys.readouterr().out


def test_runner_reports_success_as_zero(tmp_path, monkeypatch):
    module = _demo_procedure(tmp_path, monkeypatch, "demo_pass",
                             "print('done')\n")

    assert procedure_runner.run_procedure([module]) == 0


def test_runner_reports_a_missing_procedure_instead_of_crashing(
        monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["TestApp.exe"])

    code = procedure_runner.run_procedure(["omotion.scripts.not_a_procedure"])

    assert code == 2
    assert "not available in this build" in capsys.readouterr().err


def test_runner_prints_a_failing_procedures_traceback_rather_than_raising(
        tmp_path, monkeypatch, capsys):
    """An escaping exception in a windowed build can raise a modal dialog no
    operator is there to dismiss; the pane wants the traceback instead."""
    module = _demo_procedure(tmp_path, monkeypatch, "demo_boom",
                             "raise RuntimeError('laser did not settle')\n")

    code = procedure_runner.run_procedure([module])

    assert code == 1
    assert "laser did not settle" in capsys.readouterr().err


def test_runner_leaves_a_normal_launch_alone(monkeypatch):
    """Without the sentinel, maybe_run_procedure must not exit the app."""
    monkeypatch.setattr(sys, "argv", ["TestApp.exe", "--debug"])

    assert procedure_runner.maybe_run_procedure() is None


def test_pane_terminal_lines_are_mirrored_to_the_app_log(monkeypatch, caplog):
    """Audit trail: everything the pane terminal records reaches the app log
    (child output, operator answers, final verdict), plus pane-open and
    procedure-selection events."""
    from PyQt6.QtCore import QCoreApplication

    QCoreApplication.instance() or QCoreApplication([])
    monkeypatch.setattr(
        pc, "_build_procedures", lambda: [{"name": "Demo Procedure"}]
    )
    controller = pc.ProceduresController()

    with caplog.at_level(logging.INFO, logger="procedures_controller"):
        controller.paneOpened()
        controller.selectProcedure(0)
        controller._emit_line("=== Demo Procedure ===")
        controller._emit_line("[operator] left")
        controller._emit_line(
            "2026-08-14 12:00:00,000 - openmotion - INFO - hidden on screen"
        )
        controller._emit_line("procedure completed: PASS")

    assert "Procedures pane opened" in caplog.text
    assert "procedure selected: Demo Procedure" in caplog.text
    assert "=== Demo Procedure ===" in caplog.text
    assert "[operator] left" in caplog.text
    # The Verbose display filter must not gate the audit log.
    assert "hidden on screen" in caplog.text
    assert "procedure completed: PASS" in caplog.text


def test_registry_uses_package_modules_and_shared_output_dir(monkeypatch):
    monkeypatch.setattr(pc, "_bench_identity_args",
                        lambda: ["--operator", "op"])
    monkeypatch.setattr(pc, "_python_interpreter", lambda: "python-x")
    monkeypatch.setattr(pc, "_candidate_import_roots", lambda: [])
    monkeypatch.setitem(pc._probe_cache, "python-x", True)

    entries = pc._build_procedures()

    assert [e["args"][2] for e in entries] == [
        "omotion.scripts.wi15_single_sensor_laser_calibration",
        "omotion.scripts.wi15_dual_sensor_laser_calibration",
        "omotion.scripts.wi15_safety_calibration",
        "omotion.scripts.wi15_measurement_calibration",
    ]
    for entry in entries:
        i = entry["args"].index("--output-dir")
        assert entry["args"][i + 1].endswith(
            os.path.join("OpenMotion", "wi15_out"))
