#!/usr/bin/env python3
"""Compare a Fuse ECM capture with an encoded sequence by logical raster cell."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "src" / "encoder"))
from svd_ecm import screen_offset


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", type=Path)
    parser.add_argument("sequence", type=Path)
    parser.add_argument("--expected-index", type=int, default=-1)
    args = parser.parse_args()
    captured = args.capture.read_bytes()
    frames = []
    for pix in sorted(args.sequence.glob("frame_*.pix")):
        frames.append(pix.read_bytes() + pix.with_suffix(".atr").read_bytes())
    for index, frame in enumerate(frames):
        print(index, sum(a != b for a, b in zip(captured, frame)))
    expected = frames[args.expected_index]
    rows = []
    for y in range(192):
        bitmap = attributes = 0
        for x in range(32):
            offset = screen_offset(y, x)
            bitmap += captured[offset] != expected[offset]
            attributes += captured[0x1800 + offset] != expected[0x1800 + offset]
        if bitmap or attributes:
            rows.append((y, bitmap, attributes))
    print("differing logical rows (y, bitmap, attributes):")
    print(rows)


if __name__ == "__main__":
    main()
