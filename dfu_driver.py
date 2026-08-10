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

Never-seen is not an error. A machine where no device has ever entered DFU
has no registry key at all, and blocking on that would break every first
flash on a fresh bench PC — so only a positively unusable binding blocks.
"""

from __future__ import annotations

import itertools
import logging
import sys

logger = logging.getLogger("ow-testapp")

_DFU_ENUM_KEY = r"SYSTEM\CurrentControlSet\Enum\USB\VID_0483&PID_DF11"

#: Kernel services libusb (and therefore dfu-util) can drive.
_USABLE_SERVICES = {"winusb", "libusbk", "libusb0"}

_ZADIG_FIX = (
    "Install the WinUSB driver with Zadig: with the device in DFU mode "
    "(or via Options > List All Devices), pick the 0483:DF11 "
    '"STM32 BOOTLOADER" entry, choose WinUSB, and click Install. '
    "Then retry the update."
)


def _instance_services() -> list[str | None] | None:
    """The driver service bound to each DFU device instance Windows has seen.

    ``None`` (instead of a list) when the machine has never enumerated a
    0483:DF11 device; a ``None`` entry for an instance with no driver bound
    (Device Manager code 28).
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
            try:
                with winreg.OpenKey(enum_key, instance) as inst_key:
                    services.append(str(winreg.QueryValueEx(inst_key, "Service")[0]))
            except OSError:
                services.append(None)
    return services


def _evaluate(services: list[str | None] | None) -> str:
    """Classify recorded bindings; "" means do not block."""
    if services is None or not services:
        # Never enumerated: nothing to verify. A first-ever flash on this PC
        # may still fail, and the SDK reports that at classification time.
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
    except Exception:
        logger.debug("DFU driver preflight could not run; not blocking", exc_info=True)
        return ""
    issue = _evaluate(services)
    if services is None:
        logger.info("DFU driver preflight: no 0483:DF11 instance recorded; skipping")
    elif issue:
        logger.warning(f"DFU driver preflight: {issue}")
    return issue
