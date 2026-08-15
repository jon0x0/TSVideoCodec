#!/usr/bin/env python3
"""Boot the SVD DCK in Fuse and verify the displayed ECM keyframe exactly."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
from pathlib import Path

HEX_LINE = re.compile(r"^0x([0-9a-f]+)$", re.IGNORECASE)
DEFAULT_FUSE = Path(os.environ.get("FUSE", shutil.which("fuse") or "fuse"))


def symbol_address(path: Path, name: str) -> int:
    match = re.search(rf"^{name}\s+EQU\s+0([0-9A-F]+)H$", path.read_text(), re.MULTILINE)
    if not match:
        raise SystemExit(f"symbol not found: {name}")
    return int(match.group(1), 16)


def capture(fuse: Path, dck: Path, hold: int, start: int, count: int) -> bytes:
    commands = [f"breakpoint 0x{hold:04x}", "commands 1"]
    commands.extend(f"print [0x{address:04x}]" for address in range(start, start + count))
    commands += ["exit", "end"]
    launch = [
        str(fuse), "--machine", "ts2068", "--speed", "5000", "--no-sound",
        "--dock", str(dck.resolve()), "--debugger-command", "\n".join(commands),
    ]
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    result = subprocess.run(
        launch, capture_output=True, text=True, timeout=90, startupinfo=startupinfo,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise SystemExit(result.stderr or "Fuse cartridge validation failed")
    values = [int(match.group(1), 16) for line in result.stdout.splitlines()
              if (match := HEX_LINE.match(line.strip()))]
    if len(values) != count:
        raise SystemExit(f"captured {len(values)} bytes at ${start:04X}, expected {count}")
    return bytes(values)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("build", type=Path)
    parser.add_argument("sequence", type=Path)
    parser.add_argument("--fuse", type=Path, default=DEFAULT_FUSE)
    parser.add_argument("--frame", choices=("first", "last"), default="last")
    parser.add_argument("--expected-index", type=int,
                        help="zero-based sequence frame expected at the breakpoint")
    parser.add_argument("--capture-output", type=Path,
                        help="write the captured 12K ECM planes for diagnostics")
    args = parser.parse_args()
    label = "HOLD" if args.frame == "first" else "PAUSE_LAST"
    hold = symbol_address(args.build / "cartridge_boot.symbols", label)
    dck = args.build / "svd_video_64k.dck"
    captured_parts = []
    for base in (0x4000, 0x6000):
        for offset in range(0, 0x1800, 0x400):
            captured_parts.append(capture(args.fuse, dck, hold, base + offset, 0x400))
    prefixes = sorted(args.sequence.glob("frame_*.pix"))
    expected_prefix = (prefixes[args.expected_index] if args.expected_index is not None else
                       (prefixes[0] if args.frame == "first" else prefixes[-1]))
    expected = expected_prefix.read_bytes() + expected_prefix.with_suffix(".atr").read_bytes()
    captured = b"".join(captured_parts)
    if args.capture_output:
        args.capture_output.parent.mkdir(parents=True, exist_ok=True)
        args.capture_output.write_bytes(captured)
    if captured != expected:
        bitmap_diffs = sum(a != b for a, b in zip(captured[:0x1800], expected[:0x1800]))
        attr_diffs = sum(a != b for a, b in zip(captured[0x1800:], expected[0x1800:]))
        raise SystemExit(f"Fuse plane mismatch: bitmap={bitmap_diffs}, attributes={attr_diffs}")
    print(f"Fuse reached cartridge {label} at ${hold:04X}")
    print(f"displayed bitmap and ECM attributes match the encoded {args.frame} frame exactly")


if __name__ == "__main__":
    main()
