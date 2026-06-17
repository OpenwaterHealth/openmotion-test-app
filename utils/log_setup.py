"""Application logging setup for the OpenMOTION test app.

Mirrors the bloodflow app's strategy: every launch writes a timestamped log
file under ``<root>/app-logs/`` while also echoing to the console. Configures
the *root* logger so every module logger (``ow-testapp``, ``openmotion.sdk.*``,
``__main__``, ...) is captured in one file.

Kept free of PyQt imports so it can be unit-tested without launching Qt.
"""
import datetime
import logging
import os

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

    root_logger.info("=" * 64)
    root_logger.info("OpenMOTION Test App %s starting", app_version)
    root_logger.info("Log file:       %s", logfile_path)
    root_logger.info("Data directory: %s", data_dir)
    root_logger.info("=" * 64)

    return logfile_path
