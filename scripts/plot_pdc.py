#!/usr/bin/env python3
"""Plot only PDC (analog) values from a run log.

Usage:
  python plot_pdc.py --file path/to/run-YYYYMMDD_HHMMSS.log [--save out.png]
"""

import argparse
import re
from datetime import datetime
import matplotlib.pyplot as plt
import os


def parse_pdc(path):
    ts_re = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")
    pdc_list = []
    versions = {}
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = ts_re.match(line)
            if not m:
                continue
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S,%f")
            if "App Version:" in line:
                versions["App"] = line.split("App Version:")[-1].strip()
            if "SDK Version:" in line:
                versions["SDK"] = line.split("SDK Version:")[-1].strip()
            if "Console Firmware:" in line:
                versions["Console"] = line.split("Console Firmware:")[-1].strip()

            if "Analog Values" in line:
                # Analog Values - TCM: 76, TCL: 222, PDC: 2979.200
                mvals = re.search(r"PDC:\s*([-0-9\.]+)", line)
                if mvals:
                    try:
                        pdc = float(mvals.group(1))
                        pdc_list.append((ts, pdc))
                    except ValueError:
                        continue

    return versions, pdc_list


def to_seconds(base, dt):
    return (dt - base).total_seconds()


def plot_pdc(filepath, versions, pdc_list, save_path=None, show=True):
    if not pdc_list:
        raise SystemExit("No PDC data found in log.")
    base = min(t[0] for t in pdc_list)
    times = [to_seconds(base, t[0]) for t in pdc_list]
    pdc = [t[1] for t in pdc_list]

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(times, pdc, marker="o", linestyle="-", color="C1")
    ax.set_xlabel("Seconds since start")
    ax.set_ylabel("PDC")
    ax.grid(True)

    # Title center-top with filename
    fname = os.path.basename(filepath)
    fig.suptitle(fname)

    # Version info upper-left
    ver_lines = []
    for k in ("App", "SDK", "Console"):
        if k in versions:
            ver_lines.append(f"{k}: {versions[k]}")
    if ver_lines:
        fig.text(0.01, 0.98, "\n".join(ver_lines), ha="left", va="top", fontsize=9)

    fig.tight_layout(rect=[0, 0, 1, 0.95])
    if save_path:
        fig.savefig(save_path, dpi=150)
        print("Saved PDC plot to", save_path)
    if show:
        plt.show()


def main():
    p = argparse.ArgumentParser(description="Plot PDC values from run log")
    p.add_argument("--file", "-f", required=True, help="Path to run log")
    p.add_argument("--save", "-s", help="Save output image (png)")
    args = p.parse_args()
    versions, pdc_list = parse_pdc(args.file)
    plot_pdc(args.file, versions, pdc_list, save_path=args.save)


if __name__ == "__main__":
    main()
