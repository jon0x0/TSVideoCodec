#!/usr/bin/env python3
"""Import a known-good two-plane ECM CODE TAP as an SVD frame sequence."""

from __future__ import annotations

import argparse
from pathlib import Path

from svd_ecm import ECMFrame, PLANE_SIZE


def tap_blocks(data: bytes) -> list[bytes]:
    blocks = []
    offset = 0
    while offset < len(data):
        if offset + 2 > len(data):
            raise ValueError("truncated TAP block length")
        length = int.from_bytes(data[offset:offset + 2], "little")
        offset += 2
        if offset + length > len(data):
            raise ValueError("truncated TAP block")
        blocks.append(data[offset:offset + length])
        offset += length
    return blocks


def import_frame(tap: Path) -> ECMFrame:
    blocks = tap_blocks(tap.read_bytes())
    planes: dict[int, bytes] = {}
    pending_address = None
    for block in blocks:
        if len(block) == 19 and block[0] == 0 and block[1] == 3:
            pending_address = int.from_bytes(block[14:16], "little")
        elif block and block[0] == 0xFF and pending_address is not None:
            payload = block[1:-1]
            if len(payload) == PLANE_SIZE and pending_address in (0x4000, 0x6000):
                planes[pending_address] = payload
            pending_address = None
    missing = [f"${address:04X}" for address in (0x4000, 0x6000) if address not in planes]
    if missing:
        raise ValueError(f"missing 6144-byte CODE plane(s): {', '.join(missing)}")
    return ECMFrame(planes[0x4000], planes[0x6000])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tap", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--frames", type=int, default=3)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    frame = import_frame(args.tap)
    for index in range(args.frames):
        prefix = args.output / f"frame_{index:05d}"
        frame.write(prefix)
        frame.render().save(prefix.with_name(prefix.name + "_preview.png"))
    print(f"imported {args.tap} as {args.frames} identical SVD frames")


if __name__ == "__main__":
    main()
