# motion_singleton.py
#
# Single shared MotionInterface for the test app. The interface owns its
# own connection-monitor daemon thread; ``main.py`` calls
# ``motion_interface.start()`` once after the QML engine is loaded.
import os

from omotion import MotionInterface

# data_dir: SDK-managed outputs (corrected CSVs, telemetry) land next to the
# histogram CSVs the connector writes.  scan_db_path=None because the test app
# has no clinical scan database; operator_id identifies the source in logs.
_data_dir = os.path.expanduser("~")
os.makedirs(_data_dir, exist_ok=True)

motion_interface = MotionInterface(
    data_dir=_data_dir,
    scan_db_path=None,
    operator_id="test-app",
)
