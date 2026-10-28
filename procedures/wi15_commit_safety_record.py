"""Commit a passed WI-00015 Safety Calibration to the unit's safety FPGAs.

Safety Calibration writes the per-unit EE/OPT limits into the console's User
Configuration. A console that owns its laser-safety state takes them from
there only as Factory-image bench overrides: a Release image drops them and
loads the limits from each safety FPGA's UFM record instead. This procedure
writes that record, so the calibration survives the Release image:

1. Connect to the console; it must run a Factory image (boot-info v2, build
   bit 0), the only image that can write the UFM.
2. Find the latest PASSED Safety Calibration evidence for this console's
   serial in ``--output-dir`` and read the console's stored configuration.
   The EE/OPT ``DRIVE_CL``, ``PULSE_WIDTH_UL`` and ``RATE_LL`` values must be
   identical in both; anything else stops the run before a write.
3. Build both records (refused outside the safety envelope) and show them;
   the operator confirms.
4. Per device (EE, then OPT): open the configuration interface, erase the
   UFM only, write pages 0-1, read back and compare, check the feature row
   is unchanged, refresh.
5. The operator power-cycles the console (observed, not confirmed); the boot
   attestation must PASS with a valid record on EE and OPT.

Evidence: ``<serial>-safety-record-<run-id>/<serial>-safety-record-run.json``
under ``--output-dir``, checkpointed after every step. Exit 0 on PASS, 1
otherwise. No laser emission: the trigger is never started.

Runs from the test app's Procedures pane; planned to move into the SDK
(``omotion.scripts``) for 1.16.0.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

from procedures.safety_record import (
    DEVICES,
    SafetyRecordError,
    build_record,
    configuration_mismatches,
    decode_record,
    find_passed_safety_calibration,
    limits_from_configuration,
)

PROCEDURE_NAME = "Commit Safety Calibration to Unit"
PROCEDURE_SLUG = "safety-record"

ERASE_UFM = 0x08                 # fpga_prog_erase mode: UFM only
POWER_CYCLE_MIN_OFF_S = 2.0
ATTESTATION_TIMEOUT_S = 30.0     # a run takes about 6.5 s after boot
CONNECT_TIMEOUT_S = 20.0

EXIT_PASS = 0
EXIT_FAIL = 1


class CommitError(RuntimeError):
    """A step failed; the message is the operator-facing reason."""


# --------------------------------------------------------------- hardware
class ConsoleSafetyRecordBench:
    """The console operations this procedure needs, over one long-lived
    ``MotionInterface`` (it must stay up across the power cycle)."""

    def __init__(self, interface_factory=None, power_cycle_coordinator=None):
        if interface_factory is None:
            from omotion import MotionInterface as interface_factory
        self._iface = interface_factory()
        self._coordinator = power_cycle_coordinator
        self._iface.start(wait=False)

    @property
    def _console(self):
        return self._iface.console

    def connect(self, timeout_s: float = CONNECT_TIMEOUT_S) -> bool:
        return bool(self._iface.wait_for_ready(console=True, sensors=0, timeout=timeout_s))

    def is_connected(self) -> bool:
        return bool(self._console.is_connected())

    def console_serial(self) -> str | None:
        serial = self._console.read_serial_number()
        return str(serial).strip() if serial else None

    def firmware_version(self) -> str | None:
        try:
            return self._console.get_version()
        except Exception:
            return None

    def boot_info(self):
        return self._console.get_boot_info()

    def read_configuration(self) -> dict:
        config = self._console.read_config()
        data = getattr(config, "json_data", None)
        if not isinstance(data, dict):
            raise CommitError("the console did not return its stored configuration")
        return dict(data)

    def _channel(self, device: str):
        from omotion.config import MuxChannel
        return {"EE": MuxChannel.FPGA_SAFE_EE, "OPT": MuxChannel.FPGA_SAFE_OPT}[device]

    def _read_ufm(self, channel) -> bytes:
        c = self._console
        c.fpga_prog_ufm_reset(channel)
        return bytes(c.fpga_prog_ufm_read_page(channel)) + bytes(c.fpga_prog_ufm_read_page(channel))

    def write_record(self, device: str, record: bytes) -> dict:
        """Erase the UFM of one safety device, write ``record``, verify.

        The sequence verified on the bench 2026-10-22/26 (console fw
        docs/fpga-attestation.md section 6.4 step 3). The configuration flash
        is not touched; the feature row is compared before and after.
        """
        c = self._console
        channel = self._channel(device)
        try:
            c.telemetry.stop()   # keep the UART to the programming commands
        except Exception:
            pass
        evidence: dict[str, object] = {"device": device, "record": record.hex()}
        c.fpga_prog_open(channel)
        try:
            row_before = c.fpga_prog_featrow_read(channel)
            evidence["feature_row_before"] = [bytes(p).hex() for p in row_before]
            evidence["ufm_before"] = self._read_ufm(channel).hex()
            c.fpga_prog_erase(channel, ERASE_UFM)
            erased = self._read_ufm(channel)
            if erased not in (bytes(32), b"\xff" * 32):
                raise CommitError(f"{device}: UFM not blank after erase ({erased.hex()})")
            c.fpga_prog_ufm_reset(channel)
            c.fpga_prog_ufm_write_pages(channel, record)
            readback = self._read_ufm(channel)
            evidence["ufm_readback"] = readback.hex()
            if readback != record:
                raise CommitError(f"{device}: UFM read-back does not match the record")
            row_after = c.fpga_prog_featrow_read(channel)
            evidence["feature_row_after"] = [bytes(p).hex() for p in row_after]
            if tuple(bytes(p) for p in row_after) != tuple(bytes(p) for p in row_before):
                raise CommitError(f"{device}: feature row changed during the UFM write")
        finally:
            c.fpga_prog_refresh(channel)
        time.sleep(0.5)
        evidence["status_after_refresh"] = f"0x{c.fpga_prog_read_status(channel):08x}"
        return evidence

    def power_cycle(self, expected_serial: str):
        return self._coordinator.perform(
            minimum_off_s=POWER_CYCLE_MIN_OFF_S,
            expected_console_serial=expected_serial,
            is_console_connected=self.is_connected,
            read_console_serial=self.console_serial,
        )

    def attestation(self):
        return self._console.get_fpga_attestation()

    def close(self) -> None:
        self._iface.stop()


# --------------------------------------------------------------- evidence
def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value):
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return {k: _jsonable(getattr(value, k)) for k in value.__dataclass_fields__}
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class EvidenceFile:
    """One run's JSON evidence, rewritten atomically at every checkpoint."""

    def __init__(self, output_dir: str | Path, run_id: str):
        self.root = Path(output_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.directory = self.root / f"{PROCEDURE_SLUG}-{run_id}"
        self.directory.mkdir(exist_ok=True)
        self.path = self.directory / "run.json"
        self.data: dict[str, object] = {}

    def write(self) -> None:
        text = json.dumps(_jsonable(self.data), indent=2, sort_keys=True, ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        os.replace(tmp, self.path)

    def name_for_unit(self, serial: str, run_id: str) -> None:
        """Serial-first names, as the SDK's WI-00015 runs use."""
        target = self.root / f"{serial}-{PROCEDURE_SLUG}-{run_id}"
        if not target.exists():
            os.rename(self.directory, target)
            self.directory = target
        new_path = self.directory / f"{serial}-{PROCEDURE_SLUG}-run.json"
        old_path = self.directory / self.path.name
        if old_path.exists():
            os.replace(old_path, new_path)
        self.path = new_path


# --------------------------------------------------------------- workflow
def _attestation_device(report, name: str):
    for device in getattr(report, "devices", ()):
        if device.name == name:
            return device
    return None


def _wait_for_attestation(bench, timeout_s: float, clock, sleep):
    from omotion.fpga_attest import ATTEST_FAIL, ATTEST_PASS
    deadline = clock() + timeout_s
    while True:
        try:
            info = bench.boot_info()
        except Exception:
            info = None
        if info is not None and info.fpga_attest in (ATTEST_PASS, ATTEST_FAIL):
            return bench.attestation()
        if clock() >= deadline:
            return None
        sleep(0.5)


def run_commit(
    bench,
    *,
    output_dir: str | Path,
    operator: str,
    run_id: str,
    input_func: Callable[[str], str],
    output_func: Callable[[str], None],
    evidence: EvidenceFile | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Execute the procedure on ``bench``; returns the exit code."""
    from omotion.fpga_attest import ST_UFM_VALID

    evidence = evidence or EvidenceFile(output_dir, run_id)
    data = evidence.data
    data.update(procedure=PROCEDURE_NAME, run_id=run_id, operator=operator,
                started_at=_utc_now(), status="running", steps=[])
    written: list[str] = []

    def step(message: str) -> None:
        output_func(message)
        data["steps"].append({"at": _utc_now(), "message": message})
        evidence.write()

    try:
        step("Step 1 of 5: Checking the console ...")
        if not bench.connect():
            raise CommitError("the console is not connected")
        serial = bench.console_serial()
        if not serial:
            raise CommitError("the console has no serial number")
        data["console_serial"] = serial
        evidence.name_for_unit(serial, run_id)
        firmware = bench.firmware_version()
        data["console_firmware"] = firmware
        info = bench.boot_info()
        data["boot_info"] = info
        if info is None or not info.firmware_owns_safety_state:
            answer = ("did not answer the boot-info request" if info is None
                      else f"answers boot-info v{info.struct_version}")
            raise CommitError(
                f"the console reports firmware {firmware or 'unknown'} and {answer}, so it "
                "does not load a safety record; install the Factory image "
                "(openmotion-console-fw-<release>-factory-baremetal) of console firmware "
                "1.8.2-rc.3 or later, check that the console reports '<release>-factory', "
                "and run this procedure again")
        if not info.factory_image:
            raise CommitError(
                "the console runs a Release image, which cannot write the safety record; "
                "install the Factory image and run this procedure again")
        output_func(f"Console {serial}, Factory image.")

        step("Step 2 of 5: Checking the Safety Calibration result ...")
        source = find_passed_safety_calibration(output_dir, serial)
        if source is None:
            raise CommitError(
                f"no PASSED Safety Calibration for console {serial} in {output_dir}; "
                "run Safety Calibration first")
        data["safety_calibration"] = {"path": source.path, "sha256": source.sha256,
                                      "ended_at": source.ended_at,
                                      "configuration": source.configuration}
        stored = bench.read_configuration()
        data["console_configuration"] = stored
        mismatches = configuration_mismatches(source.configuration, stored)
        if mismatches:
            raise CommitError(
                "the console's stored safety values differ from the Safety Calibration "
                "result: " + "; ".join(mismatches))
        output_func(f"Using the Safety Calibration of {source.ended_at} "
                    f"({source.path.parent.name}).")

        step("Step 3 of 5: Building the safety records ...")
        limits = limits_from_configuration(source.configuration)
        records = {device: build_record(limits[device]) for device in DEVICES}
        data["records"] = {device: {"limits": limits[device], "hex": records[device],
                                    "decoded": decode_record(device, records[device])}
                           for device in DEVICES}
        evidence.write()
        for device in DEVICES:
            d = decode_record(device, records[device])
            output_func(
                f"  {device}: drive current limit {d['drive_cl']}, pulse width "
                f"{d['pulse_width_ll_us']}-{d['pulse_width_ul_us']} us, rate lower limit "
                f"{d['rate_ll_us']} us, PWM/CW current {d['pwm_current_ma']}/"
                f"{d['cw_current_ma']} mA")
        try:
            answer = input_func(
                f"Write these limits into the EE and OPT safety FPGAs of console {serial}? "
                "(yes/no): ")
        except (EOFError, KeyboardInterrupt):
            answer = ""
        data["operator_confirmed"] = answer.strip().lower() in ("y", "yes")
        if not data["operator_confirmed"]:
            data["status"] = "canceled"
            output_func("Canceled. Nothing was written.")
            return EXIT_FAIL

        step("Step 4 of 5: Writing the safety records ...")
        data["writes"] = []
        for device in DEVICES:
            written.append(device)
            result = bench.write_record(device, records[device])
            data["writes"].append(result)
            evidence.write()
            output_func(f"  {device}: written and verified.")

        step("Step 5 of 5: Checking the records after a console restart.")
        power_cycle = bench.power_cycle(serial)
        data["power_cycle"] = power_cycle
        evidence.write()
        if not getattr(power_cycle, "restart_proven", False):
            raise CommitError("the console power cycle was not observed")
        off_s = getattr(power_cycle, "off_duration_s", None) or 0.0
        if off_s < POWER_CYCLE_MIN_OFF_S:
            raise CommitError(
                f"the console was off for {off_s:.1f} s; keep it off for at least "
                f"{POWER_CYCLE_MIN_OFF_S:g} s so the safety FPGAs reload")
        after = getattr(power_cycle, "console_serial_after", None)
        if after != serial:
            raise CommitError(f"a different console came back ({after!r}, expected {serial})")
        report = _wait_for_attestation(bench, ATTESTATION_TIMEOUT_S, clock, sleep)
        data["attestation"] = report
        if report is None:
            raise CommitError("the boot attestation did not finish")
        problems = []
        for device in DEVICES:
            entry = _attestation_device(report, device)
            if entry is None or not entry.status & ST_UFM_VALID or entry.faulted:
                problems.append(entry.describe() if entry else f"{device}: not reported")
        if not report.passed or problems:
            raise CommitError("the boot attestation did not accept the records: "
                              + "; ".join(problems or report.faults() or [report.state_name]))
        data["status"] = "passed"
        output_func("Attestation PASS: EE and OPT safety records valid.")
        output_func("The unit can now take the Release (clinical) image.")
        return EXIT_PASS
    except (CommitError, SafetyRecordError) as exc:
        data["status"] = "failed"
        data["failure_reason"] = str(exc)
        output_func(f"Stopped: {exc}")
    except Exception as exc:
        data["status"] = "failed"
        data["failure_reason"] = f"{type(exc).__name__}: {exc}"
        output_func(f"Stopped with an error: {exc}")
    finally:
        data["ended_at"] = _utc_now()
        try:
            evidence.write()
        except OSError as exc:
            output_func(f"! evidence could not be written: {exc}")
    if written:
        output_func(
            "The safety record may be incomplete: until this procedure passes, the "
            "Release image keeps the laser interlocked on this unit. Run it again.")
    return EXIT_FAIL


# --------------------------------------------------------------- entry point
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--operator", help="operator name (asked when omitted)")
    parser.add_argument("--output-dir", default="wi15_out",
                        help="where Safety Calibration evidence lives and this run's goes")
    parser.add_argument("--fixture-id", help="accepted for the pane's common arguments; unused")
    return parser


def main(argv: Sequence[str] | None = None, *,
         input_func: Callable[[str], str] | None = None,
         output_func: Callable[[str], None] | None = None,
         bench_factory=None) -> int:
    input_func = input if input_func is None else input_func
    output_func = print if output_func is None else output_func
    args = _parser().parse_args(argv)
    try:
        from omotion.calibration.script_support import forward_library_logging
        forward_library_logging()
    except Exception:
        pass
    try:
        operator = (args.operator or "").strip() or input_func("Operator: ").strip()
    except (EOFError, KeyboardInterrupt):
        output_func("Canceled. Nothing was written.")
        return EXIT_FAIL
    if not operator:
        output_func("An operator name is required. Nothing was written.")
        return EXIT_FAIL

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if bench_factory is None:
        from omotion.scripts.wi15_safety_calibration import ManualPowerCycleCoordinator

        def bench_factory():
            return ConsoleSafetyRecordBench(
                power_cycle_coordinator=ManualPowerCycleCoordinator(
                    input_func=input_func, output_func=output_func))

    bench = None
    try:
        bench = bench_factory()
        code = run_commit(bench, output_dir=args.output_dir, operator=operator, run_id=run_id,
                          input_func=input_func, output_func=output_func)
    except Exception as exc:
        output_func(f"{PROCEDURE_NAME} stopped with an error: {exc}")
        code = EXIT_FAIL
    finally:
        if bench is not None:
            try:
                bench.close()
            except Exception:
                pass
    output_func(f"Final result: {'PASS' if code == EXIT_PASS else 'FAIL'}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
