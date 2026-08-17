#!/usr/bin/env python3
"""Verify the visible first boundary of a two-slice cartridge update."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from validate_cartridge import DEFAULT_FUSE, capture, symbol_address

import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "encoder"))
from svd_ecm import screen_offset


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("build", type=Path)
    parser.add_argument("sequence", type=Path)
    parser.add_argument("--fuse", type=Path, default=DEFAULT_FUSE)
    args = parser.parse_args()

    boundary = symbol_address(args.build / "cartridge_boot.symbols", "SLICE_WAIT")
    dck = args.build / "svd_video_64k.dck"
    captured = bytearray()
    for base in (0x4000, 0x6000):
        for offset in range(0, 0x1800, 0x400):
            captured += capture(args.fuse, dck, boundary, base + offset, 0x400)

    prefixes = sorted(args.sequence.glob("frame_*.pix"))
    if len(prefixes) < 2:
        raise SystemExit("two-slice validation requires at least two frames")
    first = prefixes[0].read_bytes() + prefixes[0].with_suffix(".atr").read_bytes()
    second = prefixes[1].read_bytes() + prefixes[1].with_suffix(".atr").read_bytes()
    manifest = json.loads((args.build / "manifest.json").read_text())
    order = manifest.get("slice_order", "bands")
    expected = bytearray(first)
    for plane in (0, 0x1800):
        rows = range(0, 192, 2) if order == "interlaced" else range(96)
        for y in rows:
            for xb in range(32):
                offset = screen_offset(y, xb)
                expected[plane + offset] = second[plane + offset]
    if captured != expected:
        bitmap = sum(a != b for a, b in zip(captured[:0x1800], expected[:0x1800]))
        attrs = sum(a != b for a, b in zip(captured[0x1800:], expected[0x1800:]))
        raise SystemExit(f"slice boundary mismatch: bitmap={bitmap}, attributes={attrs}")
    print(f"first {order} slice matches the expected intermediate display exactly")


if __name__ == "__main__":
    main()
