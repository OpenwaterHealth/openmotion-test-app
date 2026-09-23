"""Preflight check for the STM32 DFU driver on Windows.

Firmware updates and bootloader installs put the device into DFU mode
(USB 0483:DF11) and then drive it with the SDK's bundled dfu-util, which
needs a libusb-compatible kernel driver (WinUSB / libusbK / libusb0) bound
to that device. Windows records the binding per device instance under
``HKLM\\SYSTEM\\CurrentControlSet\\Enum\\USB\\VID_0483&PID_DF11`` and the
binding persists across re-enumerations, so the state is checkable *before*
entering DFU. The ordering matters: a missing driver does not fail cleanly —
enter_dfu() strands the device in DFU (power-cycle to recover) and the update
then times out or aborts with an unclassifiable device, because dfu-util can
see the device but not read its alt-setting names. Field case 2026-08: days
were lost to "could not tell whether this device has the bootloader
installed" halts whose actual cause was exactly this.

Only a positively unusable state blocks; anything uncertain passes and
leaves failures to surface the old way (SDK-side, after DFU entry):

- Never-seen is not an error. A machine where no device has ever entered DFU
  has no registry key at all, and blocking on that would break every first
  flash on a fresh bench PC.
- A driverless instance record can be stale: it stays behind from before a
  driver was installed. When the driver store
  (``HKLM\\SYSTEM\\DriverDatabase\\DeviceIds\\USB\\VID_0483&PID_DF11``) holds
  any INF for 0483:DF11, the next DFU entry binds to it, so such records do
  not block.
- An instance whose key cannot be read (e.g. access denied) is unknown, not
  driverless.
"""

from __future__ import annotations

import itertools
import logging
import sys

logger = logging.getLogger("ow-testapp")

_DFU_ENUM_KEY = r"SYSTEM\CurrentControlSet\Enum\USB\VID_0483&PID_DF11"
_DFU_DRIVER_DB_KEY = r"SYSTEM\DriverDatabase\DeviceIds\USB\VID_0483&PID_DF11"

#: Kernel services libusb (and therefore dfu-util) can drive.
_USABLE_SERVICES = {"winusb", "libusbk", "libusb0"}

# Zadig's "Create New Device" installs the driver for a VID:PID without the
# device attached, so the fix works while the device is still in normal mode
# (the preflight refuses to put it in DFU first).
_ZADIG_FIX = (
    "Install the WinUSB driver with Zadig (run as administrator): choose "
    'Device > Create New Device, name it "STM32 BOOTLOADER", enter USB ID '
    "0483 DF11, select WinUSB as the target driver, and click Install Driver. "
    "If the device is already in DFU mode you can instead pick the existing "
    '"STM32 BOOTLOADER" entry (Options > List All Devices). Then retry the '
    "update."
)

_UNREADABLE = object()


def _instance_services() -> list[str | None] | None:
    """The driver service bound to each DFU device instance Windows has seen.

    ``None`` (instead of a list) when the machine has never enumerated a
    0483:DF11 device; a ``None`` entry for an instance with no driver bound
    (no ``Service`` value, Device Manager code 28). Instances whose key
    cannot be read are left out: unknown is not the same as driverless.
    """
    import winreg

    try:
        enum_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DFU_ENUM_KEY)
    except OSError:
        return None

    services: list[str | None] = []
    with enum_key:
        for i in itertools.count():
            try:
                instance = winreg.EnumKey(enum_key, i)
            except OSError:
                break
            service = _read_service(winreg, enum_key, instance)
            if service is _UNREADABLE:
                logger.debug(f"DFU driver preflight: instance {instance} unreadable; ignoring")
                continue
            services.append(service)
    return services


def _read_service(winreg, enum_key, instance: str):
    try:
        inst_key = winreg.OpenKey(enum_key, instance)
    except OSError:
        return _UNREADABLE
    with inst_key:
        try:
            return str(winreg.QueryValueEx(inst_key, "Service")[0])
        except FileNotFoundError:
            return None
        except OSError:
            return _UNREADABLE


def _driver_store_has_df11() -> bool | None:
    """Whether the driver store holds any INF matching USB 0483:DF11.

    ``None`` when the driver database cannot be read.
    """
    import winreg

    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DFU_DRIVER_DB_KEY)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    with key:
        try:
            winreg.EnumValue(key, 0)
        except OSError:
            return False
    return True


def _evaluate(services: list[str | None] | None, store_has_inf: bool | None = False) -> str:
    """Classify recorded bindings; "" means do not block.

    ``store_has_inf`` is the driver-store check: ``True`` or ``None``
    (unknown) lets driverless records through as possibly stale.
    """
    if services is None or not services:
        # Never enumerated (or nothing readable): nothing to verify. A
        # first-ever flash on this PC may still fail, and the SDK reports
        # that at classification time.
        return ""
    if any(s and s.lower() in _USABLE_SERVICES for s in services):
        # One usable binding is enough: the matching INF is in the driver
        # store and future instances of the same VID:PID bind to it.
        return ""
    bound = sorted({s for s in services if s})
    if bound:
        return (
            f"The STM32 DFU device (USB 0483:DF11) on this PC is bound to "
            f"{', '.join(bound)}, which dfu-util cannot use. The update would "
            "enter DFU mode and then fail, leaving the device in DFU until "
            "power-cycled. " + _ZADIG_FIX
        )
    if store_has_inf is not False:
        # Driverless records only, but a DF11 driver package exists (or the
        # store can't be read): the records likely predate the install.
        return ""
    return (
        "This PC has seen the STM32 DFU device (USB 0483:DF11) but has no "
        "driver installed for it, so the update would enter DFU mode and "
        "then fail, leaving the device in DFU until power-cycled. "
        + _ZADIG_FIX
    )


def dfu_driver_issue() -> str:
    """"" when a usable DFU driver is recorded or the state is unknowable,
    else an actionable description of why flashing would fail. Never raises.
    """
    if sys.platform != "win32":
        return ""
    try:
        services = _instance_services()
        store_has_inf = None
        if services and not any(services):
            # Only consulted when every readable instance is driverless.
            store_has_inf = _driver_store_has_df11()
    except Exception:
        logger.debug("DFU driver preflight could not run; not blocking", exc_info=True)
        return ""
    issue = _evaluate(services, store_has_inf)
    if services is None:
        logger.info("DFU driver preflight: no 0483:DF11 instance recorded; skipping")
    elif issue:
        logger.warning(f"DFU driver preflight: {issue}")
    elif services and not any(services):
        logger.info(
            "DFU driver preflight: driverless 0483:DF11 records found, but a "
            f"DF11 driver package is in the driver store (store check={store_has_inf}); "
            "treating the records as stale"
        )
    return issue
