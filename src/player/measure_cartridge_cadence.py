#!/usr/bin/env python3
"""Measure hardware and ROM frame ticks across one cartridge sequence."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from measure_cartridge_decoder import DEFAULT_FUSE, symbols

HEX_LINE = re.compile(r"^0x([0-9a-f]+)$", re.IGNORECASE)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("build", type=Path)
    parser.add_argument("--fuse", type=Path, default=DEFAULT_FUSE)
    args = parser.parse_args()
    manifest = json.loads((args.build / "manifest.json").read_text(encoding="utf-8"))
    addresses = symbols(args.build / "cartridge_boot.symbols")
    start, stop = addresses["NEXT_FRAME"], addresses["PAUSE_LAST"]
    debugger = "\n".join((
        f"breakpoint 0x{start:04x}", f"breakpoint 0x{stop:04x}",
        "commands 1", "print spectrum:frames", "print [0x5c78]", "continue", "end",
        "commands 2", "print spectrum:frames", "print [0x5c78]", "exit", "end"))
    startupinfo = subprocess.STARTUPINFO(); startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    result = subprocess.run([
        str(args.fuse), "--machine", "ts2068", "--speed", "5000", "--no-sound",
        "--dock", str((args.build / "svd_video_64k.dck").resolve()),
        "--debugger-command", debugger], capture_output=True, text=True, timeout=90,
        startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW)
    values = [int(match.group(1), 16) for line in result.stdout.splitlines()
              if (match := HEX_LINE.match(line.strip()))]
    expected_samples = manifest["frame_count"] + 1
    if result.returncode or len(values) != expected_samples * 2:
        raise SystemExit(result.stderr or f"expected {expected_samples * 2} values, got {len(values)}")
    samples = list(zip(values[0::2], values[1::2]))
    hardware_delta = samples[-1][0] - samples[0][0]
    system_delta = (samples[-1][1] - samples[0][1]) & 0xff
    fps = manifest["frame_count"] * 60.0 / hardware_delta
    start_frames = [sample[0] for sample in samples[:-1]]
    steady_gaps = [b - a for a, b in zip(start_frames[1:-1], start_frames[2:])]
    steady_fps = 60.0 / (sum(steady_gaps) / len(steady_gaps)) if steady_gaps else 0.0
    report = {"frames": manifest["frame_count"], "hardware_frames": hardware_delta,
              "rom_frame_ticks": system_delta, "whole_sequence_fps_including_keyframe": fps,
              "steady_delta_fps": steady_fps, "steady_hardware_frame_gaps": steady_gaps,
              "samples": samples}
    (args.build / "cadence.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "samples"}, indent=2))
    print(f"wrote {args.build / 'cadence.json'}")


if __name__ == "__main__":
    main()
