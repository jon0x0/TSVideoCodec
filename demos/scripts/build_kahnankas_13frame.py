#!/usr/bin/env python3
"""Reproducibly build the original 13-frame Kahnankas loop at high quality."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]


def run(*args: object) -> None:
    subprocess.run([sys.executable, *(str(arg) for arg in args)], cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, default=ROOT / "video" / "Kahnankas.mp4")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "kahnankas_13frame_1_1s")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--raster-updates", action="store_true",
                        help="use slower top-to-bottom paired updates to reduce visible tearing")
    parser.add_argument("--tap-output", type=Path,
                        help="also build the separate contiguous-RAM TAP player here")
    args = parser.parse_args()
    source_export = args.output / "selected_source"
    sequence, stream = args.output / "sequence", args.output / "video.svd"
    run("demos/scripts/export_kahnankas_frames.py", "--video", args.video,
        "--output", source_export)
    run("src/encoder/encode_sequence.py", source_export / "selected_loop.mkv", sequence,
        "--fps", 130 / 11, "--max-frames", 13, "--geometry", "crop",
        "--dither-mode", "sierra-lite", "--brightness", -0.02,
        "--sierra-gamma", 1.3, "--temporal-attr-penalty", 0.08,
        "--temporal-pixel-penalty", 0.08, "--background-motion-threshold", 8,
        "--background-penalty-multiplier", 4)
    run("src/encoder/pack_svd.py", sequence, stream, "--fps-num", 130,
        "--fps-den", 11, "--delta-format", "hybrid")
    if args.tap_output:
        run("src/player/build_video_tap.py", sequence, args.tap_output,
            "--fps-num", 130, "--fps-den", 11)
    cartridge_args: list[object] = ["src/cartridge/build_cartridge.py", sequence, stream,
                                    args.output, "--seamless-loop", "--loop-pause-frames", 0]
    if args.raster_updates:
        cartridge_args.append("--raster-updates")
    run(*cartridge_args)
    if args.validate:
        run("src/cartridge/validate_cartridge.py", args.output, sequence, "--frame", "last")
        run("src/player/measure_cartridge_decoder.py", args.output)
        if args.tap_output:
            run("src/player/validate_video_tap.py", args.tap_output, sequence)


if __name__ == "__main__":
    main()
