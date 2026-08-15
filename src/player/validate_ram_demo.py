#!/usr/bin/env python3
"""Run the autostart TAP in Fuse and verify its final ECM planes exactly."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).parents[1]
DEFAULT_FUSE = Path(os.environ.get("FUSE", shutil.which("fuse") or "fuse"))
HEX_LINE = re.compile(r"^0x([0-9a-f]+)$", re.IGNORECASE)


def symbol_address(symbols: Path, name: str) -> int:
    match = re.search(rf"^{re.escape(name)}\s+EQU\s+0([0-9A-F]+)H$", symbols.read_text(), re.MULTILINE)
    if not match:
        raise SystemExit(f"symbol not found: {name}")
    return int(match.group(1), 16)


def capture(fuse: Path, tap: Path, breakpoint: int, start: int, count: int) -> bytes:
    commands = [f"breakpoint 0x{breakpoint:04x}", "commands 1"]
    commands += [f"print [0x{address:04x}]" for address in range(start, start + count)]
    commands += ["exit", "end"]
    command = [
        str(fuse), "--machine", "ts2068", "--speed", "5000", "--no-sound",
        "--no-loading-sound", "--tape", str(tap.resolve()), "--auto-load",
        "--debugger-command", "\n".join(commands),
    ]
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=90,
        startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise SystemExit(result.stderr or f"Fuse failed capturing ${start:04X}")
    values = [int(match.group(1), 16) for line in result.stdout.splitlines()
              if (match := HEX_LINE.match(line.strip()))]
    if len(values) != count:
        raise SystemExit(
            f"expected {count} bytes at ${start:04X}, captured {len(values)}\n"
            f"stdout tail: {result.stdout[-1000:]}\nstderr: {result.stderr}"
        )
    return bytes(values)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("demo", type=Path, nargs="?", default=ROOT / "build" / "ram_demo")
    parser.add_argument("--fuse", type=Path, default=DEFAULT_FUSE)
    parser.add_argument("--chunk-size", type=int, default=1024)
    args = parser.parse_args()
    tap = args.demo / "svd_ram_demo.tap"
    hold = symbol_address(args.demo / "svd_ram_demo.symbols", "HOLD_LAST")
    captured = []
    for base in (0x4000, 0x6000):
        plane = bytearray()
        for offset in range(0, 0x1800, args.chunk_size):
            length = min(args.chunk_size, 0x1800 - offset)
            plane += capture(args.fuse, tap, hold, base + offset, length)
        captured.append(bytes(plane))
    prefixes = sorted((args.demo / "sequence").glob("frame_*.pix"))
    if not prefixes:
        raise SystemExit("demo sequence contains no frames")
    expected_bitmap = prefixes[-1].read_bytes()
    expected_attrs = prefixes[-1].with_suffix(".atr").read_bytes()
    if captured[0] != expected_bitmap or captured[1] != expected_attrs:
        bitmap_diffs = sum(a != b for a, b in zip(captured[0], expected_bitmap))
        attr_diffs = sum(a != b for a, b in zip(captured[1], expected_attrs))
        raise SystemExit(f"Fuse mismatch: bitmap={bitmap_diffs} bytes, attributes={attr_diffs} bytes")
    sys_path = str(ROOT / "encoder")
    import sys
    sys.path.insert(0, sys_path)
    from svd_ecm import ECMFrame
    preview = args.demo / "fuse_final.png"
    ECMFrame(captured[0], captured[1]).render().save(preview)
    print(f"Fuse reached HOLD_LAST at ${hold:04X}")
    print("final bitmap and ECM attribute planes match exactly (12288 bytes)")
    print(f"wrote {preview}")


if __name__ == "__main__":
    main()
