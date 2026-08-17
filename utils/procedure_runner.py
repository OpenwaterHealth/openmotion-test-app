"""Runs a bundled procedure module inside the app's own interpreter.

A released (PyInstaller) build ships the procedure modules, the ``omotion``
package they drive, and the Python that runs both. Re-executing the app exe
with ``--run-procedure <module> [args...]`` turns it into a plain runner for
that module, so a factory bench needs no separate Python install and no SDK
checkout: everything the procedure touches comes from the release itself,
which also means the app version pins the procedure version for evidence.

This module must stay import-cheap — ``main`` imports it before PyQt and
before ``motion_singleton``. Constructing the SDK interface in the child
would grab the very device handles the parent app just released for it.
"""

from __future__ import annotations

import sys

# argv sentinel. `procedures_controller` builds the child command line with
# the same constant; `main` consumes it before any other startup work.
RUN_PROCEDURE_FLAG = "--run-procedure"


def _prepare_streams() -> None:
    """Make stdout/stderr usable as a procedure's operator console.

    UTF-8 because procedure output carries non-ASCII (a cp1252 pipe kills the
    child on first such print), and line buffering so each finished line
    reaches the pane as it happens rather than at exit. ``input()`` flushes
    its own unterminated prompt, so prompts still arrive immediately.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(
                encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            pass


def _is_importable(module: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def run_procedure(argv: list[str]) -> int:
    """Execute ``argv[0]`` as ``__main__`` with ``argv[1:]``; return exit code."""
    import runpy
    import traceback

    if not argv:
        print(f"{RUN_PROCEDURE_FLAG} needs a module name", file=sys.stderr)
        return 2
    module = argv[0]

    _prepare_streams()
    # Checked up front so "this build has no such procedure" stays
    # distinguishable from "the procedure hit an ImportError of its own":
    # runpy reports both as ImportError once it is running.
    if not _is_importable(module):
        print(f"procedure {module} is not available in this build",
              file=sys.stderr)
        return 2

    sys.argv = [module, *argv[1:]]
    try:
        runpy.run_module(module, run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
        print(exc.code, file=sys.stderr)
        return 1
    except Exception:
        # Print rather than propagate: an escaping exception in a windowed
        # frozen build can surface as a bootloader dialog nobody is there to
        # dismiss. The pane wants the traceback in its terminal instead.
        traceback.print_exc()
        return 1
    return 0


def maybe_run_procedure(argv: list[str] | None = None) -> None:
    """Exit the process running a procedure if invoked as the runner.

    Returns normally — letting the app start as usual — when the sentinel is
    absent.
    """
    argv = sys.argv if argv is None else argv
    if len(argv) > 1 and argv[1] == RUN_PROCEDURE_FLAG:
        sys.exit(run_procedure(argv[2:]))
