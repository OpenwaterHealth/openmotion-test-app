"""Unit tests for the Procedures pane's launch resolution.

Run from the repo root with:  python -m pytest tests/test_procedures_launcher.py
(`python -m pytest` puts the repo root on sys.path so the controller imports.)

Covers only the pure resolution pieces - the procedures are ``python -m
omotion.scripts.*`` package modules, so launching needs an interpreter plus
an importable ``omotion`` that carries them, with an optional checkout
override winning the import race. No Qt objects are constructed.
"""

import logging
import os

import procedures_controller as pc


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
