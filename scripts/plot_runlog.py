#!/usr/bin/env python3
"""Plot run log values: TEC, PDU, and Analog vs time.

Usage: python plot_runlog.py --file path/to/run-YYYYMMDD_HHMMSS.log [--save out.png]
"""

import argparse
import re
from datetime import datetime
import matplotlib.pyplot as plt
import os


def parse_log(path):
    ts_re = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")
    data = {
        "tec": [],  # tuples: (dt, temp, set, tec_c, tec_v)
        "pdu0": [],  # tuples: (dt, [8 vals])
        "pdu1": [],
        "analog": [],  # tuples: (dt, tcm, tcl, pdc)
        "versions": {},
    }
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = ts_re.match(line)
            if not m:
                continue
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S,%f")
            if "App Version:" in line:
                v = line.split("App Version:")[-1].strip()
                data["versions"]["App"] = v
            if "SDK Version:" in line:
                v = line.split("SDK Version:")[-1].strip()
                data["versions"]["SDK"] = v
            if "Console Firmware:" in line:
                v = line.split("Console Firmware:")[-1].strip()
                data["versions"]["Console"] = v

            if "TEC Status" in line:
                # temp: 38.82 set: 39.62 tec_c: 2.354 tec_v: 2.175
                nums = re.findall(r"([a-zA-Z_]*):\s*([\-0-9\.]+)", line)
                mapping = {k: float(v) for k, v in nums}
                temp = mapping.get("temp")
                setp = mapping.get("set")
                tec_c = mapping.get("tec_c")
                tec_v = mapping.get("tec_v")
                data["tec"].append((ts, temp, setp, tec_c, tec_v))

            if "PDU MON ADC0 vals:" in line:
                vals = re.findall(r"([-0-9\.]+)", line.split("PDU MON ADC0 vals:")[-1])
                vals = [float(x) for x in vals]
                data["pdu0"].append((ts, vals))

            if "PDU MON ADC1 vals:" in line:
                vals = re.findall(r"([-0-9\.]+)", line.split("PDU MON ADC1 vals:")[-1])
                vals = [float(x) for x in vals]
                data["pdu1"].append((ts, vals))

            if "Analog Values" in line:
                # Analog Values - TCM: 76, TCL: 222, PDC: 2979.200
                nums = re.findall(r"([A-Z]{3}):\s*([-0-9\.]+)", line)
                amap = {k: float(v) for k, v in nums}
                tcm = amap.get("TCM")
                tcl = amap.get("TCL")
                pdc = amap.get("PDC")
                data["analog"].append((ts, tcm, tcl, pdc))

    return data


def to_seconds(base, dt):
    return (dt - base).total_seconds()


def plot_data(filepath, data, save_path=None, show=True):
    # choose base time
    all_times = []
    for k in ("tec", "pdu0", "pdu1", "analog"):
        for t in data[k]:
            all_times.append(t[0])
    if not all_times:
        raise SystemExit("No timestamped data found in log.")
    base = min(all_times)

    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)

    # Title and version info
    fname = os.path.basename(filepath)
    fig.suptitle(fname, fontsize=14)
    ver_lines = []
    for k in ("App", "SDK", "Console"):
        if k in data["versions"]:
            ver_lines.append(f"{k}: {data['versions'][k]}")
    ver_text = "\n".join(ver_lines)
    fig.text(0.01, 0.98, ver_text, ha="left", va="top", fontsize=10)

    # TEC plot
    if data["tec"]:
        times = [to_seconds(base, t[0]) for t in data["tec"]]
        temp = [t[1] for t in data["tec"]]
        setp = [t[2] for t in data["tec"]]
        tec_c = [t[3] for t in data["tec"]]
        tec_v = [t[4] for t in data["tec"]]
        ax = axes[0]
        ax.plot(times, temp, label="temp (C)")
        ax.plot(times, setp, label="set (C)", linestyle="--")
        ax.set_ylabel("Temp (C)")
        ax2 = ax.twinx()
        ax2.plot(times, tec_c, label="tec_c", color="C3", alpha=0.8)
        ax2.plot(times, tec_v, label="tec_v", color="C4", alpha=0.8)
        ax2.set_ylabel("TEC I/V")
        ax.legend(loc="upper left")
        ax2.legend(loc="upper right")
        ax.grid(True)
    else:
        axes[0].text(0.5, 0.5, "No TEC data", ha="center", va="center")

    # PDU plot
    ax = axes[1]
    plotted = False
    if data["pdu0"]:
        times0 = [to_seconds(base, t[0]) for t in data["pdu0"]]
        # transpose lists to channels
        vals0 = list(zip(*[t[1] for t in data["pdu0"]]))
        for i, ch in enumerate(vals0):
            ax.plot(times0, ch, label=f"ADC0_{i}")
            plotted = True
    if data["pdu1"]:
        times1 = [to_seconds(base, t[0]) for t in data["pdu1"]]
        vals1 = list(zip(*[t[1] for t in data["pdu1"]]))
        for i, ch in enumerate(vals1):
            ax.plot(times1, ch, label=f"ADC1_{i}", linestyle="--", alpha=0.8)
            plotted = True
    if plotted:
        ax.set_ylabel("PDU (V)")
        ax.legend(ncol=4, fontsize=8)
        ax.grid(True)
    else:
        ax.text(0.5, 0.5, "No PDU data", ha="center", va="center")

    # Analog plot
    ax = axes[2]
    if data["analog"]:
        times = [to_seconds(base, t[0]) for t in data["analog"]]
        tcm = [t[1] for t in data["analog"]]
        tcl = [t[2] for t in data["analog"]]
        pdc = [t[3] for t in data["analog"]]
        ax.plot(times, tcm, label="TCM")
        ax.plot(times, tcl, label="TCL")
        ax.plot(times, pdc, label="PDC")
        ax.set_ylabel("Analog")
        ax.legend()
        ax.grid(True)
    else:
        ax.text(0.5, 0.5, "No Analog data", ha="center", va="center")

    axes[-1].set_xlabel("Seconds since start")
    fig.tight_layout(rect=[0, 0, 1, 0.96])

    if save_path:
        fig.savefig(save_path, dpi=150)
        print("Saved plot to", save_path)
    if show:
        plt.show()


def main():
    p = argparse.ArgumentParser(description="Plot run log values")
    p.add_argument("--file", "-f", required=True, help="Path to run log")
    p.add_argument("--save", "-s", help="Save output image (png)")
    args = p.parse_args()
    data = parse_log(args.file)
    plot_data(args.file, data, save_path=args.save)


if __name__ == "__main__":
    main()
