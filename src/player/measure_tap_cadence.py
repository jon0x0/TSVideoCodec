#!/usr/bin/env python3
"""Measure hardware frames and ROM FRAMES ticks across one video TAP loop."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

from validate_ram_demo import DEFAULT_FUSE, symbol_address

HEX_LINE = re.compile(r"^0x([0-9a-f]+)$", re.IGNORECASE)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("build", type=Path)
    parser.add_argument("--fuse", type=Path, default=DEFAULT_FUSE)
    args = parser.parse_args()
    symbols = args.build / "svd_video_tap.symbols"
    start = symbol_address(symbols, "NEXT_FRAME")
    stop = symbol_address(symbols, "PAUSE_LAST")
    debugger = "\n".join((
        f"breakpoint 0x{start:04x}", f"breakpoint 0x{stop:04x}",
        "commands 1", "print spectrum:frames", "print [0x5c78]", "continue", "end",
        "commands 2", "print spectrum:frames", "print [0x5c78]", "exit", "end",
    ))
    startupinfo = subprocess.STARTUPINFO(); startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    result = subprocess.run([
        str(args.fuse), "--machine", "ts2068", "--speed", "5000", "--no-sound",
        "--no-loading-sound", "--tape", str((args.build / "svd_video.tap").resolve()),
        "--auto-load", "--debugger-command", debugger,
    ], capture_output=True, text=True, timeout=90, startupinfo=startupinfo,
       creationflags=subprocess.CREATE_NO_WINDOW)
    values = [int(match.group(1), 16) for line in result.stdout.splitlines()
              if (match := HEX_LINE.match(line.strip()))]
    if result.returncode or len(values) < 4 or len(values) % 2:
        raise SystemExit(result.stderr or f"unexpected Fuse values: {values}")
    samples = list(zip(values[0::2], values[1::2]))
    hardware_delta = samples[-1][0] - samples[0][0]
    system_delta = (samples[-1][1] - samples[0][1]) & 0xFF
    print(f"samples: {len(samples)}")
    print(f"hardware frames across loop: {hardware_delta}")
    print(f"ROM FRAMES low-byte ticks across loop: {system_delta}")
    print(f"hardware/system ratio: {hardware_delta / system_delta:.6f}")
    print("samples (hardware_frame, ROM_FRAMES_low):")
    print(samples)


if __name__ == "__main__":
    main()
