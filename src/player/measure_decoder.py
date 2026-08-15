#!/usr/bin/env python3
"""Measure each SVD frame decode in Fuse using debugger frame/T-state counters."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
DEFAULT_FUSE = Path(os.environ.get("FUSE", shutil.which("fuse") or "fuse"))
HEX_LINE = re.compile(r"^0x([0-9a-f]+)$", re.IGNORECASE)
FRAME_TSTATES = 58_688
CPU_HZ = 3_528_000


def symbols(path: Path) -> dict[str, int]:
    return {
        name: int(value, 16)
        for name, value in re.findall(r"^([A-Z_]+)\s+EQU\s+0([0-9A-F]+)H$", path.read_text(), re.MULTILINE)
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("demo", type=Path, nargs="?", default=ROOT / "build" / "ram_demo")
    parser.add_argument("--fuse", type=Path, default=DEFAULT_FUSE)
    args = parser.parse_args()
    addresses = symbols(args.demo / "svd_ram_demo.symbols")
    next_frame = addresses["NEXT_FRAME"]
    frame_ready = addresses["FRAME_READY"]
    hold_last = addresses["HOLD_LAST"]
    debugger = "\n".join((
        f"breakpoint 0x{next_frame:04x}",
        f"breakpoint 0x{frame_ready:04x}",
        f"breakpoint 0x{hold_last:04x}",
        "commands 1", "print z80:pc", "print spectrum:frames", "print ula:tstates", "continue", "end",
        "commands 2", "print z80:pc", "print spectrum:frames", "print ula:tstates", "continue", "end",
        "commands 3", "exit", "end",
    ))
    command = [
        str(args.fuse), "--machine", "ts2068", "--speed", "5000", "--no-sound",
        "--no-loading-sound", "--tape", str((args.demo / "svd_ram_demo.tap").resolve()),
        "--auto-load", "--debugger-command", debugger,
    ]
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=90,
        startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise SystemExit(result.stderr or "Fuse timing run failed")
    values = [int(match.group(1), 16) for line in result.stdout.splitlines()
              if (match := HEX_LINE.match(line.strip()))]
    if len(values) != 18:
        raise SystemExit(f"expected 18 timing values, got {len(values)}\n{result.stdout}\n{result.stderr}")
    events = [tuple(values[index:index + 3]) for index in range(0, len(values), 3)]
    frame_types = [row["frame_type"] for row in csv.DictReader((args.demo / "demo.csv").open())]
    rows = []
    for index in range(3):
        start_pc, start_frame, start_tstates = events[index * 2]
        end_pc, end_frame, end_tstates = events[index * 2 + 1]
        if start_pc != next_frame or end_pc != frame_ready:
            raise SystemExit("unexpected breakpoint order during timing")
        elapsed = (end_frame - start_frame) * FRAME_TSTATES + end_tstates - start_tstates
        rows.append({
            "frame": index,
            "frame_type": frame_types[index],
            "tstates": elapsed,
            "milliseconds": round(elapsed * 1000 / CPU_HZ, 3),
            "ready_absolute_tstates": end_frame * FRAME_TSTATES + end_tstates,
        })
    output = args.demo / "decoder_timing.json"
    output.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    for row in rows:
        print(f"frame {row['frame']} {row['frame_type']}: {row['tstates']} T-states ({row['milliseconds']:.3f} ms)")
    for index in range(1, len(rows)):
        interval = rows[index]["ready_absolute_tstates"] - rows[index - 1]["ready_absolute_tstates"]
        print(f"ready interval {index - 1}->{index}: {interval} T-states ({interval * 1000 / CPU_HZ:.3f} ms)")
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
