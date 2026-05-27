import sys
import os
import warnings
import logging
import argparse
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance

from motion_connector import MotionConnector
from motion_singleton import motion_interface
from version import get_version

# set PYTHONPATH=%cd%\..\openmotion-sdk;%PYTHONPATH%
# python main.py

APP_VERSION = get_version()

logger = logging.getLogger(__name__)

# Suppress PyQt6 DeprecationWarnings related to SIP
warnings.simplefilter("ignore", DeprecationWarning)


def resource_path(rel: str) -> str:
    import sys
    import os

    base = getattr(
        sys,
        "_MEIPASS",
        os.path.abspath(
            os.path.dirname(
                sys.executable if getattr(sys, "frozen", False) else __file__
            )
        ),
    )
    return os.path.join(base, rel)


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Open-Motion Test Application")
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging and console output"
    )
    parser.add_argument(
        "--no-github",
        action="store_true",
        help="Disable all GitHub release queries (firmware dropdowns will be empty; use file upload to flash)",
    )
    args = parser.parse_args()

    # Configure logging based on debug flag
    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            handlers=[
                logging.StreamHandler(sys.stdout),  # Console output
                logging.FileHandler("debug.log"),  # File output
            ],
        )
        logger.info("Debug mode enabled - logging level set to DEBUG")
    else:
        logging.basicConfig(level=logging.INFO)

    os.environ["QT_QUICK_CONTROLS_STYLE"] = "Material"
    os.environ["QT_QUICK_CONTROLS_MATERIAL_THEME"] = "Dark"
    os.environ["QT_LOGGING_RULES"] = "qt.qpa.fonts=false"

    app = QGuiApplication(sys.argv)

    # Set the global application icon
    app.setWindowIcon(QIcon("assets/images/favicon.png"))
    engine = QQmlApplicationEngine()

    engine.warnings.connect(lambda warnings: print([w.toString() for w in warnings]))

    # Expose to QML
    log_level = logging.DEBUG if args.debug else logging.INFO
    connector = MotionConnector(log_level=log_level, github_disabled=args.no_github)
    qmlRegisterSingletonInstance("OpenMotion", 1, 0, "MotionInterface", connector)
    engine.rootContext().setContextProperty("appVersion", APP_VERSION)
    # Also expose app version on the QGuiApplication instance so Python
    # modules (not just QML) can read it via QGuiApplication.instance().property()
    app.setProperty("appVersion", APP_VERSION)

    # Load the QML file
    engine.load(resource_path("main.qml"))

    if not engine.rootObjects():
        print("Error: Failed to load QML file")
        sys.exit(-1)

    # The SDK now owns its own daemon connection-monitor thread; no
    # asyncio loop required. start() returns once any already-attached
    # devices have completed their CONNECTING transition (or wait_timeout).
    logger.info("Starting Open-Motion monitoring...")
    motion_interface.start(wait=True, wait_timeout=2.0)

    def handle_exit():
        """Stop the monitor cleanly before Qt tears down."""
        logger.info("Application closing...")
        try:
            motion_interface.stop()
        except Exception as e:
            logger.warning("Error stopping MotionInterface: %s", e)
        engine.deleteLater()

    app.aboutToQuit.connect(handle_exit)

    try:
        sys.exit(app.exec())
    except KeyboardInterrupt:
        logger.info("Application interrupted by user.")


if __name__ == "__main__":
    main()
