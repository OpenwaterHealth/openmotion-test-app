# motion_singleton.py
#
# Single shared MotionInterface for the test app. The interface owns its
# own connection-monitor daemon thread; ``main.py`` calls
# ``motion_interface.start()`` once after the QML engine is loaded.
from omotion import MotionInterface

motion_interface = MotionInterface()
