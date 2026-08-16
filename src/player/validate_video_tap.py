#!/usr/bin/env python3
"""Boot the autostart video TAP in Fuse and verify its last ECM frame."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from validate_ram_demo import DEFAULT_FUSE, capture, symbol_address


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("build", type=Path)
    parser.add_argument("sequence", type=Path)
    parser.add_argument("--fuse", type=Path, default=DEFAULT_FUSE)
    args = parser.parse_args()
    tap = args.build / "svd_video.tap"
    hold = symbol_address(args.build / "svd_video_tap.symbols", "PAUSE_LAST")
    captured = b"".join(capture(args.fuse, tap, hold, base + offset, 0x400)
                        for base in (0x4000, 0x6000)
                        for offset in range(0, 0x1800, 0x400))
    manifest = json.loads((args.build / "tap_manifest.json").read_text())
    pix_files = sorted(args.sequence.glob("frame_*.pix"))
    # A bounce cycle ends at source frame 1 so its first loop delta can return
    # to frame 0 without duplicating either endpoint.
    pix = pix_files[1] if manifest.get("bounce") else pix_files[-1]
    expected = pix.read_bytes() + pix.with_suffix(".atr").read_bytes()
    if captured != expected:
        bitmap = sum(a != b for a, b in zip(captured[:0x1800], expected[:0x1800]))
        attrs = sum(a != b for a, b in zip(captured[0x1800:], expected[0x1800:]))
        raise SystemExit(f"Fuse TAP mismatch: bitmap={bitmap}, attributes={attrs}")
    print(f"Fuse reached TAP PAUSE_LAST at ${hold:04X}")
    print(f"TAP bitmap and ECM attributes match encoded frame {pix.stem} exactly")


if __name__ == "__main__":
    main()
