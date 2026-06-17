# TEC Trip Temperature Configurator — Design

**Date:** 2026-06-17
**Repo:** openmotion-test-app
**Status:** Approved

## Goal

Give the user a friendly control to read and change the console's **TEC trip
temperature** (the over-temperature threshold at which the TEC comparator trips).
The control lives in the **TEC CTRL** tab of the Demo screen.

## Background

The console firmware stores the trip temperature as a `TEC_TRIP` key (a float
°C, e.g. `40.0`) inside its **user-config JSON**. On boot/config-apply the
firmware parses `TEC_TRIP`, converts temperature → thermistor resistance →
comparator trip voltage (`apply_tec_trip_setting` in
`openmotion-console-fw/Core/Src/main.c`).

The SDK already exposes the config: `console.read_config()` /
`console.write_config(cfg)`, where the config object supports `.get(key)`,
`.set(key, value)`, and `.json_data`.

### Existing (broken) state — not used by this design

- `pages/Console.qml` has a `TEC_TRIP` numeric field, but its container is
  `visible: false` and it is anchored on top of the Odometer box (it overlaps
  the Uptime/Laser-pulses readout if un-hidden). Its `onAccepted` calls
  `setTecTrip()`, which delegates to `set_ta_gain_resistor()` — i.e. it sets the
  **TA gain resistor**, not the trip temperature. `queryTecTripValue()` is a stub
  returning 0. **This block is left hidden and untouched; flagged as dead code to
  remove later.**

## Design

### Backend — `motion_connector.py`

1. **`queryTecTripValue()`** — replace the stub. On a daemon worker thread
   (mirrors `_do_read_user_config`), under `_console_mutex`:
   - `config = motion_interface.console.read_config()`
   - `set_tec_trip_value(int(round(float(config.get("TEC_TRIP") or 0))))`
   - On `None`/exception: log and leave the current value unchanged (no crash).

2. **`setTecTrip(res: int) -> bool`** — rewrite as a **read-modify-write** of the
   user config, keeping the synchronous `@pyqtSlot(int, result=bool)` signature
   QML depends on. Under `_console_mutex`:
   - `config = console.read_config()` → if `None`, emit `tecTripSetFailed`, return `False`
   - `config.set("TEC_TRIP", res)`
   - `updated = console.write_config(config)` → if `None`, emit `tecTripSetFailed`, return `False`
   - On success: `set_tec_trip_value(res)`, return `True`

   Read-modify-write preserves all sibling keys (`OPT_GAIN`, `OPT_THRESH`,
   `EE_GAIN`, `EE_THRESH`). We deliberately do **not** route through
   `setUserConfig`/`_do_write_user_config`, which drops OPT/EE keys when their
   thresholds are 0.

3. **New signal** `tecTripSetFailed = pyqtSignal(str)` — emitted on read/write
   failure, replacing the confusing reuse of `taGainSetFailed` for TEC errors.

`set_ta_gain_resistor` and the TA-gain UI are untouched.

### Frontend — `pages/Demo.qml`, `pageTec` (TEC CTRL tab)

The `pageTec` 4-column `GridLayout` currently has:
- Row 0: TEC Status cards (Setpoint °C / Current / Voltage) + Temperature indicator
- Row 1: "TEC Temperature" label + "DAC Setpoint (V)" field + "Update Setpoint" button

Add **Row 2**, mirroring the Row 1 pattern:
- Col 0: label `"TEC Trip (°C)"`
- Cols 1–3: caption `"Trip Temp (°C)"` + a `TextField`
  (`IntValidator { bottom: 0; top: 125 }`, `enabled: consoleConnected`, text
  bound to `MOTIONInterface.tecTripValue`) + a `"Set Trip"` `ActionButton`.

Behavior:
- Button / `onAccepted`: parse + clamp 0–125, call `MOTIONInterface.setTecTrip(v)`.
- Add `MOTIONInterface.queryTecTripValue()` to `pageTec`'s existing
  `Component.onCompleted` (next to `tec_status()`), so opening the tab reads the
  live trip temperature.
- Add `Connections` handlers: `onTecTripValueChanged` (refresh the field text)
  and `onTecTripSetFailed(msg)` (surface the error, e.g. inline red text with a
  brief auto-clear timer).

## Value semantics

- Integer °C, range 0–125 (matches existing validator intent).
- Stored to config as the integer the user typed; firmware parses with `strtod`,
  so `40` and `40.0` are equivalent.

## Testing / verification

- **Primary (manual):** launch the test app with the console attached, open Demo
  → TEC CTRL, confirm the field renders cleanly (no overlap), pre-populates with
  the device's current `TEC_TRIP`, accepts a 0–125 value, and the firmware debug
  log shows `TEC_TRIP found: <n>` after writing. Confirm OPT/EE keys are
  preserved by re-reading the config.
- **Optional (unit):** a `tests/` test mocking `console.read_config` /
  `write_config` to assert `setTecTrip` does read-modify-write and preserves
  sibling keys, and `queryTecTripValue` parses `TEC_TRIP`. No existing unit-test
  seam, so this would be the first of its kind.

## Out of scope

- The hidden `Console.qml` `TEC_TRIP` block (left as dead code; remove later).
- Float-precision trip values, per-channel trip, or any firmware changes.
