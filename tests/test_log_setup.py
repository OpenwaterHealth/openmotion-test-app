"""Unit tests for utils.log_setup.configure_app_logging.

Run from the repo root with:  python -m pytest tests/test_log_setup.py
(`python -m pytest` puts the repo root on sys.path so `import utils.*` works.)
"""
import logging
from pathlib import Path

import pytest


@pytest.fixture
def clean_root_logger():
    """Isolate the global root logger: strip its handlers for the duration of
    the test, then close+restore so we don't leak file handles (Windows locks
    the log file otherwise) or pollute other tests."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    for h in saved_handlers:
        root.removeHandler(h)
    yield root
    for h in root.handlers[:]:
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)


def test_creates_timestamped_logfile_under_app_logs(tmp_path, monkeypatch, clean_root_logger):
    from utils.log_setup import configure_app_logging

    monkeypatch.chdir(tmp_path)
    path = configure_app_logging(debug=False, app_version="9.9.9")

    p = Path(path)
    assert p.is_file(), "log file should be created on disk"
    assert p.parent == (tmp_path / "app-logs").resolve()
    assert p.name.startswith("ow-testapp-")
    assert p.suffix == ".log"


def test_attaches_console_and_file_handlers_to_root(tmp_path, monkeypatch, clean_root_logger):
    from utils.log_setup import configure_app_logging

    monkeypatch.chdir(tmp_path)
    # pytest's logging plugin attaches its own capture handlers to root, so
    # measure only the handlers configure_app_logging itself adds.
    before = {id(h) for h in clean_root_logger.handlers}
    configure_app_logging(debug=False, app_version="9.9.9")
    added = [h for h in clean_root_logger.handlers if id(h) not in before]

    file_handlers = [h for h in added if isinstance(h, logging.FileHandler)]
    # A FileHandler is a StreamHandler subclass, so exclude it when counting consoles.
    console_handlers = [
        h for h in added
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    ]
    assert len(file_handlers) == 1
    assert len(console_handlers) == 1


def test_level_follows_debug_flag(tmp_path, monkeypatch, clean_root_logger):
    from utils.log_setup import configure_app_logging

    monkeypatch.chdir(tmp_path)
    configure_app_logging(debug=True, app_version="9.9.9")
    assert clean_root_logger.level == logging.DEBUG


def test_level_info_when_not_debug(tmp_path, monkeypatch, clean_root_logger):
    from utils.log_setup import configure_app_logging

    monkeypatch.chdir(tmp_path)
    configure_app_logging(debug=False, app_version="9.9.9")
    assert clean_root_logger.level == logging.INFO


def test_falls_back_to_documents_when_cwd_not_writable(tmp_path, monkeypatch, clean_root_logger):
    from utils import log_setup

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(log_setup.os, "access", lambda *a, **k: False)
    monkeypatch.setattr(log_setup.os.path, "expanduser", lambda p: str(fake_home))

    path = log_setup.configure_app_logging(debug=False, app_version="9.9.9")

    expected_root = fake_home / "Documents" / "OpenWater Test" / "app-logs"
    assert Path(path).parent == expected_root.resolve()
    assert Path(path).is_file()


def test_banner_and_log_lines_reach_the_file(tmp_path, monkeypatch, clean_root_logger):
    from utils.log_setup import configure_app_logging

    monkeypatch.chdir(tmp_path)
    path = configure_app_logging(debug=False, app_version="9.9.9")
    logging.getLogger("ow-testapp").info("hello from a module logger")

    # Flush handlers so the assertion reads complete content.
    for h in clean_root_logger.handlers:
        h.flush()
    contents = Path(path).read_text(encoding="utf-8")

    assert "Open-Motion Test App 9.9.9 starting" in contents
    assert "hello from a module logger" in contents


def _restore_hooks():
    import sys
    import threading
    saved = (sys.excepthook, threading.excepthook)

    def undo():
        sys.excepthook, threading.excepthook = saved
    return undo


def test_an_unhandled_exception_reaches_the_log(tmp_path, monkeypatch,
                                                clean_root_logger):
    """PyQt reports a slot's exception through sys.excepthook and then aborts,
    so this is the only chance to record it - and in a windowed build stderr
    is os.devnull, so the log is the only place it can go."""
    import sys
    from utils.log_setup import configure_app_logging

    monkeypatch.chdir(tmp_path)
    undo = _restore_hooks()
    try:
        path = configure_app_logging(debug=False)
        try:
            raise RuntimeError("libusb teardown went sideways")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())
    finally:
        undo()

    contents = Path(path).read_text(encoding="utf-8")
    assert "libusb teardown went sideways" in contents
    assert "Unhandled exception on main thread" in contents
    assert "Traceback" in contents


def test_a_worker_threads_exception_reaches_the_log(tmp_path, monkeypatch,
                                                    clean_root_logger):
    """The pane releases device handles on a worker thread; an exception there
    would otherwise vanish."""
    import threading
    from utils.log_setup import configure_app_logging

    monkeypatch.chdir(tmp_path)
    undo = _restore_hooks()
    try:
        path = configure_app_logging(debug=False)

        def boom():
            raise ValueError("release worker died")

        worker = threading.Thread(target=boom, name="procedures-release")
        worker.start()
        worker.join()
    finally:
        undo()

    contents = Path(path).read_text(encoding="utf-8")
    assert "release worker died" in contents
    assert "procedures-release" in contents


def test_faulthandler_writes_beside_the_app_log(tmp_path, monkeypatch,
                                                clean_root_logger):
    """Native faults (a libusb teardown race) bypass every Python hook;
    faulthandler is what leaves a Python stack behind for those."""
    import faulthandler
    from utils.log_setup import configure_app_logging

    monkeypatch.chdir(tmp_path)
    undo = _restore_hooks()
    try:
        path = configure_app_logging(debug=False)
    finally:
        undo()

    assert faulthandler.is_enabled()
    fault_path = Path(path).with_name(Path(path).stem + "-fault.log")
    assert fault_path.is_file()
    assert "Fault dumps:" in Path(path).read_text(encoding="utf-8")
