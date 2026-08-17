# openwater.spec — drop-in replacement
import os
import sys
import struct
from PyInstaller.utils.hooks import (
    collect_all, collect_dynamic_libs, collect_submodules
)
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT

APP_NAME = "TestApp"
ENTRY = "main.py"
ICON_FILE = os.path.abspath("assets/images/favicon.ico")

datas = []
hidden = []
binaries = []

# --- your existing resource folders (keep what you already had) ---
for item in ("main.qml",):
    if os.path.exists(item):
        datas.append((item, "."))
for folder in ("pages", "components", "assets", "models", "config"):
    if os.path.isdir(folder):
        datas.append((folder, folder))

# --- PyQt6 (keep as before) ---
qt_datas, qt_bins, qt_hidden = collect_all("PyQt6")
datas   += qt_datas
binaries += qt_bins
hidden  += qt_hidden
hidden  += collect_submodules("PyQt6")
hidden  += ["qasync"]

# --- ✅ add omotion explicitly ---
om_datas, om_bins, om_hidden = collect_all("omotion")
datas   += om_datas
binaries += om_bins
hidden  += om_hidden

# The Procedures pane runs omotion.scripts.* modules by re-executing this
# bundle (--run-procedure), so they must be IN the bundle. They are never
# imported statically — only ever by name at runtime — so name them
# explicitly rather than trusting analysis to reach them. Missing procedures
# fail at the bench, not at build time; assert instead.
procedure_modules = collect_submodules("omotion.scripts")
if not any(m.startswith("omotion.scripts.wi15") for m in procedure_modules):
    raise SystemExit(
        "[spec] omotion.scripts carries no wi15 procedure modules — the "
        "installed openmotion-sdk predates them. Install an SDK that ships "
        "them or the packaged Procedures pane will have nothing to run."
    )
if "omotion.scripts.framed_prompts" not in procedure_modules:
    raise SystemExit(
        "[spec] omotion.scripts.framed_prompts is missing — the installed "
        "openmotion-sdk predates structured prompt framing (SDK #241), so a "
        "packaged Procedures pane would fall back to prompt sniffing. "
        "Install an SDK that ships it."
    )
hidden += procedure_modules
print(f"[spec] Bundling {len(procedure_modules)} omotion.scripts module(s)")

# Runtime deps of the PYTHONPATH-sourced omotion that static analysis can
# miss (crcmod: wire-protocol CRC, found missing in the 2026-08-14 build).
for _extra in ("crcmod",):
    _e_datas, _e_bins, _e_hidden = collect_all(_extra)
    datas    += _e_datas
    binaries += _e_bins
    hidden   += _e_hidden

# pywin32: the Ophir meter layer (omotion.calibration.laser_hardware) does a
# deferred "import win32com.client", so nothing imports it statically. A build
# without it ships an exe whose WI-00015 procedures die at the bench with
# "No module named 'win32com'" (found in the 2026-08-17 factory install).
try:
    import win32com  # noqa: F401 — build-environment probe only
except ImportError:
    raise SystemExit(
        "[spec] pywin32 is missing from the build environment — the packaged "
        "Procedures pane could not drive the Ophir meter. "
        "pip install -r requirements.txt and rebuild."
    )
hidden += collect_submodules("win32com")
hidden += ["pythoncom", "pywintypes"]

# --- force include pyserial / pyusb dependency ---
hidden += [
    "serial",
    "serial.tools",
    "serial.tools.list_ports",
    "usb",
    "usb.core",
    "usb.util",
    "usb.backend.libusb1",
]

# Optional: if you also have a separate 'libusb' wheel installed, this won't hurt
try:
    binaries += collect_dynamic_libs("libusb")
except Exception:
    pass

# ---------- MIRROR omotion vendored libusb under _internal\_vendor ----------
# Some builds only carry the vendored files inside _internal\omotion\_vendor\...
# We duplicate those files to _internal\_vendor\... so the wheel's _dll_dir() can find them.
def _norm(p): return p.replace("/", os.sep).replace("\\", os.sep)

arch = "x64" if 8 * struct.calcsize("P") == 64 else "x86"
needle = _norm(os.path.join("omotion", "_vendor", "libusb", "windows"))
dst_base_vendor = _norm(os.path.join("_vendor", "libusb", "windows"))

def _mirror_vendor_from_collected(collected_list):
    """Look through (src, dst) entries; if dst contains omotion\\_vendor\\libusb\\windows\\<arch>,
       add duplicates into _internal\\_vendor\\libusb\\windows\\<arch>."""
    added = 0
    for src, dst in list(collected_list):  # iterate over a snapshot
        ndst = _norm(dst)
        if needle in ndst:
            # Extract arch subdir if present
            parts = ndst.split(os.sep)
            try:
                idx = parts.index("windows")
                arch_part = parts[idx + 1] if idx + 1 < len(parts) else arch
            except ValueError:
                arch_part = arch

            target_dir = os.path.join(dst_base_vendor, arch_part)
            # Add as a binary to ensure it lands under _internal
            binaries.append((src, target_dir))
            added += 1
            
    print(f"[spec] Mirrored {added} vendored libusb file(s) to {dst_base_vendor}\\<arch>")

# Mirror from both omotion datas and bins (some wheels mark them as datas)
_mirror_vendor_from_collected(om_datas)
_mirror_vendor_from_collected(om_bins)
# ---------------------------------------------------------------------------

# Optionally add a runtime hook to put these dirs on the DLL path for Windows
runtime_hooks = ["rthook_libusb_paths.py"]

a = Analysis(
    [ENTRY],
    pathex=[],                      # you can leave this empty now
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=['PySide6','shiboken6','PySide2','PyQt5'],  # avoid mixed Qt
    runtime_hooks=runtime_hooks,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe_gui = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    icon=ICON_FILE,
    upx=False   # safer for DLLs on Windows
)

exe_cli = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=f"{APP_NAME}_console",
    console=True,
    icon=ICON_FILE,
    upx=False
)

coll = COLLECT(
    exe_gui, exe_cli,
    a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name=APP_NAME
)
