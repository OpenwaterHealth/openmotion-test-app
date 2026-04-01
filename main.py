import sys
import os
import asyncio
import warnings
import logging
import argparse
from PyQt6.QtGui import QGuiApplication, QIcon
from PyQt6.QtQml import QQmlApplicationEngine, qmlRegisterSingletonInstance
from qasync import QEventLoop

from motion_connector import MOTIONConnector
from version import get_version

# set PYTHONPATH=%cd%\..\OpenMOTION-PyLib;%PYTHONPATH%
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
    parser = argparse.ArgumentParser(description="OpenMOTION Test Application")
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
    connector = MOTIONConnector(log_level=log_level, github_disabled=args.no_github)
    qmlRegisterSingletonInstance("OpenMotion", 1, 0, "MOTIONInterface", connector)
    engine.rootContext().setContextProperty("appVersion", APP_VERSION)
    # Also expose app version on the QGuiApplication instance so Python
    # modules (not just QML) can read it via QGuiApplication.instance().property()
    app.setProperty("appVersion", APP_VERSION)

    # Load the QML file
    engine.load(resource_path("main.qml"))

    if not engine.rootObjects():
        print("Error: Failed to load QML file")
        sys.exit(-1)

    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    async def main_async():
        """Start MOTION monitoring before event loop runs."""
        logger.info("Starting MOTION monitoring...")
        await connector._interface.start_monitoring()

    async def shutdown():
        """Ensure MOTIONConnector stops monitoring before closing."""
        logger.info("Shutting down MOTION monitoring...")
        connector._interface.stop_monitoring()

        pending_tasks = [t for t in asyncio.all_tasks() if not t.done()]
        if pending_tasks:
            logger.info(f"Cancelling {len(pending_tasks)} pending tasks...")
            for task in pending_tasks:
                task.cancel()
            await asyncio.gather(*pending_tasks, return_exceptions=True)

        logger.info("LIFU monitoring stopped. Application shutting down.")

    def handle_exit():
        """Ensure QML cleans up before Python exit without blocking."""
        logger.info("Application closing...")

        # Schedule shutdown but do NOT block the loop
        asyncio.ensure_future(shutdown()).add_done_callback(lambda _: loop.stop())

        engine.deleteLater()  # Ensure QML engine is destroyed

    # Connect shutdown process to app quit event
    app.aboutToQuit.connect(handle_exit)

    try:
        with loop:
            loop.run_until_complete(main_async())
            loop.run_forever()
    except RuntimeError as e:
        if "Event loop stopped before Future completed" in str(e):
            # Graceful shutdown — expected if closing while a future is active
            logger.warning(
                "App closed while a Future was still running (safe to ignore)"
            )
        else:
            logger.error(f"Runtime error: {e}")
    except KeyboardInterrupt:
        logger.info("Application interrupted by user.")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
