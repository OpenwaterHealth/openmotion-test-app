"""Application logging setup for the Open-Motion test app.

Mirrors the bloodflow app's strategy: every launch writes a timestamped log
file under ``<root>/app-logs/`` while also echoing to the console. Configures
the *root* logger so every module logger (``ow-testapp``, ``openmotion.sdk.*``,
``__main__``, ...) is captured in one file.

Also installs the crash hooks - see ``install_crash_logging``. Without them a
windowed build dies silently, because PyQt reports an unhandled exception to
``stderr``, which ``main`` has pointed at ``os.devnull``.

Kept free of PyQt imports so it can be unit-tested without launching Qt.
"""
import datetime
import faulthandler
import logging
import os
import sys
import threading

# SDK loggers that emit one line per USB packet / telemetry tick. Pinned to
# INFO even in --debug so they don't drown the log.
_NOISY_SDK_LOGGERS = (
    "openmotion.sdk.CommInterface",
    "openmotion.sdk.ConsoleTelemetry",
    "openmotion.sdk.UARTPACKET",
    "openmotion.sdk.Sensor",
)


def resolve_log_root() -> str:
    """Directory under which ``app-logs/`` should live.

    The test app has no ``dataDirectory`` config, so use the current working
    directory when it is writable, otherwise fall back to
    ``~/Documents/OpenWater Test`` (e.g. a launch where cwd is read-only).
    """
    candidate = os.getcwd()
    if os.access(candidate, os.W_OK):
        return candidate
    return os.path.join(os.path.expanduser("~"), "Documents", "OpenWater Test")


# Kept alive for the process lifetime: faulthandler writes to this handle from
# a signal/fault context, so it must not be closed or garbage collected.
_fault_log = None


def install_crash_logging(logfile_path: str) -> str:
    """Make an abnormal exit leave evidence instead of a hole.

    Three ways this app can die without writing anything, all seen or possible
    on a bench:

    * An unhandled exception in a Qt slot. PyQt reports it and then calls
      ``qFatal()``, which aborts - and in a windowed build the report goes to
      the ``os.devnull`` ``main`` installs for a detached ``stderr``. The
      ``sys.excepthook`` below runs as part of that reporting, so the
      traceback reaches the log before the abort.
    * An exception on a worker thread (the pane releases devices on one).
    * A native fault - e.g. a libusb teardown race - which no Python hook can
      catch. ``faulthandler`` dumps every thread's Python stack for those,
      which at least names the call that was in flight.

    Returns the path of the fault dump file, a sibling of the app log.
    """
    global _fault_log
    logger = logging.getLogger("crash")

    def _log_exception(exc_type, exc_value, exc_tb, source="main thread"):
        logger.critical("Unhandled exception on %s", source,
                        exc_info=(exc_type, exc_value, exc_tb))
        for handler in logging.getLogger().handlers:
            handler.flush()  # qFatal aborts immediately after we return

    previous_hook = sys.excepthook

    def _excepthook(exc_type, exc_value, exc_tb):
        _log_exception(exc_type, exc_value, exc_tb)
        previous_hook(exc_type, exc_value, exc_tb)

    def _threadhook(args):
        _log_exception(args.exc_type, args.exc_value, args.exc_traceback,
                       source=f"thread {args.thread and args.thread.name}")

    sys.excepthook = _excepthook
    threading.excepthook = _threadhook

    fault_path = os.path.splitext(logfile_path)[0] + "-fault.log"
    try:
        _fault_log = open(fault_path, "w", encoding="utf-8")
        faulthandler.enable(file=_fault_log, all_threads=True)
    except OSError as e:
        logger.warning("faulthandler not enabled (%s)", e)
    return fault_path


def configure_app_logging(debug: bool, app_version: str = "") -> str:
    """Attach console + timestamped file handlers to the root logger.

    The file lands at ``<root>/app-logs/ow-testapp-<YYYYMMDD_HHMMSS>.log``.
    Returns the absolute path to that log file.
    """
    level = logging.DEBUG if debug else logging.INFO
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    )

    data_dir = resolve_log_root()
    run_dir = os.path.join(data_dir, "app-logs")
    os.makedirs(run_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    logfile_path = os.path.join(run_dir, f"ow-testapp-{ts}.log")

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(logfile_path, mode="w", encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    if debug:
        for noisy in _NOISY_SDK_LOGGERS:
            logging.getLogger(noisy).setLevel(logging.INFO)

    fault_path = install_crash_logging(logfile_path)

    root_logger.info("=" * 64)
    root_logger.info("Open-Motion Test App %s starting", app_version)
    root_logger.info("Log file:       %s", logfile_path)
    root_logger.info("Fault dumps:    %s", fault_path)
    root_logger.info("Data directory: %s", data_dir)
    root_logger.info("=" * 64)

    return logfile_path
