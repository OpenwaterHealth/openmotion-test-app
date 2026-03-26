from PyQt6.QtCore import (
    QObject,
    pyqtSignal,
    pyqtProperty,
    pyqtSlot,
    QVariant,
    QThread,
    QWaitCondition,
    QMutex,
    QMutexLocker,
    QRecursiveMutex,
)
from PyQt6.QtGui import QGuiApplication
import sys
import logging
import base58
import json
import csv
import os
import datetime
import time
import uuid
import numpy as np
import pandas as pd
from pathlib import Path

from omotion.GitHubReleases import GitHubReleases
from utils.resource_path import resource_path
from motion_singleton import motion_interface
from histogram_classifier import classify_histogram

try:
    from omotion.DFUProgrammer import DFUProgrammer, DFUProgress
except Exception:  # pragma: no cover
    DFUProgrammer = None
    DFUProgress = None

try:
    from omotion.GitHubReleases import GitHubReleases
except Exception:  # pragma: no cover
    GitHubReleases = None

try:
    from omotion.FPGAProgrammer import FpgaPageProgrammer, FpgaUpdateError
    from omotion.config import MuxChannel
    from omotion.CommandError import CommandError
except Exception:  # pragma: no cover
    FpgaPageProgrammer = None
    # Ensure fallback exception types inherit from BaseException so they
    # can safely be used in `except` clauses elsewhere in this module.
    FpgaUpdateError = Exception
    MuxChannel = None
    CommandError = Exception

# constants for calculations
SCALE_V = 0.0909
SCALE_I = 0.25
V_REF = 2.459  # Should be 2.5V but empirical measurements don't match
R_1 = 18000  # (R221)
R_2 = 8160  # (R224)
R_3 = 49900  # (R225)
R230 = 300e3
R234 = 300e3
R_s = 0.020  # (R217)


def solve_R_TH(v):
    """
    Solves for R_TH given voltage v (VOUT1 from ADC).

    Args:
        v     : ADC voltage (VOUT1)
        V_REF : Reference voltage
        R_1   : Resistance R1
        R_2   : Resistance R2
        R_3   : Resistance R3

    Returns:
        R_TH  : Thermistor resistance
    """
    R_TH = 1 / ((v / (V_REF / 2 * R_3)) - 1 / R_3 + 1 / R_1) - R_2
    return R_TH


def solve_v(R_TH):
    """
    Solves for v (VOUT1) given R_TH.

    Args:
        R_TH  : Thermistor resistance
        V_REF : Reference voltage
        R_1   : Resistance R1
        R_2   : Resistance R2
        R_3   : Resistance R3

    Returns:
        v     : ADC voltage (VOUT1)
    """
    v = (1 / (R_TH + R_2) + 1 / R_3 - 1 / R_1) * (V_REF / 2) * R_3
    return v


# Global loggers - will be configured by _configure_logging method
logger = None
run_logger = None

# Define system states
DISCONNECTED = 0
SENSOR_CONNECTED = 1
CONSOLE_CONNECTED = 2
READY = 3
RUNNING = 4

# Firmware source (GitHub releases)
_CONSOLE_FW_REPO_OWNER = "OpenwaterHealth"
_CONSOLE_FW_REPO_NAME = "openmotion-console-fw"
_SENSOR_FW_REPO_NAME = "openmotion-sensor-fw"
_FPGA_FW_REPO_MAP = {
    "TA": "openmotion-ta-fpga",
    "SEED": "openmotion-seed-fpga",
    "SAFETY": "openmotion-safety-fpga",
    # Explicit targets for programming the two safety FPGAs individually
    "SAFETY_EE": "openmotion-safety-fpga",
    "SAFETY_OPT": "openmotion-safety-fpga",
}
# Console FPGA mux channel mapping used for in-system programming.
_FPGA_PROGRAM_CHANNELS = {
    # MuxChannel is 0-based in omotion.config:
    # FPGA_SEED=0, FPGA_TA=1, FPGA_SAFE_EE=2, FPGA_SAFE_OPT=3
    "TA": [1],
    "SEED": [0],
    # Safety update programs both devices by default, and we also
    # support programming the EE or OPT devices individually.
    "SAFETY_EE": [2],
    "SAFETY_OPT": [3],
}


def _app_root_dir() -> Path:
    """Return a stable, writable-adjacent base directory for the app.

    When running as a PyInstaller bundle, use the executable folder. In dev,
    use the folder containing this file.
    """
    try:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
    except Exception:
        pass
    return Path(__file__).resolve().parent


def _downloads_dir() -> Path:
    # Prefer a ./downloads folder (matches the CLI test script behavior),
    # but fall back to the app root if the CWD isn't writable.
    preferred = Path.cwd() / "downloads"
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        return preferred
    except Exception:
        fallback = _app_root_dir() / "downloads"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _candidate_console_fw_tags(tag: str) -> list[str]:
    """Generate likely tag variants (case/prefix normalization)."""
    t = (tag or "").strip()
    candidates: list[str] = []
    if t:
        candidates.append(t)

        # Common normalizations seen in prerelease naming
        low = t.lower()
        if low != t:
            candidates.append(low)

        # pre-vX.Y.Z <-> pre-X.Y.Z
        if low.startswith("pre-v"):
            candidates.append("pre-" + low[len("pre-v") :])
        if low.startswith("pre-") and not low.startswith("pre-v"):
            candidates.append("pre-v" + low[len("pre-") :])

        # vX.Y.Z <-> X.Y.Z (only for non-pre tags)
        if not low.startswith("pre-"):
            if low.startswith("v") and len(low) > 1:
                candidates.append(low[1:])
            elif low and low[0].isdigit():
                candidates.append("v" + low)

    # de-dup while preserving order
    seen = set()
    ordered_tags: list[str] = []
    for x in candidates:
        if x and x not in seen:
            seen.add(x)
            ordered_tags.append(x)

    return ordered_tags


class _ConsoleFirmwareDownloadThread(QThread):
    progress = pyqtSignal(int, str)  # percent (0-100, -1 indeterminate), message
    failed = pyqtSignal(str)
    ready = pyqtSignal(str, str, str, str)  # token, tag, filename, target

    def __init__(
        self,
        connector: "MOTIONConnector",
        tag: str,
        filename: str,
        target: str = "CONSOLE",
    ):
        super().__init__()
        self._connector = connector
        self._tag = tag
        self._filename = filename
        self._target = target

    def run(self):
        token: str | None = None
        try:
            self.progress.emit(-1, f"Locating {self._filename} for {self._tag}…")

            if GitHubReleases is None:
                self.failed.emit(
                    "GitHubReleases is unavailable (omotion SDK not found in environment)."
                )
                return

            # Download into a stable downloads/ directory (create if missing)
            dl_dir = _downloads_dir()
            dl_dir.mkdir(parents=True, exist_ok=True)

            token = uuid.uuid4().hex

            # Track token -> (dir, file, cleanup?, target) where cleanup False for downloads/
            # (bin_path filled in once download completes)
            self._connector._fw_temp_files[token] = (
                str(dl_dir),
                str((dl_dir / self._filename).resolve()),
                False,
                self._target,
            )

            repo_name = (
                _CONSOLE_FW_REPO_NAME
                if self._target == "CONSOLE"
                else _SENSOR_FW_REPO_NAME
            )
            gh = GitHubReleases(_CONSOLE_FW_REPO_OWNER, repo_name, timeout=30)
            last_exc: Exception | None = None
            downloaded_path: Path | None = None

            for candidate_tag in _candidate_console_fw_tags(self._tag):
                try:
                    self.progress.emit(-1, f"Fetching release {candidate_tag}…")
                    release = gh.get_release_by_tag(candidate_tag)

                    assets = gh.get_asset_list(release=release)
                    asset_names = {
                        a.get("name") for a in (assets or []) if isinstance(a, dict)
                    }
                    if self._filename not in asset_names:
                        last_exc = RuntimeError(
                            f"Asset '{self._filename}' not present in release '{candidate_tag}'."
                        )
                        continue

                    self.progress.emit(-1, f"Downloading {self._filename}…")
                    downloaded_path = gh.download_asset(
                        release, self._filename, output_dir=dl_dir
                    )
                    break
                except Exception as exc:
                    last_exc = exc
                    continue

            if downloaded_path is None:
                msg = f"Firmware binary '{self._filename}' was not found for release '{self._tag}'."
                if last_exc is not None:
                    msg += f" ({last_exc})"
                self.failed.emit(msg)
                return

            local_path = str(Path(downloaded_path).resolve())
            self._connector._fw_temp_files[token] = (
                str(dl_dir),
                local_path,
                False,
                self._target,
            )
            self.progress.emit(-1, f"Downloaded asset to: {local_path}")
            self.progress.emit(100, "Download complete")
            self.ready.emit(token, self._tag, self._filename, self._target)
        except Exception as exc:
            if token is not None:
                self._connector._cleanup_fw_token(token)
            self.failed.emit(str(exc))


class _ConsoleFirmwareFlashThread(QThread):
    progress = pyqtSignal(int, str)  # percent (0-100, -1 indeterminate), message
    failed = pyqtSignal(str)
    finished_ok = pyqtSignal()

    def __init__(self, connector: "MOTIONConnector", bin_path: str):
        super().__init__()
        self._connector = connector
        self._bin_path = bin_path

    def run(self):
        if DFUProgrammer is None:
            self.failed.emit(
                "DFUProgrammer is unavailable (omotion SDK not found in environment)."
            )
            return

        try:
            self.progress.emit(-1, "Requesting DFU mode…")
            self._connector._console_mutex.lock()
            try:
                ok = motion_interface.console_module.enter_dfu()
            finally:
                self._connector._console_mutex.unlock()

            if not ok:
                self.failed.emit("Console refused DFU mode request.")
                return

            # Give the bootloader time to re-enumerate
            time.sleep(5.0)

            dfu = DFUProgrammer(vidpid="0483:df11")
            self.progress.emit(-1, "Waiting for DFU device…")
            if not dfu.wait_for_dfu_device(timeout_s=30.0):
                self.failed.emit("DFU device did not appear (timeout).")
                return

            def on_progress(p):
                phase = "Working"
                try:
                    if p.phase == "erase":
                        phase = "Erasing"
                    elif p.phase == "download":
                        phase = "Downloading"
                except Exception:
                    pass

                pct = -1
                try:
                    if p.percent is not None:
                        pct = int(p.percent)
                except Exception:
                    pct = -1
                self.progress.emit(pct, f"{phase}…")

            self.progress.emit(0, "Flashing…")
            result = dfu.flash_bin(
                Path(self._bin_path),
                address=DFUProgrammer.DEFAULT_ADDRESS,
                alt=0,
                verbose=0,
                normalize_dfu_suffix=True,
                progress=on_progress,
                line_callback=None,
                echo_output=False,
                echo_progress_lines=False,
            )

            if not getattr(result, "success", False):
                code = getattr(result, "returncode", "?")
                self.failed.emit(f"Flash failed (dfu-util exit code {code}).")
                return

            self.progress.emit(100, "Flash complete")
            self.finished_ok.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class _DeviceFirmwareFlashThread(QThread):
    progress = pyqtSignal(int, str)  # percent (0-100, -1 indeterminate), message
    failed = pyqtSignal(str)
    finished_ok = pyqtSignal()

    def __init__(self, connector: "MOTIONConnector", bin_path: str, target: str):
        super().__init__()
        self._connector = connector
        self._bin_path = bin_path
        self._target = target

    def run(self):
        if DFUProgrammer is None:
            self.failed.emit(
                "DFUProgrammer is unavailable (omotion SDK not found in environment)."
            )
            return

        try:
            self.progress.emit(-1, "Requesting DFU mode…")

            # Request DFU mode on the correct module with appropriate mutex
            if self._target == "CONSOLE":
                self._connector._console_mutex.lock()
                try:
                    ok = motion_interface.console_module.enter_dfu()
                finally:
                    self._connector._console_mutex.unlock()
            else:
                # SENSOR_LEFT or SENSOR_RIGHT
                sensor_mutex = self._connector._get_sensor_mutex(self._target)
                sensor_tag = "left" if self._target == "SENSOR_LEFT" else "right"
                sensor_mutex.lock()
                try:
                    ok = motion_interface.sensors[sensor_tag].enter_dfu()
                finally:
                    sensor_mutex.unlock()

            if not ok:
                self.failed.emit("Device refused DFU mode request.")
                return

            # Give the bootloader time to re-enumerate
            time.sleep(5.0)

            dfu = DFUProgrammer(vidpid="0483:df11")
            self.progress.emit(-1, "Waiting for DFU device…")
            if not dfu.wait_for_dfu_device(timeout_s=30.0):
                self.failed.emit("DFU device did not appear (timeout).")
                return

            def on_progress(p):
                phase = "Working"
                try:
                    if p.phase == "erase":
                        phase = "Erasing"
                    elif p.phase == "download":
                        phase = "Downloading"
                except Exception:
                    pass

                pct = -1
                try:
                    if p.percent is not None:
                        pct = int(p.percent)
                except Exception:
                    pct = -1
                self.progress.emit(pct, f"{phase}…")

            self.progress.emit(0, "Flashing…")
            result = dfu.flash_bin(
                Path(self._bin_path),
                address=DFUProgrammer.DEFAULT_ADDRESS,
                alt=0,
                verbose=0,
                normalize_dfu_suffix=True,
                progress=on_progress,
                line_callback=None,
                echo_output=False,
                echo_progress_lines=False,
            )

            if not getattr(result, "success", False):
                code = getattr(result, "returncode", "?")
                self.failed.emit(f"Flash failed (dfu-util exit code {code}).")
                return

            self.progress.emit(100, "Flash complete")
            self.finished_ok.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class _ConsoleFpgaUpdateThread(QThread):
    progress = pyqtSignal(int, str)  # percent (0-100, -1 indeterminate), message
    failed = pyqtSignal(str)
    finished_ok = pyqtSignal(str)

    def __init__(
        self, connector: "MOTIONConnector", target: str, tag: str, verify: bool = False
    ):
        super().__init__()
        self._connector = connector
        self._target = (target or "").upper()
        self._tag = (tag or "").strip()
        self._verify = bool(verify)

    def run(self):
        try:
            logger.info(
                f"[FPGA-UPD] thread start target={self._target} tag={self._tag}"
            )
            if GitHubReleases is None:
                logger.info("[FPGA-UPD] GitHubReleases unavailable in environment")
                self.failed.emit(
                    "GitHubReleases is unavailable (omotion SDK not found in environment)."
                )
                return
            if FpgaPageProgrammer is None or MuxChannel is None:
                logger.info(
                    "[FPGA-UPD] FPGA programmer components unavailable in environment"
                )
                self.failed.emit(
                    "FPGA programmer is unavailable (omotion SDK FPGA components missing)."
                )
                return

            repo = _FPGA_FW_REPO_MAP.get(self._target)
            channels = _FPGA_PROGRAM_CHANNELS.get(self._target)
            if not repo or not channels:
                logger.info(
                    f"[FPGA-UPD] invalid target mapping target={self._target} repo={repo} channels={channels}"
                )
                self.failed.emit(f"Invalid FPGA update target: {self._target}")
                return

            logger.info(f"[FPGA-UPD] using repo={repo} channels={channels}")

            self.progress.emit(5, f"Fetching {self._target} release {self._tag}…")
            gh = GitHubReleases(_CONSOLE_FW_REPO_OWNER, repo, timeout=30)

            release = None
            last_exc: Exception | None = None
            for candidate_tag in _candidate_console_fw_tags(self._tag):
                try:
                    logger.info(
                        f"[FPGA-UPD] try get_release_by_tag tag={candidate_tag}"
                    )
                    release = gh.get_release_by_tag(candidate_tag)
                    logger.info(f"[FPGA-UPD] release resolved tag={candidate_tag}")
                    break
                except Exception as exc:
                    last_exc = exc
                    logger.info(
                        f"[FPGA-UPD] tag lookup failed tag={candidate_tag} err={exc}"
                    )

            if release is None:
                msg = f"Release '{self._tag}' not found for {self._target}."
                if last_exc is not None:
                    msg += f" ({last_exc})"
                logger.info(f"[FPGA-UPD] release resolution failed msg={msg}")
                self.failed.emit(msg)
                return

            self.progress.emit(15, "Resolving .jed asset…")
            assets = gh.get_asset_list(release=release)
            if not isinstance(assets, list):
                assets = []
            logger.info(f"[FPGA-UPD] assets discovered count={len(assets)}")

            jed_assets = []
            for asset in assets:
                if not isinstance(asset, dict):
                    continue
                name = str(asset.get("name") or "")
                if name.lower().endswith(".jed"):
                    jed_assets.append(asset)

            logger.info(f"[FPGA-UPD] jed assets count={len(jed_assets)}")

            if not jed_assets:
                logger.info(
                    f"[FPGA-UPD] no .jed assets in release target={self._target} tag={self._tag}"
                )
                self.failed.emit(
                    f"No .jed asset found in release '{self._tag}' for {self._target}."
                )
                return

            jed_assets.sort(key=lambda a: str(a.get("created_at") or ""), reverse=True)
            jed_name = str(jed_assets[0].get("name") or "")
            if not jed_name:
                logger.info("[FPGA-UPD] resolved .jed asset missing name")
                self.failed.emit("Resolved .jed asset has no filename.")
                return

            logger.info(f"[FPGA-UPD] selected jed asset={jed_name}")

            self.progress.emit(25, f"Downloading {jed_name}…")
            dl_dir = _downloads_dir()
            dl_dir.mkdir(parents=True, exist_ok=True)
            jed_path = Path(
                gh.download_asset(release, jed_name, output_dir=dl_dir)
            ).resolve()
            self.progress.emit(35, f"Downloaded {jed_name}")
            logger.info(f"[FPGA-UPD] downloaded jed path={jed_path}")

            programmer = FpgaPageProgrammer(
                motion_interface.console_module,
                verify=self._verify,
                erase_timeout=35.0,
                refresh_timeout=10.0,
            )
            logger.info(
                f"[FPGA-UPD] FpgaPageProgrammer initialized verify={self._verify} erase_timeout=35 refresh_timeout=10"
            )

            total = len(channels)
            for idx, channel in enumerate(channels):
                base = 35 + int((55 * idx) / total)
                span = max(1, int(55 / total))

                def _on_progress(
                    pages_done: int, total_pages: int, ch=channel, b=base, s=span
                ):
                    local_pct = (
                        0.0
                        if total_pages <= 0
                        else (100.0 * float(pages_done) / float(total_pages))
                    )
                    overall = min(95, b + int((s * local_pct) / 100.0))
                    self.progress.emit(overall, f"Programming channel {ch}…")

                self.progress.emit(base, f"Programming channel {channel}…")
                logger.info(
                    f"[FPGA-UPD] programming start target={self._target} channel={channel} ({idx + 1}/{total})"
                )
                self._connector._console_mutex.lock()
                try:
                    attempt = 0
                    while True:
                        try:
                            programmer.program_from_jedec(
                                target_fpga=MuxChannel(channel),
                                jedec_path=str(jed_path),
                                on_progress=_on_progress,
                            )
                            break
                        except Exception as exc_inner:
                            attempt += 1
                            logger.warning(
                                f"[FPGA-UPD] programming attempt {attempt} failed target={self._target} channel={channel} err={exc_inner}"
                            )
                            if attempt >= 2:
                                raise
                            # small delay to allow bus/mux/device to settle before retry
                            time.sleep(0.5)
                finally:
                    self._connector._console_mutex.unlock()
                logger.info(
                    f"[FPGA-UPD] programming done target={self._target} channel={channel}"
                )

            self.progress.emit(100, "FPGA programming complete")
            logger.info(
                f"[FPGA-UPD] thread complete target={self._target} tag={self._tag}"
            )
            self.finished_ok.emit(f"{self._target} FPGA updated successfully.")

        except (FpgaUpdateError, CommandError) as exc:
            logger.error(
                f"[FPGA-UPD] programmer error target={self._target} tag={self._tag}: {exc}"
            )
            self.failed.emit(str(exc))
        except Exception as exc:
            logger.exception(
                f"[FPGA-UPD] unexpected error target={self._target} tag={self._tag}"
            )
            self.failed.emit(str(exc))


class CaptureThread(QThread):
    new_histogram = pyqtSignal(list)  # Signal for histogram data
    update_status = pyqtSignal(str)  # Signal for status updates

    def __init__(self, camera_index, fps=5, parent=None):
        super().__init__(parent)
        self.camera_index = camera_index
        self.running = False
        self.frame_delay = 1.0 / fps

    def run(self):
        if self.camera_index == 9:
            CAMERA_MASK = 0xFF  # All cameras
        else:
            CAMERA_MASK = 1 << (self.camera_index - 1)
        status_map = motion_interface.sensors["left"].get_camera_status(CAMERA_MASK)
        if not status_map:
            logger.error("Failed to get camera status map.")
            return None

        for cam_idx in range(8):
            if CAMERA_MASK & (1 << cam_idx):
                status = status_map.get(cam_idx)
                if status is None:
                    logger.error(f"Camera {cam_idx + 1} missing in status map.")
                    return None

                if not status & (1 << 0):  # Not READY
                    logger.error(f"Camera {cam_idx + 1} is not ready.")
                    return None

                if not (status & (1 << 1) and status & (1 << 2)):  # Not programmed
                    self.update_status.emit(f"prog {cam_idx + 1}")
                    logger.debug(f"FPGA configuration started for camera {cam_idx + 1}")
                    start_time = time.time()

                    if not motion_interface.sensors["left"].program_fpga(
                        camera_position=(1 << cam_idx), manual_process=False
                    ):
                        logger.error(f"Failed to program FPGA for camera {cam_idx + 1}")
                        return None
                    logger.debug(
                        f"FPGA programmed for camera {cam_idx + 1} | Time: {(time.time() - start_time) * 1000:.2f} ms"
                    )

                if not (status & (1 << 1) and status & (1 << 3)):  # Not configured
                    self.update_status.emit(f"conf {cam_idx + 1}")
                    logger.debug(f"Configuring registers for camera {cam_idx + 1}")
                    if not motion_interface.sensors["left"].camera_configure_registers(
                        1 << cam_idx
                    ):
                        logger.error(
                            f"Failed to configure registers for camera {cam_idx + 1}"
                        )
                        return None

        logger.debug("Setting test pattern...")
        self.update_status.emit("set live")
        if not motion_interface.sensors["left"].camera_configure_test_pattern(
            CAMERA_MASK, 0x04
        ):
            logger.error("Failed to set test pattern.")
            return None

        # Get status
        status_map = motion_interface.sensors["left"].get_camera_status(CAMERA_MASK)
        if not status_map:
            logger.error("Failed to get camera status.")
            return None

        for cam_idx in range(8):
            if CAMERA_MASK & (1 << cam_idx):
                status = status_map.get(cam_idx)

                if status is None:
                    logger.error(f"Camera {cam_idx + 1} missing in status map.")
                    return None
                logger.debug(
                    f"Camera {self.camera_index} status: 0x{status:02X} - {motion_interface.sensors['left'].decode_camera_status(status)}"
                )

                if not (
                    status & (1 << 0) and status & (1 << 1) and status & (1 << 2)
                ):  # Not ready for histo
                    logger.error("Not configured.")
                    return None

        self.running = True
        while self.running:
            start_time = time.time()
            try:
                logger.debug("Capturing histogram...")
                if not motion_interface.sensors["left"].camera_capture_histogram(
                    CAMERA_MASK
                ):
                    logger.error("Capture failed.")
                else:
                    logger.debug("Capture successful, retrieving histogram...")
                    time.sleep(0.005)  # Wait for capture to complete
                    histogram = motion_interface.sensors["left"].camera_get_histogram(
                        CAMERA_MASK
                    )
                    if histogram is None:
                        logger.error("Histogram retrieval failed.")
                    else:
                        logger.debug("Histogram frame received successfully.")
                        histogram = histogram[
                            :4096
                        ]  # Ensure we only take the first 4096 bins
                        bins, histo = motion_interface.bytes_to_integers(histogram)
                        if bins:
                            self.new_histogram.emit(bins)
                            continue  # Continue to next frame

                self.new_histogram.emit([])  # Emit empty on failure
            except Exception as e:
                logger.error(f"Error in capture thread: {e}")
                self.new_histogram.emit([])

            elapsed = time.time() - start_time
            if elapsed < self.frame_delay:
                time.sleep(self.frame_delay - elapsed)

    def stop(self):
        self.running = False
        self.wait(500)


class MOTIONConnector(QObject):
    # Ensure signals are correctly defined
    signalConnected = pyqtSignal(str, str)  # (descriptor, port)
    signalDisconnected = pyqtSignal(str, str)  # (descriptor, port)
    signalDataReceived = pyqtSignal(str, str)  # (descriptor, data)

    consoleDeviceInfoReceived = pyqtSignal(str, str, str)
    sensorDeviceInfoReceived = pyqtSignal(str, str)
    # Target-aware variant for pages that need both left/right info simultaneously
    sensorDeviceInfoReceivedEx = pyqtSignal(str, str, str)
    temperatureSensorUpdated = pyqtSignal(float)  # (imu_temp)
    accelerometerSensorUpdated = pyqtSignal(int, int, int)  # (imu_accel)
    gyroscopeSensorUpdated = pyqtSignal(int, int, int)  # (imu_accel)

    cameraConfigUpdated = pyqtSignal(int, bool)  # camera_mask, passed=True/False
    histogramCaptureCompleted = pyqtSignal(
        int, float, float, str
    )  # (camera_index, weighted_mean, std_dev, result: "PASS"|"FAIL"|"LOW_LIGHT")
    cameraPowerStatusUpdated = pyqtSignal(list)  # (power_status_list)
    csvOutputDirectoryChanged = pyqtSignal(str)  # (directory_path)

    triggerStateChanged = pyqtSignal(str)  # 🔹 New signal for trigger state change

    connectionStatusChanged = pyqtSignal()  # 🔹 New signal for connection updates
    consoleTemperatureUpdated = pyqtSignal(float, float, float)  # (temp1, temp2, temp3)

    laserStateChanged = pyqtSignal(bool)  # 🔹 New signal for laser state change
    safetyFailureStateChanged = pyqtSignal(
        bool
    )  # 🔹 New signal for safety failure state chang

    isStreamingChanged = pyqtSignal()

    stateChanged = pyqtSignal()  # Notifies QML when state changes
    rgbStateReceived = pyqtSignal(int, str)  # Emit both integer value and text
    fanSpeedsReceived = pyqtSignal(int)  # Emit both integers
    fpgaVersionsReceived = pyqtSignal(
        "QVariant"
    )  # {"TA": str, "Seed": str, "SafetyEE": str, "SafetyOPT": str}

    histogramReady = pyqtSignal(list)  # Emit 1024 bins to QML
    latestVersionInfoReceived = pyqtSignal(
        "QVariant"
    )  # emits dict with latest/releases
    latestSensorVersionInfoReceived = pyqtSignal(str, "QVariant")  # (target, info)
    latestFpgaVersionInfoReceived = pyqtSignal(
        "QVariant"
    )  # {"TA": {...}, "SEED": {...}, "SAFETY": {...}}
    updateCapStatus = pyqtSignal(str)

    # Firmware update signals (download -> confirm -> DFU flash)
    consoleFirmwareUpdateBusyChanged = pyqtSignal()
    # Emits: target, stage, percent, message
    consoleFirmwareUpdateProgress = pyqtSignal(str, str, int, str)
    # Emits: token, tag, filename, target
    consoleFirmwareDownloadReady = pyqtSignal(str, str, str, str)
    # Emits: target, success, message
    consoleFirmwareUpdateFinished = pyqtSignal(str, bool, str)
    # Emits: target, message
    consoleFirmwareUpdateError = pyqtSignal(str, str)

    # FPGA update signals
    fpgaFirmwareUpdateBusyChanged = pyqtSignal()
    fpgaFirmwareVerifyEnabledChanged = pyqtSignal()
    # Emits: target, percent, message
    fpgaFirmwareUpdateProgress = pyqtSignal(str, int, str)
    # Emits: target, success, message
    fpgaFirmwareUpdateFinished = pyqtSignal(str, bool, str)
    # Emits: target, message
    fpgaFirmwareUpdateError = pyqtSignal(str, str)

    tcmChanged = pyqtSignal()
    tclChanged = pyqtSignal()
    pdcChanged = pyqtSignal()
    pduMonChanged = pyqtSignal()

    tecStatusChanged = pyqtSignal()
    tecDacChanged = pyqtSignal()

    tecTripValueChanged = pyqtSignal()
    taGainSetFailed = pyqtSignal(str)

    userConfigLoaded = pyqtSignal(
        float, float, float, float, float
    )  # tec_trip, opt_gain, opt_thresh, ee_gain, ee_thresh
    userConfigError = pyqtSignal(str)

    def __init__(self, config_dir="config", log_level=logging.INFO):
        super().__init__()
        self._interface = motion_interface

        self._tec_trip_value = 0

        # Configure logging with the provided level
        self._configure_logging(log_level)

        # Initialize CSV output directory to user's home directory
        import os

        self._csv_output_directory = os.path.expanduser("~")

        # Check if console and sensor are connected
        console_connected, left_sensor_connected, right_sensor_connected = (
            motion_interface.is_device_connected()
        )

        self._leftSensorConnected = left_sensor_connected
        self._rightSensorConnected = right_sensor_connected
        self._consoleConnected = console_connected
        self._laserOn = False
        self._safetyFailure = False
        self._running = False
        self._trigger_state = "OFF"
        self._state = DISCONNECTED
        self._i2c_mutex = QMutex()
        self._is_streaming = False
        self._capture_thread = None
        self._console_status_thread = None

        # --- per-trigger run log support ---
        self._runlog_handler = None  # logging.FileHandler or None
        self._runlog_path = None  # str or None
        self._runlog_active = False  # bool

        self.laser_params = self._load_laser_params(config_dir)

        self._tcm = 0.0
        self._tcl = 0.0
        self._pdc = 0.0

        self._tec_voltage = 0.0
        self._tec_temp = 0.0
        self._tec_monV = 0.0
        self._tec_monC = 0.0
        self._tec_good = False

        self._tec_dac = 0.0

        self._pdu_raws = [0] * 16
        self._pdu_vals = [0.0] * 16

        self._console_mutex = QRecursiveMutex()

        # Console firmware update state
        self._console_fw_busy = False
        # token -> (dir_path, bin_path, cleanup, target)
        self._fw_temp_files: dict[str, tuple[str, str, bool, str]] = {}
        self._fw_download_thread: _ConsoleFirmwareDownloadThread | None = None
        self._fw_flash_thread: _ConsoleFirmwareFlashThread | None = None
        self._fpga_fw_busy = False
        self._fpga_fw_verify = False
        self._fpga_update_thread: _ConsoleFpgaUpdateThread | None = None

        # Sensor mutexes for left and right sensors (following console mutex pattern)
        self._left_sensor_mutex = QRecursiveMutex()
        self._right_sensor_mutex = QRecursiveMutex()

        self.connect_signals()

    @pyqtProperty(bool, notify=consoleFirmwareUpdateBusyChanged)
    def consoleFirmwareUpdateBusy(self) -> bool:
        return bool(getattr(self, "_console_fw_busy", False))

    @pyqtProperty(bool, notify=fpgaFirmwareUpdateBusyChanged)
    def fpgaFirmwareUpdateBusy(self) -> bool:
        return bool(getattr(self, "_fpga_fw_busy", False))

    @pyqtProperty(bool, notify=fpgaFirmwareVerifyEnabledChanged)
    def fpgaFirmwareVerifyEnabled(self) -> bool:
        return bool(getattr(self, "_fpga_fw_verify", False))

    @fpgaFirmwareVerifyEnabled.setter
    def fpgaFirmwareVerifyEnabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if getattr(self, "_fpga_fw_verify", False) == enabled:
            return
        self._fpga_fw_verify = enabled
        logger.info(f"[FPGA-UPD] verify toggle set to {enabled}")
        self.fpgaFirmwareVerifyEnabledChanged.emit()

    def _set_console_fw_busy(self, busy: bool) -> None:
        if getattr(self, "_console_fw_busy", False) == busy:
            return
        self._console_fw_busy = busy
        self.consoleFirmwareUpdateBusyChanged.emit()

    def _set_fpga_fw_busy(self, busy: bool) -> None:
        if getattr(self, "_fpga_fw_busy", False) == busy:
            return
        self._fpga_fw_busy = busy
        self.fpgaFirmwareUpdateBusyChanged.emit()

    def _cleanup_fw_token(self, token: str) -> None:
        try:
            dir_path, bin_path, do_cleanup, _ = self._fw_temp_files.pop(token)
        except Exception:
            return
        if not do_cleanup:
            return
        try:
            if os.path.exists(bin_path):
                os.remove(bin_path)
        except Exception:
            pass
        try:
            if os.path.isdir(dir_path):
                os.rmdir(dir_path)
        except Exception:
            pass

    @pyqtSlot(str)
    def beginConsoleFirmwareDownload(self, tag: str) -> None:
        """Download motion-console-fw.bin for the selected release tag into a temp location."""
        logger.info(f"beginConsoleFirmwareDownload {tag}")
        target = "CONSOLE"
        if not tag or tag == "N/A":
            self.consoleFirmwareUpdateError.emit(target, "No release tag selected.")
            return
        if self.consoleFirmwareUpdateBusy:
            self.consoleFirmwareUpdateError.emit(
                target, "A firmware update is already in progress."
            )
            return

        self._set_console_fw_busy(True)
        filename = "motion-console-fw.bin"

        self._fw_download_thread = _ConsoleFirmwareDownloadThread(
            self, tag, filename, target
        )
        self._fw_download_thread.progress.connect(
            lambda pct, msg: self.consoleFirmwareUpdateProgress.emit(
                target, "download", int(pct), str(msg)
            )
        )
        self._fw_download_thread.ready.connect(self._on_console_fw_download_ready)
        self._fw_download_thread.failed.connect(
            lambda msg: self._on_console_fw_failed(msg, target)
        )
        self._fw_download_thread.finished.connect(
            lambda: setattr(self, "_fw_download_thread", None)
        )
        self._fw_download_thread.start()

    @pyqtSlot(str, str)
    def beginDeviceFirmwareDownload(self, target: str, tag: str) -> None:
        """Generic: download firmware binary for a device target (CONSOLE, SENSOR_LEFT, SENSOR_RIGHT)."""
        logger.info(f"beginDeviceFirmwareDownload target={target} tag={tag}")
        if target not in ("CONSOLE", "SENSOR_LEFT", "SENSOR_RIGHT"):
            self.consoleFirmwareUpdateError.emit(target, "Invalid update target.")
            return
        if not tag or tag == "N/A":
            self.consoleFirmwareUpdateError.emit(target, "No release tag selected.")
            return
        if self.consoleFirmwareUpdateBusy:
            self.consoleFirmwareUpdateError.emit(
                target, "A firmware update is already in progress."
            )
            return

        self._set_console_fw_busy(True)
        filename = (
            "motion-console-fw.bin" if target == "CONSOLE" else "motion-sensor-fw.bin"
        )

        self._fw_download_thread = _ConsoleFirmwareDownloadThread(
            self, tag, filename, target
        )
        self._fw_download_thread.progress.connect(
            lambda pct, msg: self.consoleFirmwareUpdateProgress.emit(
                target, "download", int(pct), str(msg)
            )
        )
        self._fw_download_thread.ready.connect(self._on_console_fw_download_ready)
        self._fw_download_thread.failed.connect(
            lambda msg: self._on_console_fw_failed(msg, target)
        )
        self._fw_download_thread.finished.connect(
            lambda: setattr(self, "_fw_download_thread", None)
        )
        self._fw_download_thread.start()

    @pyqtSlot(str, str)
    def beginDeviceFirmwareFromLocal(self, target: str, local_path: str) -> None:
        """Register a local firmware file for the specified target and emit download-ready.

        The QML side will receive the same ready signal and show the confirm dialog.
        """
        logger.info(f"beginDeviceFirmwareFromLocal target={target} path={local_path}")
        if target not in ("CONSOLE", "SENSOR_LEFT", "SENSOR_RIGHT"):
            self.consoleFirmwareUpdateError.emit(target, "Invalid update target.")
            return
        try:
            p = Path(local_path)
            if not p.exists():
                self.consoleFirmwareUpdateError.emit(
                    target, "Selected file does not exist."
                )
                return
            fname = p.name
            # Validate filename
            if target == "CONSOLE":
                if fname != "motion-console-fw.bin":
                    self.consoleFirmwareUpdateError.emit(
                        target, "Filename must be motion-console-fw.bin"
                    )
                    return
            else:
                if fname != "motion-sensor-fw.bin":
                    self.consoleFirmwareUpdateError.emit(
                        target, "Filename must be motion-sensor-fw.bin"
                    )
                    return

            token = uuid.uuid4().hex
            # store (dir_path, bin_path, do_cleanup=False, target)
            self._fw_temp_files[token] = (
                str(p.parent),
                str(p.resolve()),
                False,
                target,
            )
            # Use tag 'local' to indicate uploaded file
            tag = "local"
            self._set_console_fw_busy(True)
            # Emit ready so QML shows the confirm dialog
            self.consoleFirmwareDownloadReady.emit(token, tag, fname, target)
        except Exception as e:
            logger.error(f"beginDeviceFirmwareFromLocal error: {e}")
            self.consoleFirmwareUpdateError.emit(target, str(e))

    def _on_console_fw_download_ready(
        self, token: str, tag: str, filename: str, target: str
    ) -> None:
        self.consoleFirmwareDownloadReady.emit(token, tag, filename, target)

    def _on_console_fw_failed(self, message: str, target: str = "CONSOLE") -> None:
        self.consoleFirmwareUpdateError.emit(target, message)
        self._set_console_fw_busy(False)

    @pyqtSlot(str)
    def cancelConsoleFirmwareUpdate(self, token: str) -> None:
        """Cancel after download/confirmation (cleans up temp file)."""
        self._cleanup_fw_token(token)
        self._set_console_fw_busy(False)

    @pyqtSlot(str)
    def startConsoleFirmwareUpdate(self, token: str) -> None:
        """Flash the previously-downloaded firmware using DFU."""
        if not token or token not in self._fw_temp_files:
            self.consoleFirmwareUpdateError.emit(
                "CONSOLE", "Firmware download token is missing/invalid."
            )
            self._set_console_fw_busy(False)
            return
        if self._fw_flash_thread is not None:
            self.consoleFirmwareUpdateError.emit(
                "CONSOLE", "Firmware flashing is already in progress."
            )
            return
        _, bin_path, _, target = self._fw_temp_files[token]
        if not os.path.exists(bin_path):
            self.consoleFirmwareUpdateError.emit(
                target, "Downloaded firmware file is missing."
            )
            self._cleanup_fw_token(token)
            self._set_console_fw_busy(False)
            return
        self._fw_flash_thread = _DeviceFirmwareFlashThread(self, bin_path, target)
        self._fw_flash_thread.progress.connect(
            lambda pct, msg: self.consoleFirmwareUpdateProgress.emit(
                target, "flash", int(pct), str(msg)
            )
        )
        self._fw_flash_thread.finished_ok.connect(
            lambda: self._on_console_fw_finished(
                token, True, "Firmware updated successfully.", target
            )
        )
        self._fw_flash_thread.failed.connect(
            lambda msg: self._on_console_fw_finished(token, False, str(msg), target)
        )
        self._fw_flash_thread.finished.connect(
            lambda: setattr(self, "_fw_flash_thread", None)
        )
        self._fw_flash_thread.start()

    def _on_console_fw_finished(
        self, token: str, success: bool, message: str, target: str = "CONSOLE"
    ) -> None:
        self._cleanup_fw_token(token)
        self.consoleFirmwareUpdateFinished.emit(target, bool(success), str(message))
        self._set_console_fw_busy(False)

    def _configure_logging(self, log_level):
        """Configure logging for motion_connector with the specified log level."""
        global logger, run_logger

        # Get logger instance
        logger = logging.getLogger("ow-testapp")
        logger.setLevel(log_level)

        # Common formatter
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

        # Configure handlers - ensure console output for debug messages
        if not logger.hasHandlers():
            # Check if root logger has handlers (main.py configured logging)
            root_logger = logging.getLogger()
            if root_logger.handlers:
                # Let messages propagate to root logger (main.py handles console/file output)
                logger.propagate = True
            else:
                # No root handlers, set up our own console handler
                console_handler = logging.StreamHandler()
                console_handler.setLevel(log_level)
                console_handler.setFormatter(formatter)
                logger.addHandler(console_handler)

                # Also add file handler for local logging
                run_dir = os.path.join(os.getcwd(), "app-logs")
                os.makedirs(run_dir, exist_ok=True)

                # Build timestamp like 20251029_124455
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

                # ow-testapp-<ts>.log
                logfile_path = os.path.join(run_dir, f"ow-testapp-{ts}.log")

                file_handler = logging.FileHandler(
                    logfile_path, mode="w", encoding="utf-8"
                )
                file_handler.setLevel(log_level)
                file_handler.setFormatter(formatter)
                logger.addHandler(file_handler)

                # Optional: announce where we're logging
                logger.info(f"logging to {logfile_path}")

        # Run logger (ONLY writes to run.log, no console spam)
        run_logger = logging.getLogger("runlog")
        run_logger.setLevel(log_level)
        run_logger.propagate = False

        # --- Load RT model (10K3CG_R-T.CSV) for TEC lookup ---
        try:
            # Look for file in the repository's models directory next to this file
            base_dir = os.path.dirname(__file__)
            candidate = os.path.join(base_dir, "models", "10K3CG_R-T.CSV")
            if not os.path.exists(candidate):
                # try lower-case extension variant
                candidate = os.path.join(base_dir, "models", "10K3CG_R-T.csv")

            if os.path.exists(candidate):
                df = pd.read_csv(candidate)
                self._data_RT = np.array(df)
                logger.info(
                    f"Loaded RT model from {candidate} shape={self._data_RT.shape}"
                )
            else:
                self._data_RT = None
                logger.warning(f"RT model file not found at {candidate}")
        except Exception as e:
            self._data_RT = None
            logger.error(f"Failed to load RT model: {e}")

    # --- SCAN MANAGEMENT METHODS ---
    @pyqtSlot(result=list)
    def _load_laser_params(self, config_dir):

        config_path = (
            resource_path("config", "laser_params.json")
            if config_dir == "config"
            else Path(config_dir) / "laser_params.json"
        )
        if not config_path.exists():
            logger.error(f"[Connector] Laser parameter file not found: {config_path}")
            return []

        try:
            with open(config_path, "r") as f:
                params = json.load(f)
            logger.info(
                f"[Connector] Loaded {len(params)} laser parameter sets from {config_path}"
            )
            return params
        except FileNotFoundError:
            logger.error(f"[Connector] Laser parameter file not found: {config_path}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"[Connector] Invalid JSON in {config_path}: {e}")
            return []

    def connect_signals(self):
        """Connect LIFUInterface signals to QML."""
        motion_interface.signal_connect.connect(self.on_connected)
        motion_interface.signal_disconnect.connect(self.on_disconnected)
        motion_interface.signal_data_received.connect(self.on_data_received)

    def _get_fpga_scale(self, label: str, name: str):
        """Retrieve the scale factor for a given `label` and function `name` from models/FpgaModel.js.

        Returns float scale on success or None on failure. Caches results on the instance.
        """
        key = (label, name)
        if getattr(self, "_fpga_scale_cache", None) is None:
            self._fpga_scale_cache = {}
        if key in self._fpga_scale_cache:
            return self._fpga_scale_cache[key]

        try:
            import re

            model_path = os.path.join(
                os.path.dirname(__file__), "models", "FpgaModel.js"
            )
            with open(model_path, "r", encoding="utf-8") as f:
                txt = f.read()

            # Find the label block first
            label_re = re.compile(
                r'label\s*:\s*"'
                + re.escape(label)
                + r'"\s*,.*?functions\s*:\s*\[(.*?)\]',
                re.S,
            )
            m_label = label_re.search(txt)
            if not m_label:
                return None

            functions_block = m_label.group(1)

            # Find the function entry with the given name and extract scale
            fn_re = re.compile(
                r'\{[^}]*name\s*:\s*"'
                + re.escape(name)
                + r'"[^}]*scale\s*:\s*([0-9]+(?:\.[0-9]+)?)',
                re.S,
            )
            m_fn = fn_re.search(functions_block)
            if not m_fn:
                return None

            scale = float(m_fn.group(1))
            self._fpga_scale_cache[key] = scale
            return scale

        except Exception as e:
            logging.debug(f"Failed to read FPGA model scale for {label}/{name}: {e}")
            return None

    def _get_sensor_mutex(self, sensor_tag: str) -> QRecursiveMutex:
        """Get the appropriate mutex for the given sensor."""
        if sensor_tag == "SENSOR_LEFT":
            return self._left_sensor_mutex
        elif sensor_tag == "SENSOR_RIGHT":
            return self._right_sensor_mutex
        else:
            raise ValueError(f"Invalid sensor tag: {sensor_tag}")

    def _get_sensor_side(self, sensor_tag: str) -> str:
        """Convert sensor tag to sensor side string."""
        if sensor_tag == "SENSOR_LEFT":
            return "left"
        elif sensor_tag == "SENSOR_RIGHT":
            return "right"
        else:
            raise ValueError(f"Invalid sensor tag: {sensor_tag}")

    def _start_runlog(self):
        """
        Create a dedicated run log file and attach it to the global logger
        so that all logger.info / logger.error etc. also go into this file
        while the trigger is running.
        """
        if self._runlog_active:
            # Already running; nothing to do
            return

        # Directory for individual trigger runs
        run_dir = os.path.join(os.getcwd(), "run-logs")
        os.makedirs(run_dir, exist_ok=True)

        # Timestamped filename for this specific trigger session
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self._runlog_path = os.path.join(run_dir, f"run-{ts}.log")

        # Create handler
        run_handler = logging.FileHandler(self._runlog_path, mode="w", encoding="utf-8")
        # Match the global formatter you already defined at top of file
        run_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )

        run_handler.setLevel(logging.INFO)

        # Attach this handler to run_logger ONLY
        run_logger.addHandler(run_handler)

        # Save so we can remove/close it later
        self._runlog_handler = run_handler
        self._runlog_active = True

        # --- Gather version info for header ---
        # SDK version (MOTION SDK / sensor SDK)
        try:
            sdk_ver = (
                self._interface.get_sdk_version()
            )  # same as get_sdk_version() slot :contentReference[oaicite:4]{index=4}
        except Exception as e:
            sdk_ver = f"ERROR({e})"

        # App version (from main application). Read from QGuiApplication property if available.
        try:
            app = QGuiApplication.instance()
            app_ver = app.property("appVersion") if app is not None else None
            if app_ver is None:
                app_ver = "unknown"
        except Exception as e:
            app_ver = f"ERROR({e})"

        # Console firmware version (from console module) :contentReference[oaicite:5]{index=5}
        try:
            # _console_mutex is a QRecursiveMutex so re-locking is safe if we're already in startTrigger
            self._console_mutex.lock()
            try:
                fw_ver = motion_interface.console_module.get_version()
            finally:
                self._console_mutex.unlock()
        except Exception as e:
            fw_ver = f"ERROR({e})"

        #
        # Write session header into the run log
        #
        run_logger.info("========== RUN START ==========")
        run_logger.info(f"App Version: {app_ver}")
        run_logger.info(f"SDK Version: {sdk_ver}")
        run_logger.info(f"Console Firmware: {fw_ver}")
        run_logger.info("================================")

        # Also drop a breadcrumb to the main logger so humans see it in console/UI log:
        logger.info(f"[RUNLOG] started -> {self._runlog_path}")

    def _stop_runlog(self):
        """
        Detach and close the per-run file handler.
        """
        if not self._runlog_active or self._runlog_handler is None:
            return

        # Mark end of run in the run log
        run_logger.info(f"[RUNLOG] Trigger run logging stopped -> {self._runlog_path}")
        run_logger.info("========== RUN END ==========")

        # Also note it in the main logger (console/app log)
        logger.info(f"[RUNLOG] stopped -> {self._runlog_path}")

        # 1. Remove handler from run_logger
        try:
            run_logger.removeHandler(self._runlog_handler)
        except Exception as e:
            logger.error(f"Error detaching run log handler: {e}")

        # 2. Close the handler so the file is flushed and released
        try:
            self._runlog_handler.close()
        except Exception as e:
            logger.error(f"Error closing run log handler: {e}")

        # 3. Clear state
        self._runlog_handler = None
        self._runlog_path = None
        self._runlog_active = False

    @pyqtSlot(result=bool)
    def setLaserPowerFromConfig(self) -> bool:
        """Apply laser power parameters loaded at startup."""
        try:
            return self.set_laser_power_from_config(self._interface)
        except Exception as e:
            logger.error(f"setLaserPowerFromConfig error: {e}")
            return False

    def set_laser_power_from_config(self, interface):
        logger.info("[Connector] Setting laser power from config...")

        # ------------------------------------------------------------------
        # Read user config to discover which laser_params.json entries should
        # be skipped and replaced by user-defined values.
        # EE_THRESH / EE_GAIN  → Safety EE  DRIVE CL (channel 6, offset 0x10)
        # OPT_THRESH / OPT_GAIN → Safety OPT DRIVE CL (channel 7, offset 0x10)
        # ------------------------------------------------------------------
        user_cfg: dict = {}
        try:
            cfg_obj = interface.console_module.read_config()
            if cfg_obj is not None:
                user_cfg = cfg_obj.json_data or {}
        except Exception as _e:
            logger.warning(
                f"[Connector] Could not read user config before laser init: {_e}"
            )

        ee_thresh = user_cfg.get("EE_THRESH")
        ee_gain = user_cfg.get("EE_GAIN")
        opt_thresh = user_cfg.get("OPT_THRESH")
        opt_gain = user_cfg.get("OPT_GAIN")

        # (channel, offset) entries to skip in the JSON pass
        _EE_DRIVE_CL = (6, 0x10)  # Safety EE  DRIVE CL
        _OPT_DRIVE_CL = (7, 0x10)  # Safety OPT DRIVE CL

        skip_entries: set = set()
        if ee_thresh is not None or ee_gain is not None:
            skip_entries.add(_EE_DRIVE_CL)
        if opt_thresh is not None or opt_gain is not None:
            skip_entries.add(_OPT_DRIVE_CL)

        self._console_mutex.lock()
        try:
            for idx, laser_param in enumerate(self.laser_params, start=1):
                muxIdx = laser_param["muxIdx"]
                channel = laser_param["channel"]
                i2cAddr = laser_param["i2cAddr"]
                offset = laser_param["offset"]
                dataToSend = bytearray(laser_param["dataToSend"])

                if (channel, offset) in skip_entries:
                    logger.info(
                        f"[Connector] Skipping JSON entry ch={channel} off=0x{offset:02X} "
                        f"(overridden by user config)"
                    )
                    continue

                logger.info(
                    f"[Connector] ({idx}/{len(self.laser_params)}) "
                    f"Writing I2C: muxIdx={muxIdx}, channel={channel}, "
                    f"i2cAddr=0x{i2cAddr:02X}, offset=0x{offset:02X}, "
                    f"data={list(dataToSend)}"
                )

                if not interface.console_module.write_i2c_packet(
                    mux_index=muxIdx,
                    channel=channel,
                    device_addr=i2cAddr,
                    reg_addr=offset,
                    data=dataToSend,
                ):
                    logger.error(
                        f"Failed to set laser power (muxIdx={muxIdx}, channel={channel})"
                    )
                    return False

            # ------------------------------------------------------------------
            # Write user-config DRIVE CL overrides after the JSON pass.
            # Default scale matches the static FpgaModel value (1.86 mA/LSB).
            # Data format: 16-bit LSB-first (isMsbFirst=false in FpgaModel).
            # thresh is a raw uint16 register value; gain is a float scale factor
            # used only for the QML FpgaData scale override.
            # ------------------------------------------------------------------

            def _write_drive_cl(ch: int, thresh, gain: float, label: str) -> bool:
                if thresh is None:
                    return True
                set_value = thresh
                gain_f = float(gain) if gain is not None else 0.0
                if gain_f != 0.0:
                    set_value = thresh/gain_f
                raw = max(0, min(0xFFFF, int(round(set_value))))  # uint16 raw value
                data = bytearray([raw & 0xFF, (raw >> 8) & 0xFF])  # LSB first

                logger.info(
                    f"[Connector] Writing user-config {label} DRIVE CL: "
                    f"raw={raw}, gain={gain_f} → {list(data)}"
                )
                return interface.console_module.write_i2c_packet(
                    mux_index=1, channel=ch, device_addr=0x41, reg_addr=0x10, data=data
                )

            if not _write_drive_cl(6, ee_thresh, ee_gain, "Safety EE"):
                logger.error("Failed to write user-config Safety EE DRIVE CL")
                return False
            if not _write_drive_cl(7, opt_thresh, opt_gain, "Safety OPT"):
                logger.error("Failed to write user-config Safety OPT DRIVE CL")
                return False

            logger.info("Laser power set successfully.")
        finally:
            self._console_mutex.unlock()

        return True

    @pyqtProperty(str, notify=csvOutputDirectoryChanged)
    def csvOutputDirectory(self):
        """Get the current CSV output directory."""
        return self._csv_output_directory

    @csvOutputDirectory.setter
    def csvOutputDirectory(self, directory):
        """Set the CSV output directory."""
        if directory != self._csv_output_directory:
            self._csv_output_directory = directory
            self.csvOutputDirectoryChanged.emit(directory)
            logger.info(f"CSV output directory changed to: {directory}")

    @pyqtSlot()
    def selectCsvOutputDirectory(self):
        """Signal QML to open directory selection dialog."""
        # Emit signal to trigger QML folder dialog
        self.csvOutputDirectoryChanged.emit("SELECT_DIRECTORY")

    @pyqtSlot(str)
    def setCsvOutputDirectory(self, directory):
        """Set the CSV output directory from QML."""
        if directory and directory != "SELECT_DIRECTORY":
            self.csvOutputDirectory = directory

    def update_state(self):
        """Update system state based on connection and configuration."""
        if not self._consoleConnected and (
            (not self._leftSensorConnected) or (not self._rightSensorConnected)
        ):
            self._state = DISCONNECTED
        elif self._leftSensorConnected and not self._consoleConnected:
            self._state = SENSOR_CONNECTED
        elif self._consoleConnected and not self._leftSensorConnected:
            self._state = CONSOLE_CONNECTED
        elif self._consoleConnected and self._leftSensorConnected:
            self._state = READY
        elif self._consoleConnected and self._leftSensorConnected and self._running:
            self._state = RUNNING
        self.stateChanged.emit()  # Notify QML of state update
        logger.info(f"Updated state: {self._state}")

    @property
    def interface(self):
        return motion_interface

    @pyqtProperty(bool, notify=connectionStatusChanged)
    def leftSensorConnected(self):
        """Expose Sensor connection status to QML."""
        return self._leftSensorConnected

    @pyqtProperty(bool, notify=connectionStatusChanged)
    def rightSensorConnected(self):
        """Expose Sensor connection status to QML."""
        return self._rightSensorConnected

    @pyqtProperty(bool, notify=connectionStatusChanged)
    def consoleConnected(self):
        """Expose Console connection status to QML."""
        return self._consoleConnected

    @pyqtProperty(bool, notify=laserStateChanged)
    def laserOn(self):
        """Expose Console connection status to QML."""
        return self._laserOn

    @pyqtProperty(bool, notify=safetyFailureStateChanged)
    def safetyFailure(self):
        """Expose Console connection status to QML."""
        return self._safetyFailure

    @pyqtProperty(int, notify=stateChanged)
    def state(self):
        """Expose state as a QML property."""
        return self._state

    @pyqtProperty(float, notify=tcmChanged)
    def tcm(self):
        return self._tcm

    @pyqtProperty(float, notify=tclChanged)
    def tcl(self):
        return self._tcl

    @pyqtProperty(float, notify=pdcChanged)
    def pdc(self):
        return self._pdc

    @pyqtProperty(bool, notify=isStreamingChanged)
    def isStreaming(self):
        return self._is_streaming

    @pyqtProperty(str, notify=triggerStateChanged)
    def triggerState(self):
        return self._trigger_state

    @pyqtProperty(float, notify=tecStatusChanged)
    def tecVoltage(self):
        return self._tec_voltage

    @pyqtProperty(float, notify=tecStatusChanged)
    def tecTemp(self):
        return self._tec_temp

    @pyqtProperty(float, notify=tecStatusChanged)
    def tecMonV(self):
        return self._tec_monV

    @pyqtProperty(float, notify=tecStatusChanged)
    def tecMonC(self):
        return self._tec_monC

    @pyqtProperty(bool, notify=tecStatusChanged)
    def tecGood(self):
        return self._tec_good

    @pyqtProperty(float, notify=tecDacChanged)
    def tecDAC(self):
        return self._tec_dac

    @pyqtSlot(result=str)
    def get_sdk_version(self):
        return self._interface.get_sdk_version()

    @pyqtProperty(QVariant, notify=pduMonChanged)
    def pduRaws(self):
        return self._pdu_raws

    @pyqtProperty(QVariant, notify=pduMonChanged)
    def pduVals(self):
        return self._pdu_vals

    @pyqtProperty(QVariant, notify=pduMonChanged)
    def adc0Vals(self):
        return self._pdu_vals[:8]

    @pyqtProperty(QVariant, notify=pduMonChanged)
    def adc1Vals(self):
        return self._pdu_vals[8:]

    @pyqtSlot(str)
    def powerCamerasOn(self, target: str):
        """Enable power to all cameras on all connected sensors (equivalent to scripts/enable_camera_power.py --mask 0xFF)."""
        try:
            MASK_ALL = 0xFF
            logger.info(
                f"Enabling camera power mask=0x{MASK_ALL:02X} on {target.capitalize()}"
            )

            ok = motion_interface.sensors[target].enable_camera_power(MASK_ALL)
            if ok:
                logger.info(f"{target.capitalize()}: Power enabled")
            else:
                logger.error(f"{target.capitalize()}: Failed to enable power")
        except Exception as e:
            logger.error(f"Error enabling camera power: {e}")

    @pyqtSlot(str)
    def powerCamerasOff(self, target: str):
        """Disable power to all cameras on all connected sensors (equivalent to scripts/disable_camera_power.py --mask 0xFF)."""
        try:
            MASK_ALL = 0xFF
            logger.info(
                f"Disabling camera power mask=0x{MASK_ALL:02X} on {target.capitalize()}"
            )

            ok = motion_interface.sensors[target].disable_camera_power(MASK_ALL)
            if ok:
                logger.info(f"{target.capitalize()}: Power disabled")
            else:
                logger.error(f"{target.capitalize()}: Failed to disable power")
        except Exception as e:
            logger.error(f"Error disabling camera power: {e}")

    @pyqtSlot(str, int, str, bool)
    def captureHistogramToCSV(
        self,
        sensor_tag: str,
        camera_index: int,
        serial_number: str,
        is_dark: bool = False,
    ):
        """Capture histogram from selected camera and save as CSV file named with serial number."""
        try:
            sensor_side = self._get_sensor_side(sensor_tag)
            mutex = self._get_sensor_mutex(sensor_tag)

            mutex.lock()
            try:
                capture_type = "dark histogram" if is_dark else "histogram"
                logger.info(
                    f"Capturing {capture_type} for {sensor_side} camera {camera_index} with SN {serial_number}"
                )

                # Single camera
                bins, histo = self._interface.get_camera_histogram(
                    sensor_side=sensor_side,
                    camera_id=camera_index,
                    test_pattern_id=4,
                    auto_upload=True,
                )
                if bins:
                    suffix = "_dark" if is_dark else "_light"
                    filename = f"{serial_number}_histogram{suffix}.csv"
                    bins[0] = (
                        bins[0] - 6
                    )  # delete the sentinel value from the histogram

                    # Get camera temperature
                    try:
                        temperature = self._interface.sensors[
                            sensor_side
                        ].imu_get_temperature()
                        logger.info(f"Camera temperature: {temperature}°C")
                    except Exception as e:
                        logger.error(f"Failed to get camera temperature: {e}")
                        temperature = (
                            0.0  # Fallback to 0 if temperature retrieval fails
                        )

                    # Calculate weighted mean
                    weighted_mean, std_dev = self._calculate_weighted_mean_std_dev(
                        bins[:1024]
                    )
                    print(f"Weighted mean of histogram: {weighted_mean:.2f}")
                    print(f"Standard deviation of histogram: {std_dev:.2f}")

                    # Classify histogram (light: PASS/FAIL/LOW_LIGHT; dark: PASS only, not saved as "result")
                    result = "PASS"  # Default for dark or on error
                    if not is_dark:
                        try:
                            histogram_bins = bins[:1024]
                            if histogram_bins and len(histogram_bins) == 1024:
                                result = classify_histogram(
                                    histogram_bins, is_light_histogram=True
                                )
                                if result == "LOW_LIGHT":
                                    logger.warning(
                                        f"Light histogram mean {weighted_mean:.1f} < 75 for camera {camera_index + 1}: Low Light — not saving."
                                    )
                                elif result == "FAIL":
                                    logger.info(
                                        f"Histogram classified as non-normal for camera {camera_index + 1}"
                                    )
                                else:
                                    logger.debug(
                                        f"Histogram classified as normal for camera {camera_index + 1}"
                                    )
                        except Exception as e:
                            logger.error(f"Error classifying histogram: {e}")
                            result = "PASS"

                    if result != "LOW_LIGHT":
                        self._save_histogram_csv(
                            bins, filename, temperature, camera_index
                        )
                        logger.info(f"Saved {capture_type} to {filename}")
                    # Emit signal with weighted mean and classification result for async UI update
                    self.histogramCaptureCompleted.emit(
                        camera_index, weighted_mean, std_dev, result
                    )
                else:
                    logger.error(
                        f"Failed to get {capture_type} for camera {camera_index + 1}"
                    )

            finally:
                mutex.unlock()

        except Exception as e:
            logger.error(f"Error capturing {capture_type}: {e}")

    @pyqtSlot(str, bool, "QStringList")
    def captureAllCamerasHistogramToCSV(
        self, sensor_tag: str, is_dark: bool = False, serial_numbers: list = None
    ):
        """Capture histogram from all cameras and save each with individual serial numbers."""
        try:
            sensor_side = self._get_sensor_side(sensor_tag)
            mutex = self._get_sensor_mutex(sensor_tag)

            mutex.lock()
            try:
                capture_type = "dark histograms" if is_dark else "histograms"
                logger.info(
                    f"Capturing {capture_type} for all cameras on {sensor_side}"
                )

                # Map camera indices to their display order (same as in QML)
                camera_mapping = [
                    0,
                    7,
                    1,
                    6,
                    2,
                    5,
                    3,
                    4,
                ]  # Left column: 1,2,3,4; Right column: 8,7,6,5

                for display_idx, camera_idx in enumerate(camera_mapping):
                    self.captureHistogramToCSV(
                        sensor_tag,
                        camera_idx,
                        serial_numbers[display_idx] if serial_numbers else "",
                        is_dark,
                    )
            finally:
                mutex.unlock()
        except Exception as e:
            logger.error(f"Error capturing {capture_type}: {e}")

    def _save_histogram_csv(self, bins, filename, temperature=0.0, camera_index=0):
        """Helper method to save histogram data to CSV file with incremental counter to prevent overwriting."""
        try:
            import os
            import csv
            import datetime

            # Create filename with timestamp if serial_number is empty
            if not filename or filename.startswith("_histogram"):
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"histogram_{timestamp}.csv"

            # Ensure filename has .csv extension
            if not filename.endswith(".csv"):
                filename += ".csv"

            # Generate unique filename with incremental counter if file exists
            base_filename = filename
            counter = 1

            while True:
                filepath = os.path.join(self._csv_output_directory, filename)
                if not os.path.exists(filepath):
                    break

                # File exists, increment counter and try again
                name_part = base_filename.rsplit(".", 1)[0]  # Remove .csv extension
                extension = (
                    base_filename.rsplit(".", 1)[1] if "." in base_filename else "csv"
                )
                filename = f"{name_part}_{counter}.{extension}"
                counter += 1

            with open(filepath, "w", newline="") as csvfile:
                writer = csv.writer(csvfile)

                # Create header row with column names
                header = ["cam_id", "frame_id"]
                # Add bin numbers (0-1023)
                header.extend([str(i) for i in range(1024)])
                header.extend(["temperature", "sum"])
                writer.writerow(header)

                # Create data row
                data_row = [camera_index, "1"]  # cam_id=1, frame_id=1
                data_row.extend(bins[:1024])  # Ensure we only take first 1024 bins
                # Pad with zeros if bins is shorter than 1024
                while len(data_row) < 1026:  # 2 + 1024
                    data_row.append(0)
                # Add temperature and sum
                data_row.extend([temperature, sum(bins[:1024])])
                writer.writerow(data_row)

            logger.info(f"Histogram saved to {filepath}")

        except Exception as e:
            logger.error(f"Failed to save histogram CSV: {e}")

    def _calculate_weighted_mean_std_dev(self, histogram_data):
        """Calculate the weighted mean and standard deviation of histogram data using numpy algorithm."""
        try:
            if (
                not histogram_data
                or len(histogram_data) == 0
                or len(histogram_data) != 1024
            ):
                return 0.0, 0.0

            # Create a copy to avoid modifying the original data
            hist = histogram_data.copy()

            # Rule 1: zero out the 1024th bin (index 1023)
            hist[1023] = 0

            # Rule 2: if a bin has less than 100 in it, set it to 0 (equivalent to noisyBinMin = 100)
            noisyBinMin = 100
            for i in range(len(hist)):
                if hist[i] < noisyBinMin:
                    hist[i] = 0

            # Create bin indices array (0 to 1023)
            bins = list(range(len(hist)))

            # Calculate weighted mean: np.dot(hist,bins)/np.sum(hist)
            weighted_sum = sum(hist[i] * bins[i] for i in range(len(hist)))
            total_count = sum(hist)

            if total_count == 0:
                return 0.0, 0.0

            mean = weighted_sum / total_count

            # Calculate bins squared: np.multiply(bins,bins)
            bins_sq = [bins[i] * bins[i] for i in range(len(bins))]

            # Calculate variance using sample formula:
            # var = (np.dot(hist,binsSq)-mean*mean*np.sum(hist))/(np.sum(hist)-1)
            hist_dot_bins_sq = sum(hist[i] * bins_sq[i] for i in range(len(hist)))
            variance = (hist_dot_bins_sq - mean * mean * total_count) / (
                total_count - 1
            )

            # Calculate standard deviation: np.sqrt(var)
            std = variance**0.5 if variance >= 0 else 0.0

            return mean, std

        except Exception as e:
            logger.error(f"Error calculating weighted mean: {e}")
            return 0.0, 0.0

    @pyqtSlot(str, str)
    def on_connected(self, descriptor, port):
        """Handle device connection."""
        print(f"Device connected: {descriptor} on port {port}")
        if descriptor.upper() == "SENSOR_LEFT":
            self._leftSensorConnected = True
        if descriptor.upper() == "SENSOR_RIGHT":
            self._rightSensorConnected = True
        elif descriptor.upper() == "CONSOLE":
            self._consoleConnected = True

        self.signalConnected.emit(descriptor, port)
        self.connectionStatusChanged.emit()
        self.update_state()

    @pyqtSlot(str, str)
    def on_disconnected(self, descriptor, port):
        """Handle device disconnection."""
        if descriptor.upper() == "SENSOR_LEFT":
            self._leftSensorConnected = False
        elif descriptor.upper() == "SENSOR_RIGHT":
            self._rightSensorConnected = False
        elif descriptor.upper() == "CONSOLE":
            self._consoleConnected = False

            # Stop status thread
            if self._console_status_thread:
                self._console_status_thread.stop()
                self._console_status_thread = None

        self.signalDisconnected.emit(descriptor, port)
        self.connectionStatusChanged.emit()
        self.update_state()

    @pyqtSlot(str, str)
    def on_data_received(self, descriptor, message):
        """Handle incoming data from the MOTION device."""
        logger.info(f"Data received from {descriptor}: {message}")
        self.signalDataReceived.emit(descriptor, message)

    @pyqtSlot(str)
    def querySensorInfo(self, target: str):
        """Fetch and emit device information with mutex protection and event-based UI updates."""
        try:
            if target == "SENSOR_LEFT" or target == "SENSOR_RIGHT":
                sensor_tag = "left" if target == "SENSOR_LEFT" else "right"
                mutex = self._get_sensor_mutex(target)

                mutex.lock()
                try:
                    fw_version = motion_interface.sensors[sensor_tag].get_version()
                    logger.info(f"Version: {fw_version}")
                    hw_id = motion_interface.sensors[sensor_tag].get_hardware_id()
                    device_id = base58.b58encode(bytes.fromhex(hw_id)).decode()
                    # Emit signal for async UI update
                    self.sensorDeviceInfoReceived.emit(fw_version, device_id)
                    self.sensorDeviceInfoReceivedEx.emit(target, fw_version, device_id)
                    logger.info(
                        f"Sensor Device Info - Firmware: {fw_version}, Device ID: {device_id}"
                    )
                finally:
                    mutex.unlock()
            else:
                logger.error(f"Invalid target for sensor info query: {target}")
                return
        except Exception as e:
            logger.error(f"Error querying device info: {e}")

    @pyqtSlot()
    def queryConsoleInfo(self):
        """Fetch and emit device information."""
        self._console_mutex.lock()
        try:
            fw_version = motion_interface.console_module.get_version()
            logger.info(f"Version: {fw_version}")
            hw_id = motion_interface.console_module.get_hardware_id()
            device_id = base58.b58encode(bytes.fromhex(hw_id)).decode()
            board_id = motion_interface.console_module.read_board_id()
            self.consoleDeviceInfoReceived.emit(fw_version, device_id, str(board_id))
            logger.info(
                f"Console Device Info - Firmware: {fw_version}, Device ID: {device_id}, Board ID: {board_id}"
            )
        except Exception as e:
            logger.error(f"Error querying device info: {e}")
        finally:
            self._console_mutex.unlock()

    @pyqtSlot()
    def queryConsoleLatestVersionInfo(self):
        """Fetch latest firmware/release info from console module and emit to QML."""
        self._console_mutex.lock()
        try:
            info = motion_interface.console_module.get_latest_version_info()
            logger.info(f"Latest version info: {info}")
            # Emit whatever structure the console module returns (QVariant-compatible)
            self.latestVersionInfoReceived.emit(info)
        except Exception as e:
            logger.error(f"Error querying latest version info: {e}")
        finally:
            self._console_mutex.unlock()

    @pyqtSlot(str)
    def querySensorLatestVersionInfo(self, target: str):
        """Fetch latest firmware/release info from a sensor module and emit to QML."""
        try:
            if target != "SENSOR_LEFT" and target != "SENSOR_RIGHT":
                logger.error(
                    f"Invalid target for sensor latest version query: {target}"
                )
                return

            sensor_tag = "left" if target == "SENSOR_LEFT" else "right"
            mutex = self._get_sensor_mutex(target)

            mutex.lock()
            try:
                # sensor modules may expose get_latest_version_info similar to console
                info = motion_interface.sensors[sensor_tag].get_latest_version_info()
                logger.info(f"Latest sensor ({sensor_tag}) version info: {info}")
                self.latestSensorVersionInfoReceived.emit(target, info)
            finally:
                mutex.unlock()
        except Exception as e:
            logger.error(f"Error querying sensor latest version info for {target}: {e}")

    @pyqtSlot()
    def queryConsoleLatestFpgaVersionInfo(self):
        """Fetch latest FPGA release info and emit only selected .jed asset fields.

        Emitted payload shape:
            {
                "TA": {"tag_name", "name", "browser_download_url", "created_at"} | {"tag_name": "N/A", ...},
                "SEED": {"tag_name", "name", "browser_download_url", "created_at"} | {"tag_name": "N/A", ...},
                "SAFETY": {"tag_name", "name", "browser_download_url", "created_at"} | {"tag_name": "N/A", ...}
            }
        """
        try:
            if GitHubReleases is None:
                logger.error(
                    "GitHubReleases is unavailable (omotion SDK not found in environment)."
                )
                self.latestFpgaVersionInfoReceived.emit(
                    {
                        "TA": {
                            "tag_name": "N/A",
                            "name": "N/A",
                            "browser_download_url": "",
                            "created_at": "",
                        },
                        "SEED": {
                            "tag_name": "N/A",
                            "name": "N/A",
                            "browser_download_url": "",
                            "created_at": "",
                        },
                        "SAFETY": {
                            "tag_name": "N/A",
                            "name": "N/A",
                            "browser_download_url": "",
                            "created_at": "",
                        },
                    }
                )
                return

            def _default_payload() -> dict:
                return {
                    "tag_name": "N/A",
                    "name": "N/A",
                    "browser_download_url": "",
                    "created_at": "",
                }

            def _pick_latest_jed_asset(gh: GitHubReleases) -> dict:
                release = gh.get_latest_release()
                if not isinstance(release, dict):
                    return _default_payload()

                assets = release.get("assets")
                if not isinstance(assets, list):
                    try:
                        assets = gh.get_asset_list(release=release)
                    except Exception:
                        assets = []

                if not isinstance(assets, list):
                    assets = []

                jed_assets = []
                for a in assets:
                    if not isinstance(a, dict):
                        continue
                    name = str(a.get("name") or "")
                    if name.lower().endswith(".jed"):
                        jed_assets.append(a)

                if not jed_assets:
                    return _default_payload()

                # Prefer the newest .jed by created_at when available.
                jed_assets.sort(
                    key=lambda a: str(a.get("created_at") or ""), reverse=True
                )
                best = jed_assets[0]
                return {
                    "tag_name": str(release.get("tag_name") or "N/A"),
                    "name": str(best.get("name") or "N/A"),
                    "browser_download_url": str(best.get("browser_download_url") or ""),
                    "created_at": str(best.get("created_at") or ""),
                }

            gh_ta = GitHubReleases("OpenwaterHealth", "openmotion-ta-fpga")
            gh_seed = GitHubReleases("OpenwaterHealth", "openmotion-seed-fpga")
            gh_safety = GitHubReleases("OpenwaterHealth", "openmotion-safety-fpga")

            payload = {
                "TA": _pick_latest_jed_asset(gh_ta),
                "SEED": _pick_latest_jed_asset(gh_seed),
                "SAFETY": _pick_latest_jed_asset(gh_safety),
            }

            logger.info(f"Latest TA FPGA .jed asset: {payload['TA']}")
            logger.info(f"Latest SEED FPGA .jed asset: {payload['SEED']}")
            logger.info(f"Latest SAFETY FPGA .jed asset: {payload['SAFETY']}")

            self.latestFpgaVersionInfoReceived.emit(payload)

        except Exception as e:
            logger.error(f"Error querying latest FPGA version info: {e}")
            self.latestFpgaVersionInfoReceived.emit(
                {
                    "TA": {
                        "tag_name": "N/A",
                        "name": "N/A",
                        "browser_download_url": "",
                        "created_at": "",
                    },
                    "SEED": {
                        "tag_name": "N/A",
                        "name": "N/A",
                        "browser_download_url": "",
                        "created_at": "",
                    },
                    "SAFETY": {
                        "tag_name": "N/A",
                        "name": "N/A",
                        "browser_download_url": "",
                        "created_at": "",
                    },
                }
            )

    @pyqtSlot(str, str)
    def beginFpgaFirmwareUpdate(self, target: str, tag: str) -> None:
        """Download latest .jed for target/tag and program console FPGA(s).

        target: "TA" | "SEED" | "SAFETY_EE" | "SAFETY_OPT"
        tag: release tag (e.g. "1.1.0")
        """
        target = (target or "").upper()
        tag = (tag or "").strip()
        verify = bool(getattr(self, "_fpga_fw_verify", False))
        logger.info(
            f"beginFpgaFirmwareUpdate target={target} tag={tag} verify={verify}"
        )

        if target not in _FPGA_PROGRAM_CHANNELS:
            logger.info(f"[FPGA-UPD] reject invalid target target={target}")
            self.fpgaFirmwareUpdateError.emit(
                target or "UNKNOWN", "Invalid FPGA target."
            )
            return
        if not tag or tag == "N/A":
            logger.info(f"[FPGA-UPD] reject missing tag target={target} tag={tag}")
            self.fpgaFirmwareUpdateError.emit(target, "No FPGA release tag selected.")
            return
        if not self._consoleConnected:
            logger.info(f"[FPGA-UPD] reject console disconnected target={target}")
            self.fpgaFirmwareUpdateError.emit(target, "Console is not connected.")
            return
        if self.fpgaFirmwareUpdateBusy:
            logger.info(f"[FPGA-UPD] reject busy target={target}")
            self.fpgaFirmwareUpdateError.emit(
                target, "An FPGA update is already in progress."
            )
            return

        self._set_fpga_fw_busy(True)
        self._fpga_update_thread = _ConsoleFpgaUpdateThread(
            self, target, tag, verify=verify
        )
        logger.info(
            f"[FPGA-UPD] thread created target={target} tag={tag} verify={verify}"
        )
        self._fpga_update_thread.progress.connect(
            lambda pct, msg: self.fpgaFirmwareUpdateProgress.emit(
                target, int(pct), str(msg)
            )
        )
        self._fpga_update_thread.failed.connect(
            lambda msg: self._on_fpga_fw_failed(target, str(msg))
        )
        self._fpga_update_thread.finished_ok.connect(
            lambda msg: self._on_fpga_fw_finished(target, True, str(msg))
        )
        self._fpga_update_thread.finished.connect(
            lambda: setattr(self, "_fpga_update_thread", None)
        )
        self._fpga_update_thread.start()
        logger.info(
            f"[FPGA-UPD] thread started target={target} tag={tag} verify={verify}"
        )

    def _on_fpga_fw_failed(self, target: str, message: str) -> None:
        logger.info(f"[FPGA-UPD] failed target={target} message={message}")
        self.fpgaFirmwareUpdateError.emit(target, message)
        self.fpgaFirmwareUpdateFinished.emit(target, False, message)
        self._set_fpga_fw_busy(False)

    def _on_fpga_fw_finished(self, target: str, success: bool, message: str) -> None:
        logger.info(
            f"[FPGA-UPD] finished target={target} success={success} message={message}"
        )
        self.fpgaFirmwareUpdateFinished.emit(target, bool(success), str(message))
        self._set_fpga_fw_busy(False)

    @pyqtSlot()
    def queryConsoleTemperature(self):
        """Fetch and emit Console Temperature data."""
        self._console_mutex.lock()
        try:
            temp1, temp2, temp3 = motion_interface.console_module.get_temperatures()
            logger.info(
                f"Console Temperature Data - Temp1: {temp1}, Temp2: {temp2}, Temp3: {temp3}"
            )
            self.consoleTemperatureUpdated.emit(temp1, temp2, temp3)
        except Exception as e:
            logger.error(f"Error querying Console Temperature data: {e}")
        finally:
            self._console_mutex.unlock()

    @pyqtSlot(str)
    def querySensorTemperature(self, target: str):
        """Fetch and emit Temperature data with mutex protection and event-based UI updates."""
        try:
            if target == "SENSOR_LEFT" or target == "SENSOR_RIGHT":
                sensor_tag = "left" if target == "SENSOR_LEFT" else "right"
                mutex = self._get_sensor_mutex(target)

                mutex.lock()
                try:
                    imu_temp = motion_interface.sensors[
                        sensor_tag
                    ].imu_get_temperature()
                    logger.info(f"Temperature Data - IMU Temp: {imu_temp}")
                    # Emit signal for async UI update
                    self.temperatureSensorUpdated.emit(imu_temp)
                finally:
                    mutex.unlock()
            else:
                logger.error(f"Invalid target for sensor info query: {target}")
                return
        except Exception as e:
            logger.error(f"Error querying Temperature data: {e}")

    @pyqtSlot(int)
    def setRGBState(self, state):
        """Set the RGB state using integer values."""
        self._console_mutex.lock()
        try:
            valid_states = [0, 1, 2, 3]
            if state not in valid_states:
                logger.error(f"Invalid RGB state value: {state}")
                return

            if motion_interface.console_module.set_rgb_led(state) == state:
                logger.info(f"RGB state set to: {state}")
            else:
                logger.error(f"Failed to set RGB state to: {state}")
        except Exception as e:
            logger.error(f"Error setting RGB state: {e}")
        finally:
            self._console_mutex.unlock()

    @pyqtSlot()
    def queryRGBState(self):
        """Fetch and emit RGB state."""
        self._console_mutex.lock()
        try:
            state = motion_interface.console_module.get_rgb_led()
            state_text = {0: "Off", 1: "IND1", 2: "IND2", 3: "IND3"}.get(
                state, "Unknown"
            )

            logger.info(f"RGB State: {state_text}")
            self.rgbStateReceived.emit(state, state_text)  # Emit both values
        except Exception as e:
            logger.error(f"Error querying RGB state: {e}")
        finally:
            self._console_mutex.unlock()

    @pyqtSlot()
    def queryFans(self):
        """Fetch and emit Fan Speed."""
        self._console_mutex.lock()
        try:
            fan_speed = motion_interface.console_module.get_fan_speed()

            logger.info(f"Fan Speed: {fan_speed}")
            self.fanSpeedsReceived.emit(fan_speed)  # Emit both values
        except Exception as e:
            logger.error(f"Error querying Fan Speeds: {e}")
        finally:
            self._console_mutex.unlock()

    @pyqtSlot()
    def queryFpgaVersions(self):
        """Read 4-byte version registers from each FPGA and emit fpgaVersionsReceived.

        Byte layout: [REV, MINOR, MAJOR, ID] → version string "MAJOR.MINOR.REV"
        Example: 00 01 01 02 → "1.1.0"

        FPGAs:
          TA       – mux_idx=1, channel=4, i2c_addr=0x41, reg_addr=0x14
          Seed     – mux_idx=1, channel=5, i2c_addr=0x41, reg_addr=0x13
          SafetyEE – mux_idx=1, channel=6, i2c_addr=0x41, reg_addr=0x25
          SafetyOPT– mux_idx=1, channel=7, i2c_addr=0x41, reg_addr=0x25
        """
        FPGAS = [
            ("TA", 1, 4, 0x41, 0x14),
            ("Seed", 1, 5, 0x41, 0x13),
            ("SafetyEE", 1, 6, 0x41, 0x25),
            ("SafetyOPT", 1, 7, 0x41, 0x25),
        ]
        versions = {}
        self._console_mutex.lock()
        try:
            for name, mux_idx, channel, i2c_addr, reg_addr in FPGAS:
                try:
                    data, data_len = motion_interface.console_module.read_i2c_packet(
                        mux_index=mux_idx,
                        channel=channel,
                        device_addr=i2c_addr,
                        reg_addr=reg_addr,
                        read_len=4,
                    )
                    if data is None or data_len < 4:
                        logger.error(
                            f"[FPGA] {name}: read failed (data={data}, len={data_len})"
                        )
                        versions[name] = "N/A"
                    else:
                        rev, minor, major, fpga_id = data[0], data[1], data[2], data[3]
                        ver_str = f"{major}.{minor}.{rev}"
                        logger.info(f"[FPGA] {name}: {ver_str} (id=0x{fpga_id:02X})")
                        versions[name] = ver_str
                except Exception as e:
                    logger.error(f"[FPGA] {name}: exception reading version: {e}")
                    versions[name] = "N/A"
        finally:
            self._console_mutex.unlock()
        self.fpgaVersionsReceived.emit(versions)

    @pyqtSlot(result=QVariant)
    def queryTriggerConfig(self):
        self._console_mutex.lock()
        try:
            trigger_setting = motion_interface.console_module.get_trigger_json()
            if trigger_setting:
                if isinstance(trigger_setting, str):
                    updateTrigger = json.loads(trigger_setting)
                else:
                    updateTrigger = trigger_setting
                if updateTrigger["TriggerStatus"] == 2:
                    self._trigger_state = "ON"
                    self.triggerStateChanged.emit("ON")
                    return trigger_setting or {}

            self._trigger_state = "OFF"
            self.triggerStateChanged.emit("OFF")

            return trigger_setting or {}
        except Exception as e:
            logger.error(f"Error querying trigger configuration: {e}")
        finally:
            self._console_mutex.unlock()

    @pyqtSlot(str, result=bool)
    def setTrigger(self, triggerjson):
        self._console_mutex.lock()
        try:
            json_trigger_data = json.loads(triggerjson)

            trigger_setting = motion_interface.console_module.set_trigger_json(
                data=json_trigger_data
            )
            if trigger_setting:
                logger.info(f"Trigger Setting: {trigger_setting}")
                return True
            else:
                logger.error("Failed to set trigger setting.")
                return False

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON data: {e}")
            return False

        except AttributeError as e:
            logger.error(f"Invalid interface or method: {e}")
            return False

        except Exception as e:
            logger.error(f"Unexpected error while setting trigger: {e}")
            return False
        finally:
            self._console_mutex.unlock()

    @pyqtSlot(result=bool)
    @pyqtSlot(str, result=bool)
    def startTrigger(self, triggerjson=None):
        self._console_mutex.lock()
        try:
            if triggerjson:
                json_trigger_data = json.loads(triggerjson)

                trigger_setting = motion_interface.console_module.set_trigger_json(
                    data=json_trigger_data
                )
                if not trigger_setting:
                    logger.error("Error while setting trigger trigger not started")
                    return False

                logger.info(f"Trigger Setting: {trigger_setting}")

            success = motion_interface.console_module.start_trigger()
            if success:
                # Start the per-run log now
                self._start_runlog()
                logger.info("TRIGGER STARTED")

                # Start status thread
                if self._console_status_thread is None:
                    self._console_status_thread = ConsoleStatusThread(self)
                    self._console_status_thread.statusUpdate.connect(
                        self.handleUpdateCapStatus
                    )  # Or define a dedicated signal
                    self._console_status_thread.start()

                self._trigger_state = "ON"
                self.triggerStateChanged.emit("ON")
            return success

        except Exception as e:
            logger.error(f"Unexpected error while setting trigger: {e}")
            return False
        finally:
            self._console_mutex.unlock()

    @pyqtSlot()
    def stopTrigger(self):
        try:
            # (1) Figure out if we're being called from inside the status thread
            current_thread = QThread.currentThread()
            called_from_status_thread = (
                self._console_status_thread is not None
                and current_thread is self._console_status_thread
            )

            # (2) Stop the polling thread
            if self._console_status_thread:
                # If we're in the SAME thread, don't self.join().
                if called_from_status_thread:
                    # Just tell the thread loop to exit after this iteration
                    self._console_status_thread._running = False
                    self._console_status_thread._wait_condition.wakeAll()
                    # Do NOT .wait() here
                else:
                    # Safe to fully stop/join from another thread (e.g. UI button)
                    self._console_status_thread.stop()
                    self._console_status_thread = None

            # (3) Close out the run log
            self._stop_runlog()

            # (4) Tell console to stop firing
            self._console_mutex.lock()
            try:
                motion_interface.console_module.stop_trigger()
            finally:
                self._console_mutex.unlock()

            # (5) Update state
            self._trigger_state = "OFF"
            self.triggerStateChanged.emit("OFF")

            return True

        except Exception as e:
            logger.error(f"Unexpected error while stopping trigger: {e}")
            return False

    @pyqtSlot(str)
    def querySensorAccelerometer(self, target: str):
        """Fetch and emit Accelerometer data with mutex protection and event-based UI updates."""
        try:
            if target == "SENSOR_LEFT" or target == "SENSOR_RIGHT":
                sensor_tag = "left" if target == "SENSOR_LEFT" else "right"
                mutex = self._get_sensor_mutex(target)

                mutex.lock()
                try:
                    accel = motion_interface.sensors[sensor_tag].imu_get_accelerometer()
                    logger.info(
                        f"Accel (raw): X={accel[0]}, Y={accel[1]}, Z={accel[2]}"
                    )
                    # Emit signal for async UI update
                    self.accelerometerSensorUpdated.emit(accel[0], accel[1], accel[2])
                finally:
                    mutex.unlock()
            else:
                logger.error(f"Invalid target for sensor info query: {target}")
                return
        except Exception as e:
            logger.error(f"Error querying Accelerometer data: {e}")

    @pyqtSlot()
    def querySensorGyroscope(self):
        """Fetch and emit Gyroscope data."""
        try:
            gyro = motion_interface.sensors["left"].imu_get_gyroscope()
            logger.info(f"Gyro  (raw): X={gyro[0]}, Y={gyro[1]}, Z={gyro[2]}")
            self.gyroscopeSensorUpdated.emit(gyro[0], gyro[1], gyro[2])
        except Exception as e:
            logger.error(f"Error querying Gyroscope data: {e}")

    @pyqtSlot(str, int)
    def configureCamera(self, target: str, cam_mask: int):
        """Configure camera with mutex protection and event-based UI updates."""
        try:
            if target == "SENSOR_LEFT" or target == "SENSOR_RIGHT":
                sensor_tag = "left" if target == "SENSOR_LEFT" else "right"
                mutex = self._get_sensor_mutex(target)

                mutex.lock()
                try:
                    passed_flash = motion_interface.sensors[sensor_tag].program_fpga(
                        camera_position=cam_mask, manual_process=False
                    )
                    passed_configure = motion_interface.sensors[
                        sensor_tag
                    ].camera_configure_registers(camera_position=cam_mask)

                    if not passed_flash or not passed_configure:
                        logger.error(
                            f"Failed to configure camera {sensor_tag} with mask {cam_mask}"
                        )
                        self.cameraConfigUpdated.emit(cam_mask, False)
                        return

                    gain = 16
                    exposure = 600
                    print(f"Switching camera to {cam_mask}")
                    cam_position = cam_mask.bit_length() - 1
                    passed_sw = motion_interface.sensors[sensor_tag].switch_camera(
                        cam_position
                    )
                    print(f"Setting gain to {gain}")
                    passed_gain = motion_interface.sensors[sensor_tag].camera_set_gain(
                        gain
                    )
                    print(f"Setting exposure to {exposure}")
                    passed_exposure = motion_interface.sensors[
                        sensor_tag
                    ].camera_set_exposure(0, us=exposure)
                    print(
                        f"Camera {sensor_tag} with mask {cam_mask} configured with gain {gain} and exposure {exposure}"
                    )
                    passed = (
                        passed_flash
                        and passed_configure
                        and passed_sw
                        and passed_gain
                        and passed_exposure
                    )
                    self.cameraConfigUpdated.emit(cam_mask, passed)
                finally:
                    mutex.unlock()
            else:
                logger.error(f"Invalid target for camera configuration: {target}")
                return
        except Exception as e:
            logger.error(f"Error configuring Camera {cam_mask}: {e}")
            self.cameraConfigUpdated.emit(cam_mask, False)

    @pyqtSlot(str)
    def configureAllCameras(self, target: str):
        for i in range(8):
            bitmask = 1 << i  # 0x01, 0x02, 0x04, ..., 0x80
            self.configureCamera(target, bitmask)

    @pyqtSlot(str, result=bool)
    def sendPingCommand(self, target: str):
        """Send a ping command to HV device."""
        try:
            if target == "CONSOLE":
                self._console_mutex.lock()
                if motion_interface.console_module.ping():
                    logger.info("Ping command sent successfully")
                    return True
                else:
                    logger.error("Failed to send ping command")
                    return False
            elif target == "SENSOR_LEFT" or target == "SENSOR_RIGHT":
                sensor_tag = "left" if target == "SENSOR_LEFT" else "right"
                if motion_interface.sensors[sensor_tag].ping():
                    logger.info("Ping command sent successfully")
                    return True
                else:
                    logger.error("Failed to send ping command")
                    return False
            else:
                logger.error("Invalid target for ping command")
                return False
        except Exception as e:
            logger.error(f"Error sending ping command: {e}")
            return False
        finally:
            if target == "CONSOLE":
                self._console_mutex.unlock()

    @pyqtSlot(str, result=bool)
    def sendLedToggleCommand(self, target: str):
        """Send a LED Toggle command to device with mutex protection."""
        try:
            if target == "CONSOLE":
                self._console_mutex.lock()
                try:
                    if motion_interface.console_module.toggle_led():
                        logger.info("Toggle command sent successfully")
                        return True
                    else:
                        logger.error("Failed to Toggle command")
                        return False
                finally:
                    self._console_mutex.unlock()
            elif target == "SENSOR_LEFT" or target == "SENSOR_RIGHT":
                sensor_tag = "left" if target == "SENSOR_LEFT" else "right"
                mutex = self._get_sensor_mutex(target)

                mutex.lock()
                try:
                    if motion_interface.sensors[sensor_tag].toggle_led():
                        logger.info("Toggle command sent successfully")
                        return True
                    else:
                        logger.error("Failed to send Toggle command")
                        return False
                finally:
                    mutex.unlock()
            else:
                logger.error("Invalid target for Toggle command")
                return False
        except Exception as e:
            logger.error(f"Error sending Toggle command: {e}")
            return False

    @pyqtSlot(str, result=bool)
    def sendEchoCommand(self, target: str):
        """Send Echo command to device."""
        try:
            expected_data = b"Hello FROM Test Application!"
            if target == "CONSOLE":
                self._console_mutex.lock()
                echoed_data, data_len = motion_interface.console_module.echo(
                    echo_data=expected_data
                )
            elif target == "SENSOR_LEFT" or target == "SENSOR_RIGHT":
                sensor_tag = "left" if target == "SENSOR_LEFT" else "right"
                echoed_data, data_len = motion_interface.sensors[sensor_tag].echo(
                    echo_data=expected_data
                )
            else:
                logger.error("Invalid target for Echo command")
                return False

            if echoed_data == expected_data and data_len == len(expected_data):
                logger.info("Echo command successful - Data matched")
                return True
            else:
                logger.error("Echo command failed - Data mismatch")
                return False

        except Exception as e:
            logger.error(f"Error sending Echo command: {e}")
            return False
        finally:
            if target == "CONSOLE":
                self._console_mutex.unlock()

    @pyqtSlot(result=int)
    def getFsyncCount(self):
        """Get the Fsync count from the console."""
        self._console_mutex.lock()
        try:
            fsync_count = motion_interface.console_module.get_fsync_pulsecount()
            logger.info(f"Fsync Count: {fsync_count}")
            return fsync_count
        except Exception as e:
            logger.error(f"Error getting Fsync count: {e}")
            return -1
        finally:
            self._console_mutex.unlock()

    @pyqtSlot(result=int)
    def getLsyncCount(self):
        """Get the Fsync count from the console."""
        self._console_mutex.lock()
        try:
            lsync_count = motion_interface.console_module.get_lsync_pulsecount()
            logger.debug(f"Lsync Count: {lsync_count}")
            return lsync_count
        except Exception as e:
            logger.error(f"Error getting Lsync count: {e}")
            return -1
        finally:
            self._console_mutex.unlock()

    @pyqtSlot(str, int, int, int, int, int, result=QVariant)
    def i2cReadBytes(
        self,
        target: str,
        mux_idx: int,
        channel: int,
        i2c_addr: int,
        offset: int,
        data_len: int,
    ):
        """Send i2c read to device"""
        try:
            logger.debug(
                f"I2C Read Request -> target={target}, mux_idx={mux_idx}, channel={channel}, "
                f"i2c_addr=0x{int(i2c_addr):02X}, offset=0x{int(offset):02X}, read_len={int(data_len)}"
            )

            if target == "CONSOLE":
                self._console_mutex.lock()
                fpga_data, fpga_data_len = (
                    motion_interface.console_module.read_i2c_packet(
                        mux_index=mux_idx,
                        channel=channel,
                        device_addr=i2c_addr,
                        reg_addr=offset,
                        read_len=data_len,
                    )
                )
                if fpga_data is None or fpga_data_len == 0:
                    logger.error("Read I2C Failed")
                    return []
                else:
                    logger.debug("Read I2C Success")
                    logger.debug(
                        f"Raw bytes: {fpga_data.hex(' ')}"
                    )  # Print as hex bytes separated by spaces
                    return list(fpga_data[:fpga_data_len])

            elif target == "SENSOR_LEFT" or target == "SENSOR_RIGHT":
                logger.error("I2C Read Not Implemented")
                return []
        except Exception as e:
            logger.error(f"Error sending i2c read command: {e}")
            return []
        finally:
            if target == "CONSOLE":
                self._console_mutex.unlock()

    @pyqtSlot(str, int, int, int, int, list, result=bool)
    def i2cWriteBytes(
        self,
        target: str,
        mux_idx: int,
        channel: int,
        i2c_addr: int,
        offset: int,
        data: list[int],
    ) -> bool:
        """Send i2c write to device"""
        QMutexLocker(self._i2c_mutex)  # Lock auto-released at function exit
        try:
            logger.debug(
                f"I2C Write Request -> target={target}, mux_idx={mux_idx}, channel={channel}, "
                f"i2c_addr=0x{int(i2c_addr):02X}, offset=0x{int(offset):02X}, data={[f'0x{int(b):02X}' for b in data]}"
            )

            sanitized_data = []
            for b in data:
                try:
                    value = int(b) & 0xFF  # convert to int and clip to byte
                    sanitized_data.append(value)
                except Exception as e:
                    logger.error(f"Invalid byte value: {b} ({e})")
                    return False

            byte_data = bytes(sanitized_data)

            if target == "CONSOLE":
                self._console_mutex.lock()
                if motion_interface.console_module.write_i2c_packet(
                    mux_index=mux_idx,
                    channel=channel,
                    device_addr=i2c_addr,
                    reg_addr=offset,
                    data=byte_data,
                ):
                    logger.debug("Write I2C Success")
                    return True
                else:
                    logger.error("Write I2C Failed")
                    return False
            elif target == "SENSOR_LEFT" or target == "SENSOR_RIGHT":
                logger.debug("I2C Write Not Implemented")
                return True
        except Exception as e:
            logger.error(f"Error sending i2c write command: {e}")
            return False
        finally:
            if target == "CONSOLE":
                self._console_mutex.unlock()

    @pyqtSlot(str)
    def softResetSensor(self, target: str):
        """reset hardware Sensor device."""
        self._console_mutex.lock()
        try:
            if target == "CONSOLE":
                if motion_interface.console_module.soft_reset():
                    logger.info("Software Reset Sent")
                else:
                    logger.error("Failed to send Software Reset")
            elif target == "SENSOR_LEFT" or target == "SENSOR_RIGHT":
                sensor_tag = "left" if target == "SENSOR_LEFT" else "right"
                if motion_interface.sensors[sensor_tag].soft_reset():
                    logger.info("Software Reset Sent")
                else:
                    logger.error("Failed to send Software Reset")
        except Exception as e:
            logger.error(f"Error Sending Software Reset: {e}")
        finally:
            self._console_mutex.unlock()

    @pyqtSlot(int, int, result="QStringList")
    def scanI2C(self, mux: int, chan: int) -> list[str]:
        self._console_mutex.lock()
        try:
            addresses = motion_interface.console_module.scan_i2c_mux_channel(mux, chan)
            hex_addresses = [hex(addr) for addr in addresses]
            logger.info(f"Devices found on MUX {mux} channel {chan}: {hex_addresses}")
            return hex_addresses
        except Exception as e:
            logger.error(f"Error scanning I2C Bus: {e}")
        finally:
            self._console_mutex.unlock()

    @pyqtSlot(result=bool)
    def getTecEnabled(self) -> bool:
        self._console_mutex.lock()
        try:
            self._tec_dac = motion_interface.console_module.tec_voltage()
            logger.info(f"TEC DAC Setting: {self._tec_dac}")
            self.tecDacChanged.emit()
            return True
        except Exception as e:
            logger.error(f"Error setting Fan Speed: {e}")
            return False
        finally:
            self._console_mutex.unlock()

    @pyqtSlot(int, result=bool)
    def setFanLevel(self, speed: int):
        """Set Fan Level to device."""
        self._console_mutex.lock()
        try:
            if motion_interface.console_module.set_fan_speed(fan_speed=speed) == speed:
                logger.info("Fan set successfully")
                return True
            else:
                logger.error("Failed to set Fan Speed")
                return False

        except Exception as e:
            logger.error(f"Error setting Fan Speed: {e}")
            return False
        finally:
            self._console_mutex.unlock()

    @pyqtProperty(int, notify=tecTripValueChanged)
    def tecTripValue(self):
        return getattr(self, "_tec_trip_value", 0)

    def set_tec_trip_value(self, value):
        if getattr(self, "_tec_trip_value", 0) != value:
            self._tec_trip_value = value
            self.tecTripValueChanged.emit()

    @pyqtSlot()
    def queryTecTripValue(self):
        """
        Query current TEC trip value from the console module.
        Currently stubbed; prefer implementing actual SDK call.
        """
        # TODO: replace stub with actual SDK query when available
        self.set_tec_trip_value(0)

    @pyqtSlot(int, result=bool)
    def setTecTrip(self, res: int) -> bool:
        """Set TEC trip point.

        Calls the underlying SDK method `set_ta_gain_resistor` (TA resistor
        setting used for TEC trip) and returns True on success.
        """
        self._console_mutex.lock()
        try:
            # Delegate to console module
            result = motion_interface.console_module.set_ta_gain_resistor(res)
            if result:
                logger.info(f"TA gain resistor set to {res} ohms")
                return True
            else:
                msg = f"Failed to set TA gain resistor to {res} ohms"
                logger.error(msg)
                self.taGainSetFailed.emit(msg)
                return False
        except ValueError as ve:
            msg = f"Invalid TA gain value or console not connected: {ve}"
            logger.error(msg)
            self.taGainSetFailed.emit(msg)
            return False
        except Exception as e:
            msg = f"Error setting TA gain resistor: {e}"
            logger.error(msg)
            self.taGainSetFailed.emit(msg)
            return False
        finally:
            self._console_mutex.unlock()

    @pyqtSlot()
    def readUserConfig(self):
        """Read user configuration from the console device and emit userConfigLoaded.
        Runs on a background thread so the UI remains responsive."""
        import threading

        threading.Thread(target=self._do_read_user_config, daemon=True).start()

    def _do_read_user_config(self):
        self._console_mutex.lock()
        try:
            config = motion_interface.console_module.read_config()
            if config is None:
                msg = "Failed to read user configuration from device"
                logger.error(msg)
                self.userConfigError.emit(msg)
                return
            tec_trip = float(config.get("TEC_TRIP") or 0.0)
            opt_gain = float(config.get("OPT_GAIN") or 0.0)
            opt_thresh = float(config.get("OPT_THRESH") or 0.0)
            ee_gain = float(config.get("EE_GAIN") or 0.0)
            ee_thresh = float(config.get("EE_THRESH") or 0.0)
            logger.info(
                f"User config read: TEC_TRIP={tec_trip}, OPT_GAIN={opt_gain}, "
                f"OPT_THRESH={opt_thresh}, EE_GAIN={ee_gain}, EE_THRESH={ee_thresh}"
            )
            self.userConfigLoaded.emit(
                tec_trip, opt_gain, opt_thresh, ee_gain, ee_thresh
            )
        except Exception as e:
            msg = f"Error reading user configuration: {e}"
            logger.error(msg)
            self.userConfigError.emit(msg)
        finally:
            self._console_mutex.unlock()

    @pyqtSlot(float, float, float, float, float)
    def setUserConfig(
        self,
        tec_trip: float,
        opt_gain: float,
        opt_thresh: float,
        ee_gain: float,
        ee_thresh: float,
    ) -> None:
        """Write user configuration to the console device.
        Runs on a background thread so the UI remains responsive."""
        import threading

        threading.Thread(
            target=self._do_write_user_config,
            args=(tec_trip, opt_gain, opt_thresh, ee_gain, ee_thresh),
            daemon=True,
        ).start()

    def _do_write_user_config(self, tec_trip, opt_gain, opt_thresh, ee_gain, ee_thresh):
        self._console_mutex.lock()
        try:
            config = motion_interface.console_module.read_config()
            if config is None:
                msg = "Failed to read user configuration before writing"
                logger.error(msg)
                self.userConfigError.emit(msg)
                return
            config.set("TEC_TRIP", tec_trip)
            config.set("OPT_GAIN", opt_gain)
            config.set("OPT_THRESH", opt_thresh)
            config.set("EE_GAIN", ee_gain)
            config.set("EE_THRESH", ee_thresh)
            updated = motion_interface.console_module.write_config(config)
            if updated is None:
                msg = "Failed to write user configuration to device"
                logger.error(msg)
                self.userConfigError.emit(msg)
                return
            logger.info(
                f"User config written: seq={updated.header.seq}, crc=0x{updated.header.crc:04X}"
            )
        except Exception as e:
            msg = f"Error writing user configuration: {e}"
            logger.error(msg)
            self.userConfigError.emit(msg)
        finally:
            self._console_mutex.unlock()

    @pyqtSlot("QVariantList")
    def saveHistogramToCSV(self, data):
        try:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.expanduser(f"~/histogram_{timestamp}.csv")
            with open(path, "w", newline="") as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(["Bin", "Value"])
                for i, value in enumerate(data):
                    writer.writerow([i, value])
            logger.info(f"Histogram saved to {path}")
        except Exception as e:
            logger.error(f"Failed to save histogram: {e}")

    @pyqtSlot(list)
    def on_new_histogram(self, bins):
        if bins:
            self.histogramReady.emit(bins)
        else:
            logger.error("Capture thread failed to retrieve histogram.")
            self.histogramReady.emit([])  # Emit empty to clear

    @pyqtSlot(str)
    def handleUpdateCapStatus(self, status: str):
        """Update the capture status."""
        logger.info(f"Capture Status: {status}")
        self.updateCapStatus.emit(status)

    @pyqtSlot(int)
    def startCameraStream(self, camera_index: int):
        logger.info(f"Starting camera stream for camera {camera_index + 1}")
        logger.error("Camera streaming is not implemented yet.")
        # if self._capture_thread is None or not self._capture_thread.isRunning():
        #     self._capture_thread = CaptureThread(camera_index)
        #     self._capture_thread.new_histogram.connect(self.on_new_histogram)
        #     self._capture_thread.update_status.connect(self.handleUpdateCapStatus)
        #     self._capture_thread.start()
        #     self._is_streaming = True
        #     self.isStreamingChanged.emit()

    @pyqtSlot(int)
    def stopCameraStream(self, cam_num):
        logger.error("Camera streaming is not implemented yet.")
        # if self._is_streaming and self._capture_thread:
        #     logger.info(f"Stopping camera stream for cam {cam_num}")
        #     self._capture_thread.stop()
        #     self._capture_thread = None
        #     self._is_streaming = False
        #     self.isStreamingChanged.emit()

    @pyqtSlot(str, int, int)
    def getCameraHistogram(
        self, target: str, camera_index: int, test_pattern_id: int = 4
    ):
        logger.info(f"Getting histogram for camera {camera_index + 1}")
        bins, histo = motion_interface.get_camera_histogram(
            sensor_side=target,
            camera_id=camera_index,
            test_pattern_id=test_pattern_id,
            auto_upload=True,
        )

        if bins:
            self.histogramReady.emit(bins)
        else:
            logger.error("Failed to retrieve histogram.")
            self.histogramReady.emit([])  # Emit empty to clear

    @pyqtSlot()
    def readSafetyStatus(self):
        # Replace this with your actual console status check
        self._console_mutex.lock()
        try:
            muxIdx = 1
            i2cAddr = 0x41
            offset = 0x24
            data_len = 1  # Number of bytes to read

            channels = {"SE": 6, "SO": 7}
            statuses = {}

            for label, channel in channels.items():
                status = self.i2cReadBytes(
                    "CONSOLE", muxIdx, channel, i2cAddr, offset, data_len
                )
                if status:
                    statuses[label] = status[0]
                else:
                    raise Exception("I2C read error")

            status_text = f"SE: 0x{statuses['SE']:02X}, SO: 0x{statuses['SO']:02X}"

            if (statuses["SE"] & 0x0F) == 0 and (statuses["SO"] & 0x0F) == 0:
                if self._safetyFailure:
                    self._safetyFailure = False
                    self.safetyFailureStateChanged.emit(False)
            else:
                if not self._safetyFailure:
                    self._safetyFailure = True
                    self.stopTrigger()
                    self.laserStateChanged.emit(False)
                    self.safetyFailureStateChanged.emit(True)
                    logging.error(f"Failure Detected: {status_text}")

            # Emit combined status if needed

            logging.info(f"Status QUERY: {status_text}")

        except Exception as e:
            logging.error(f"Console status query failed: {e}")
        finally:
            self._console_mutex.unlock()

    @pyqtSlot(str)
    def queryCameraPowerStatus(self, target: str):
        """Query camera power status for all cameras on the specified sensor with mutex protection."""
        try:
            if target == "SENSOR_LEFT" or target == "SENSOR_RIGHT":
                sensor_tag = "left" if target == "SENSOR_LEFT" else "right"
                mutex = self._get_sensor_mutex(target)

                mutex.lock()
                try:
                    logger.info(f"Querying camera power status for {sensor_tag} sensor")

                    # Query power status for all cameras
                    sensor = motion_interface.sensors[sensor_tag]
                    power_status = sensor.get_camera_power_status()

                    if power_status is not None:
                        # Convert to list of booleans for QML
                        power_status_list = list(power_status)
                        logger.info(f"Camera power status: {power_status_list}")
                        logger.info(
                            f"Power status list type: {type(power_status_list)}, length: {len(power_status_list)}"
                        )

                        # Emit signal to update UI
                        self.cameraPowerStatusUpdated.emit(power_status_list)
                    else:
                        logger.error("Failed to retrieve camera power status")
                        # Emit empty status (all False)
                        self.cameraPowerStatusUpdated.emit([False] * 8)
                finally:
                    mutex.unlock()
            else:
                logger.error(f"Invalid target for camera power status query: {target}")
                self.cameraPowerStatusUpdated.emit([False] * 8)
                return

        except Exception as e:
            logger.error(f"Error querying camera power status: {e}")
            # Emit empty status (all False) on error
            self.cameraPowerStatusUpdated.emit([False] * 8)

    @pyqtSlot(str, bool, result=bool)
    def setFanControl(self, target: str, fan_on: bool):
        """Set fan control state on the specified sensor with mutex protection."""
        try:
            if target == "SENSOR_LEFT" or target == "SENSOR_RIGHT":
                sensor_tag = "left" if target == "SENSOR_LEFT" else "right"
                mutex = self._get_sensor_mutex(target)

                mutex.lock()
                try:
                    logger.info(
                        f"Setting fan control to {'ON' if fan_on else 'OFF'} on {sensor_tag} sensor"
                    )

                    # Set fan control state
                    sensor = motion_interface.sensors[sensor_tag]
                    result = sensor.set_fan_control(fan_on)

                    if result:
                        logger.info(
                            f"Fan control set to {'ON' if fan_on else 'OFF'} successfully"
                        )
                    else:
                        logger.error(
                            f"Failed to set fan control to {'ON' if fan_on else 'OFF'}"
                        )

                    return result
                finally:
                    mutex.unlock()
            else:
                logger.error(f"Invalid target for fan control: {target}")
                return False

        except Exception as e:
            logger.error(f"Error setting fan control: {e}")
            return False

    @pyqtSlot(str, result=bool)
    def getFanControlStatus(self, target: str):
        """Get fan control status from the specified sensor with mutex protection."""
        try:
            if target == "SENSOR_LEFT" or target == "SENSOR_RIGHT":
                sensor_tag = "left" if target == "SENSOR_LEFT" else "right"
                mutex = self._get_sensor_mutex(target)

                mutex.lock()
                try:
                    # Get fan control status
                    sensor = motion_interface.sensors[sensor_tag]
                    status = sensor.get_fan_control_status()

                    return status
                finally:
                    mutex.unlock()
            else:
                logger.error(f"Invalid target for fan control status: {target}")
                return False

        except Exception as e:
            logger.error(f"Error getting fan control status: {e}")
            return False

    @pyqtSlot(result=bool)  # GET: no parameter - float
    @pyqtSlot(float, result=bool)  # SET: float parameter - bool
    def tec_voltage(self, value=None):
        self._console_mutex.lock()
        try:
            if value is None:
                # GET operation
                self._tec_dac = motion_interface.console_module.tec_voltage()
                logger.debug(f"TEC DAC Setting: {self._tec_dac}")
                run_logger.info(
                    "TEC Setpoint Voltage - volt: %.6f ", float(self._tec_dac)
                )

            else:
                # SET operation
                motion_interface.console_module.tec_voltage(value)
                logger.debug(f"TEC voltage set to: {value}")
                self._tec_dac = value
                run_logger.info(
                    "TEC Setpoint Voltage - volt: %.6f ", float(self._tec_dac)
                )

            self.tecDacChanged.emit()
            return True
        except Exception as e:
            logger.error(f"Error in TEC voltage operation: {e}")
            return False
        finally:
            self._console_mutex.unlock()

    @pyqtSlot(result=QVariant)
    def tec_status(self):
        """
        Returns a dict suitable for QML:
        On error: { ok: False, error: "..." }
        """

        self._console_mutex.lock()
        try:
            v, i, p, t, ok = motion_interface.console_module.tec_status()

            R_TH = (
                1 / ((float(v) / (V_REF / 2 * R_3)) - 1 / R_3 + 1 / R_1) - R_2
            )  # v = OUT1, VOUT1 from ADC
            Thermistor_Temp = np.interp(
                R_TH, self._data_RT[:, 1][::-1], self._data_RT[:, 0][::-1]
            )

            R_SET = (
                1 / ((float(i) / (V_REF / 2 * R_3)) - 1 / R_3 + 1 / R_1) - R_2
            )  # i = IN2P, TEMPSET from ADC
            SET_Temp = np.interp(
                R_SET, self._data_RT[:, 1][::-1], self._data_RT[:, 0][::-1]
            )

            self._tec_voltage = round(
                float(Thermistor_Temp), 2
            )  # Measured thermistor temperature
            self._tec_temp = round(float(SET_Temp), 2)  # Measured target setpiont
            self._tec_monC = round(
                (float(p) - 0.5 * V_REF) / (25 * R_s), 3
            )  # p = V_itec
            self._tec_monV = round((float(t) - 0.5 * V_REF) * 4, 3)  # t = V_vtec
            self._tec_good = bool(ok)  # TMPGD pin (abs(OUT1-IN2P) < 100mV)

            # Long-run health sample -> goes ONLY to run.log

            run_logger.info(
                "TEC Status -  temp: %.2f set: %.2f tec_c: %.3f tec_v: %.3f good: %s",
                self._tec_voltage,
                self._tec_temp,
                float(p),
                float(t),
                bool(ok),
            )

            self.tecStatusChanged.emit()

            return ok

        except Exception as e:
            logger.error(f"Error in TEC status operation: {e}")
            return False
        finally:
            self._console_mutex.unlock()

    @pyqtSlot(result=QVariant)
    def pdu_mon(self):
        """
        Returns a dict (QVariant) for QML:
        On success:
          {
            "ok": True,
            "adc0": {"raws": [...8...], "vals": [...8...]},
            "adc1": {"raws": [...8...], "vals": [...8...]},
          }
        On error:
          { "ok": False, "error": "..." }
        """
        self._console_mutex.lock()
        try:
            pdu = motion_interface.console_module.read_pdu_mon()
            if pdu is None:
                logger.error("PDU MON: no data")
                return {"ok": False, "error": "no data"}

            temp1, temp2, temp3 = motion_interface.console_module.get_temperatures()

            # Cache for QML bindings
            self._pdu_raws = list(pdu.raws)
            self._pdu_vals = list(pdu.volts)

            # Emit change for any bound properties
            self.pduMonChanged.emit()

            adc1_scaled = [
                (v / SCALE_V)
                if i == 6
                else (v / SCALE_I)  # i is ADC1 channel index 0..7
                for i, v in enumerate(self._pdu_vals[8:])
            ]

            # Run-log (concise)
            run_logger.info(
                "PDU MON ADC0 vals: %s",
                " ".join(f"{(v / SCALE_V):.3f}" for v in self._pdu_vals[:8]),
            )

            run_logger.info(
                "PDU MON ADC1 vals: %s", " ".join(f"{i:.3f}" for i in adc1_scaled)
            )

            run_logger.info(
                "TEMP MON: MCU: %.2f SAFETY: %.2f TA: %.2f", temp1, temp2, temp3
            )

            # Return QML-friendly dict
            return {
                "ok": True,
                "adc0": {
                    "raws": self._pdu_raws[:8],
                    "vals": self._pdu_vals[:8],
                },
                "adc1": {
                    "raws": self._pdu_raws[8:],
                    "vals": self._pdu_vals[8:],
                },
            }

        except Exception as e:
            logger.error("Error in PDU MON operation: %s", e)
            return {"ok": False, "error": str(e)}
        finally:
            self._console_mutex.unlock()

    @pyqtSlot()
    def shutdown(self):
        logger.info("Shutting down MOTIONConnector...")

        if self._capture_thread:
            self._capture_thread.stop()
            self._capture_thread = None

        if self._console_status_thread:
            self._console_status_thread.stop()
            self._console_status_thread = None


class ConsoleStatusThread(QThread):
    statusUpdate = pyqtSignal(str)

    def __init__(self, connector: MOTIONConnector, parent=None):
        super().__init__(parent)
        self.connector = connector
        self._running = True
        self._mutex = QMutex()
        self._wait_condition = QWaitCondition()
        self.last_run = time.time()

    def run(self):
        while self._running:
            now = time.time()

            # run the heavy work ~1 Hz
            if now - self.last_run >= 1.0:
                try:
                    #
                    # 1. TEC status poll
                    #
                    # This updates _tec_* fields inside connector and emits tecStatusChanged
                    ok_tec = self.connector.tec_status()

                    #
                    # 2. PDU Mon poll
                    #
                    self.connector.pdu_mon()

                    #
                    # 3. Safety / interlock state
                    #
                    muxIdx = 1
                    i2cAddr = 0x41
                    offset = 0x24
                    data_len = 1

                    channels = {"SE": 6, "SO": 7}
                    statuses = {}

                    for label, channel in channels.items():
                        status = self.connector.i2cReadBytes(
                            "CONSOLE", muxIdx, channel, i2cAddr, offset, data_len
                        )
                        if status:
                            statuses[label] = status[0]
                        else:
                            self.statusUpdate.emit(f"{label} Disconnected")
                            raise Exception("I2C read error")

                    status_text = (
                        f"SE: 0x{statuses['SE']:02X}, SO: 0x{statuses['SO']:02X}"
                    )
                    run_logger.info(
                        f"Safety Status - SE: 0x{statuses['SE']:02X}, SO: 0x{statuses['SO']:02X}"
                    )

                    ok_se = (statuses["SE"] & 0x0F) == 0
                    ok_so = (statuses["SO"] & 0x0F) == 0
                    
                    if ok_se and ok_so and ok_tec:
                        if self.connector._safetyFailure:
                            self.connector._safetyFailure = False
                            self.connector.safetyFailureStateChanged.emit(False)
                    else:
                        if not self.connector._safetyFailure:
                            # First time we see a failure
                            self.connector._safetyFailure = True
                            # Request trigger stop (safe version won't deadlock)
                            self.connector.stopTrigger()
                            self.connector.laserStateChanged.emit(False)
                            self.connector.safetyFailureStateChanged.emit(True)
                            logging.error(f"Failure Detected: {status_text}")

                    #
                    # 3. Analog telemetry (tcm/tcl/pdc)
                    #
                    tcm_raw = self.connector.getLsyncCount()
                    tcl_raw = self.connector.i2cReadBytes(
                        "CONSOLE", muxIdx, 4, i2cAddr, 0x10, 4
                    )
                    pdc_raw = self.connector.i2cReadBytes(
                        "CONSOLE", muxIdx, 7, i2cAddr, 0x1C, 2
                    )

                    # Represent raw byte arrays as hex for easier reading
                    try:
                        if isinstance(tcl_raw, (bytes, bytearray, list)):
                            tcl_hex = " ".join(f"0x{int(b):02X}" for b in tcl_raw)
                        else:
                            tcl_hex = str(tcl_raw)

                        if isinstance(pdc_raw, (bytes, bytearray, list)):
                            pdc_hex = " ".join(f"0x{int(b):02X}" for b in pdc_raw)
                        else:
                            pdc_hex = str(pdc_raw)
                    except Exception:
                        tcl_hex = str(tcl_raw)
                        pdc_hex = str(pdc_raw)

                    logging.debug(
                        f"tcm_raw: {tcm_raw} tcl_raw: [{tcl_hex}] pdc_raw: [{pdc_hex}]"
                    )

                    if tcl_raw and pdc_raw:
                        tcm = int(tcm_raw)
                        tcl = int.from_bytes(tcl_raw, byteorder="little")

                        # Attempt to read the ADC DATA scale from models/FpgaModel.js
                        scale = self.connector._get_fpga_scale("Safety OPT", "ADC DATA")

                        pdc = int.from_bytes(pdc_raw, byteorder="little") * float(
                            scale
                        )  # mA

                        if (
                            tcl != self.connector._tcl
                            or tcm != self.connector._tcm
                            or pdc != self.connector._pdc
                        ):
                            self.connector._tcl = tcl
                            self.connector._tcm = tcm
                            self.connector._pdc = pdc

                            logging.debug(
                                f"Analog Values - TCM: {tcm}, TCL: {tcl}, PDC: {pdc:.3f} mA"
                            )

                            run_logger.info(
                                f"Analog Values - TCM: {tcm}, TCL: {tcl}, PDC: {pdc:.3f}"
                            )

                            self.connector.tclChanged.emit()
                            self.connector.tcmChanged.emit()
                            self.connector.pdcChanged.emit()

                except Exception as e:
                    logging.error(f"Console status query failed: {e}")

                # mark we ran this 1Hz tick
                self.last_run = now

            # sleep-ish for up to 100ms, or until stop() wakes us
            self._mutex.lock()
            self._wait_condition.wait(self._mutex, 100)
            self._mutex.unlock()

    def stop(self):
        # Called from *another* thread in normal shutdown
        self._running = False
        self._wait_condition.wakeAll()
        self.quit()
        self.wait()
