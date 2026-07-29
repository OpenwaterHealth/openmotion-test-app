# Install bootloader from a local file (offline factory) — design

Date: 2026-07-28
Issue: [openmotion-test-app#83](https://github.com/OpenwaterHealth/openmotion-test-app/issues/83)
Branch: `feature/83-offline-bootloader-install`

## Problem

The factory floor has no internet. Converting a device to bootloader mode is the one
firmware path in the test app that still requires GitHub.

`_BootloaderInstallThread` (`motion_connector.py:566`) resolves its production image
only by downloading a GitHub release asset, and the three lock buttons in
`pages/Settings.qml` (lines 1643, 1844, 2040) refuse a local file outright:

> "Select a release to install the bootloader from; a local file cannot be used for this."

Under `--no-github` the release dropdown is empty, so there is no reachable tag and
bootloader install is impossible offline.

Every other firmware path already installs from a local file and needs no change:

| Path | Local-file entry point |
|---|---|
| Console / sensor application firmware | `beginDeviceFirmwareFromLocal` (`motion_connector.py:1606`) |
| FPGA `.jed` — TA, Seed, Safety EE, Safety OPT | `beginFpgaFirmwareFromLocal` (`motion_connector.py:2836`) |

## Scope

Add a local-file source for the **production image only**, using browse-per-flash —
the operator picks a `.bin` from disk each time, mirroring the existing
"Upload File..." pattern.

Explicitly **out of scope**, decided with the issue author:

- A local firmware *folder* that populates the release dropdown offline. Browse-per-flash
  was chosen as the workflow; a folder-backed release list is a larger change touching
  every firmware flow.
- Any modification to the app-firmware or FPGA local paths. They already work offline;
  they will be verified under `--no-github`, not changed.
- Recording the boot mode on an "already installed" abort. Reachable on console only,
  until #73 ships — see "The already-installed abort" below.

Two small in-scope additions, both on code this change already touches: a power-cycle
hint on the install-failure message, and two stale comments about when boot mode becomes
knowable. Both are described under Documentation and Error handling.

## Safety: the cross-device brick this introduces

`omotion.bootloader_install.install_bootloader` validates the image **by filename only**.
`is_production_asset()` (`omotion/firmware_update.py:189`) just checks for the substring
"production". It does **not** check console vs sensor, and it is the only content gate
before an irreversible write at `BARE_METAL_FLASH_ADDRESS`.

The GitHub path cannot hit this — it always fetches the exact `production_asset(kind)`
name. A browse dialog can: selecting `motion-sensor-production.bin` while the console is
targeted passes the SDK check and irreversibly writes the wrong image. Recovery needs SWD
or a BOOT0 strap.

This is a new failure mode created by this feature, so the app must close it. Note that
`beginDeviceFirmwareFromLocal` deliberately dropped its filename gate — that reasoning
does **not** carry over here, because it relies on the SDK validating the image against
the boot mode it detects in DFU, and the bootloader path has no equivalent kind check.

## Architecture

No new files in the app; the change is contained to the two firmware modules plus tests.

### 1. Validation helper — `motion_connector.py`

```
_validate_production_image(target: str, path: str) -> str
```

Pure function. Returns `""` when the file is acceptable, otherwise an
operator-readable error string. Rules, all case-insensitive on the basename:

1. The path exists and is a file.
2. The name contains `production` (delegated to the SDK's `is_production_asset`, so the
   two stay in sync).
3. The name contains the expected kind token — `console` for the `console` target,
   `sensor` for `left` / `right` — **and** does not contain the opposing token.

Rule 3 accepts version-stamped names such as `motion-console-production-1.8.3.bin` while
blocking the cross-device case. The "not the opposing token" half handles names that
mention both.

Exposed to QML as `@pyqtSlot(str, str, result=str) validateProductionImage(target, path)`
so a bad file is rejected **before** the irreversible-warning modal appears, and called
again inside the install thread as a backstop for any non-UI caller.

### 2. `_BootloaderInstallThread` — optional local path

The constructor gains an optional `local_path`. `run()` splits image resolution into two
branches, the same shape `_ConsoleFpgaUpdateThread` already uses for local vs
`_download_jed_from_github()` (`motion_connector.py:876`):

- **local** — validate, use the path as-is, no network
- **download** — today's behaviour unchanged

Everything downstream of resolution (the `install_bootloader` call,
`acknowledge_irreversible=True`, progress plumbing, `_note_boot_mode`) is shared.

Two latent bugs are fixed by the split:

- Line 586 requires **both** `install_bootloader` and `GitHubReleases` to be importable.
  A local install needs only the former. The `GitHubReleases` check moves into the
  download branch.
- Line 592's `--no-github` bail runs before any image resolution, so it would also block
  the local path. It moves into the download branch too.

### 3. New slot — `installBootloaderFromLocal(target, local_path)`

Mirrors `installBootloader` (`motion_connector.py:1364`): same target whitelist, same
`consoleFirmwareUpdateBusy` gate, same `bootloaderInstallFinished` and
`consoleFirmwareUpdateProgress` signals. Follows the `beginDeviceFirmwareFromLocal`
precedent of a separate slot rather than overloading the tag argument.

### 4. QML — `pages/Settings.qml`

The three lock-button `onClicked` handlers (console 1636, left 1837, right 2033) are
near-identical copies of the same 12 lines. They collapse into one new helper,
`_startBootloaderInstall(target, tag)`, placed next to the existing
`_startFpgaFromLocal`.

New in the helper: when the selected tag is `Upload File...`, empty, or `N/A`, open a new
`blUploadDialog` FileDialog (`nameFilters: ["Firmware binaries (*.bin)"]`) for that target
instead of showing today's refusal.

On `onAccepted`, following the same URL-to-native-path normalization the two existing
upload dialogs use:

1. Call `validateProductionImage`. Non-empty result → `fwErrorDialog`, stop. Nothing is
   written and the warning modal never opens.
2. Set `blInstallLocalPath` and show the chosen **filename** in `bootloaderWarningDialog`
   in place of a release tag.
3. On confirm, route to `installBootloaderFromLocal` when `blInstallLocalPath` is set,
   otherwise to today's `installBootloader`.

`blInstallLocalPath` must be cleared whenever `_startBootloaderInstall` takes the
release-tag branch. Otherwise a local install followed by a tag-based one would silently
re-flash the stale local file.

The irreversible-install confirmation gate is preserved exactly as it is. This change
never removes or weakens it.

## Error handling

| Condition | Surfaced as |
|---|---|
| Wrong kind, missing "production", or nonexistent file | `fwErrorDialog` at file-selection time, before the warning modal |
| Same, reaching the thread from a non-UI caller | `bootloaderInstallFinished(target, false, msg)` — the existing result dialog |
| Device already has a bootloader / not bare metal | Unchanged: `BootloaderInstallError` from the SDK, which aborts without writing. Failure message gains a power-cycle hint (below). |
| `install_bootloader` unimportable (SDK too old) | Unchanged message |
| `--no-github` | No longer blocks the local path; still blocks the download path |

### The already-installed abort, and why it is left alone

Re-running an install on a converted device is safe: `install_bootloader`
(`omotion/bootloader_install.py:85`) enters DFU, reads the DFU alt-setting layout, and
raises `BootloaderInstallError("this device already has the bootloader installed; nothing
was written")` before `flash_bin` is ever reached. No erase, no write.

Two consequences were considered. Neither root cause is fixed here; one gets a mitigation:

**The device is left in DFU.** The abort happens after `enter_dfu()`, and it is
`flash_bin` that passes `:leave` to dfu-util (`omotion/DFUProgrammer.py:237`). Nothing on
the failure path exits DFU, so the unit stays there until power-cycled. A programmatic
exit would need a new `DFUProgrammer` detach call — an SDK change, out of scope. Mitigated
here by appending **"Power-cycle the device to bring it out of DFU."** to the install
failure message. This applies to every abort, including the "could not confirm this device
is bare metal" case, which no boot-mode query can prevent.

**The app does not record the mode it just detected.** `_note_boot_mode` is only called on
the success path (`motion_connector.py:661`), so an already-installed abort leaves the
padlock unlocked, and the operator can click straight into the same abort again. This is a
real bug, but it is reachable on console only and #73 is already closing that window — see
below. Recording the mode was scoped out on that basis.

### Boot-mode reporting is asymmetric today (context for the above)

The lock icon does not depend on a DFU op. `querySensorInfo` (`motion_connector.py:2510`)
and `queryConsoleInfo` (`motion_connector.py:2594`) both query `get_boot_mode()` over
normal comms via `OW_CMD_BOOT_INFO` on connect, and record definite answers.

- **Sensors:** firmware implements `OW_CMD_BOOT_INFO` (0x09,
  `openmotion-sensor-fw/Core/Src/if_commands.c:105`). The padlock closes on connect and the
  button goes inert. The abort path is effectively unreachable.
- **Console:** released firmware has no 0x0B handler — the command enum in
  `openmotion-console-fw/Core/Inc/common.h` jumps 0x0A → 0x0D. The SDK's query NAKs,
  yields `UNKNOWN`, and the call site records nothing, so a converted console reads
  unlocked indefinitely. This is open issue
  [openmotion-test-app#73](https://github.com/OpenwaterHealth/openmotion-test-app/issues/73),
  already implemented in an unmerged console-fw worktree.

So the abort path is reachable **on console only, until #73 ships**. That is why the
mode-recording fix is not worth carrying in this ticket.

## Testing

`tests/test_production_image_validation.py`, following the existing
`MOTIONConnector.__new__` + MagicMock-signal pattern from `test_boot_mode_cache.py`:

- accepts `motion-console-production.bin` for `console`
- accepts `motion-sensor-production.bin` for `left` and `right`
- accepts a version-stamped `motion-console-production-1.8.3.bin`
- rejects `motion-sensor-production.bin` for `console` (the brick case)
- rejects `motion-console-production.bin` for `left`
- rejects `motion-console-fw-signed.bin` (no "production")
- rejects a path that does not exist
- is case-insensitive

**Not covered by automated tests:** the flash itself. It needs a bare-metal console and
sensor on the bench and cannot be simulated. It will be handed over as a manual checklist
and reported as unverified, not as working:

1. Launch with `--no-github`; confirm the release dropdown holds only `Upload File...`.
2. Install the bootloader on a bare-metal console from a staged
   `motion-console-production.bin`; confirm the lock icon turns 🔒 and the device boots.
3. Repeat on a bare-metal sensor.
4. Select the sensor image for the console; confirm it is rejected before the warning
   modal and nothing is written.
5. Re-run the install on an already-converted device; confirm the SDK's
   "already installed" abort still fires, nothing is written, and the failure message
   carries the power-cycle hint. On a sensor the padlock should already read 🔒 and the
   button should be inert, making this hard to reach — try it on the console.
6. Regression: app firmware and FPGA `.jed` still flash from a local file under
   `--no-github`.

## Documentation

- `README.md` — **does not mention `--no-github` at all** (verified: no match in the file).
  Since this feature exists specifically to make the offline flow work, the flag and the
  offline firmware workflow get documented there. This is an addition, not a correction.
- `CLAUDE.md` — the `--no-github` note in "Working without hardware" and the firmware row
  of the "Start here" table both imply bootloader install is unavailable offline.
  **This file is untracked** (`git ls-files` has no match; it is not gitignored either), so
  it lives only in the main checkout and is absent from this worktree. It must be edited
  there, and the edit will not appear in the PR. Do it last, and call it out explicitly in
  the handoff rather than letting it look like part of the reviewed change.

Two stale comments are also corrected. Both claim boot mode is only knowable after a DFU
op, which stopped being true when the `OW_CMD_BOOT_INFO` query landed; both actively
mislead a reader about when the lock icon updates:

- `motion_connector.py:1434` — `deviceBootMode` docstring: "Empty is the normal state
  until an update or install has run"
- `pages/Settings.qml:1609` — "Boot mode is only known after a DFU op this session (or
  would need `OW_CMD_BOOT_INFO`)"

The QML one sits in the exact block being rewritten for the lock-button helper. The
corrected text should state what is actually true: the mode is queried over normal comms
on connect, sensors answer today, and the console will once #73 ships.
