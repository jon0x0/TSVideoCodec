#!/usr/bin/env python3
"""Reproducibly encode and build the passive-64K Kahnankas video loop."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
DEFAULT_VIDEO = ROOT / "video" / "Kahnankas.mp4"


def run(*arguments: object) -> None:
    subprocess.run([sys.executable, *(str(item) for item in arguments)], cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, default=DEFAULT_VIDEO)
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "kahnankas_loop_7_5fps")
    parser.add_argument("--validate", action="store_true", help="run Fuse accuracy and timing checks")
    args = parser.parse_args()
    sequence = args.output / "sequence"
    stream = args.output / "video.svd"
    run("src/encoder/encode_sequence.py", args.video, sequence,
        "--fps", 7.5, "--geometry", "crop", "--dither-mode", "sierra-lite",
        "--brightness", -0.02, "--sierra-gamma", 1.3,
        "--temporal-attr-penalty", 0.3, "--temporal-pixel-penalty", 0.3,
        "--background-motion-threshold", 8, "--background-penalty-multiplier", 4,
        "--clip-delta-bytes", 39000, "--clip-bitmap-fraction", 0.8,
        "--clip-min-frame-bytes", 100, "--clip-max-frame-bytes", 3600,
        "--max-attribute-age", 4)
    run("src/encoder/pack_svd.py", sequence, stream,
        "--fps-num", 15, "--fps-den", 2, "--delta-format", "hybrid")
    run("src/cartridge/build_cartridge.py", sequence, stream, args.output,
        "--seamless-loop", "--loop-pause-frames", 0)
    if args.validate:
        run("src/cartridge/validate_cartridge.py", args.output, sequence, "--frame", "last")
        run("src/player/measure_cartridge_decoder.py", args.output)


if __name__ == "__main__":
    main()
