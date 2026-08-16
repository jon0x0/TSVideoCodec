#!/usr/bin/env python3
"""One-command TSVideoCodec front end for TAP and 64 KB cartridge output."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).parent


def run(script: str, *arguments: object) -> None:
    subprocess.run(
        [sys.executable, str(ROOT / script), *(str(value) for value in arguments)],
        cwd=ROOT,
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a GIF or video directly to a TS2068 TAP or cartridge")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path,
                        help="generated working directory and artifact destination")
    parser.add_argument("--format", choices=("cartridge", "tap", "both"),
                        default="cartridge")
    parser.add_argument("--fps", type=float, default=12.0)
    parser.add_argument("--max-frames", type=int, default=12,
                        help="zero selects every source frame")
    parser.add_argument("--start-seconds", type=float, default=0.0)
    parser.add_argument("--geometry", choices=("fit", "crop"), default="fit")
    parser.add_argument("--encoder", choices=("python", "native"), default="python")
    parser.add_argument("--max-hybrid-bytes", type=int, default=1400,
                        help="per-frame reconstructed delta budget; zero disables")
    parser.add_argument("--keyframe-codec", choices=("raw", "packbits", "auto"),
                        default="auto", help="cartridge initial-frame storage")
    parser.add_argument("--dither-mode", choices=("sierra-lite", "legacy"),
                        default="sierra-lite")
    parser.add_argument("--auto", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--transport",
                        choices=("hybrid", "paired", "row-hybrid", "raster"),
                        default="paired",
                        help="cartridge update transport")
    parser.add_argument("--loop", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--loop-pause-frames", type=int, default=0)
    parser.add_argument("--pasmo", default=None,
                        help="Pasmo executable; defaults to PASMO or PATH")
    args = parser.parse_args()

    if args.fps <= 0:
        parser.error("--fps must be positive")
    if args.max_frames < 0:
        parser.error("--max-frames cannot be negative")
    if args.max_hybrid_bytes < 0:
        parser.error("--max-hybrid-bytes cannot be negative")
    if args.format in ("tap", "both") and not args.loop:
        parser.error("the current TAP player is looping; --no-loop is cartridge-only")

    source = args.input.resolve()
    if not source.is_file():
        parser.error(f"input does not exist: {source}")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sequence = output / "sequence"
    stream = output / "video.svd"
    rate = Fraction(str(args.fps)).limit_denominator(255)

    encoder_args: list[object] = [
        source, sequence,
        "--fps", args.fps,
        "--max-frames", args.max_frames,
        "--start-seconds", args.start_seconds,
        "--geometry", args.geometry,
        "--encoder", args.encoder,
        "--dither-mode", args.dither_mode,
        "--max-hybrid-bytes", args.max_hybrid_bytes,
    ]
    if args.auto:
        encoder_args.append("--auto")
    run("src/encoder/encode_sequence.py", *encoder_args)

    artifacts: dict[str, str] = {}
    if args.format in ("cartridge", "both"):
        run("src/encoder/pack_svd.py", sequence, stream,
            "--fps-num", rate.numerator, "--fps-den", rate.denominator,
            "--delta-format", "hybrid")
        cartridge_args: list[object] = [sequence, stream, output / "cartridge"]
        cartridge_args += ["--keyframe-codec", args.keyframe_codec]
        if args.loop:
            cartridge_args += ["--seamless-loop", "--loop-pause-frames",
                               args.loop_pause_frames]
        else:
            cartridge_args.append("--stop-at-end")
        transport_flag = {
            "hybrid": None,
            "paired": "--paired-cell-updates",
            "row-hybrid": "--row-hybrid-updates",
            "raster": "--raster-updates",
        }[args.transport]
        if transport_flag:
            cartridge_args.append(transport_flag)
        if args.pasmo:
            cartridge_args += ["--pasmo", args.pasmo]
        run("src/cartridge/build_cartridge.py", *cartridge_args)
        artifacts["dck"] = str(output / "cartridge" / "svd_video_64k.dck")
        artifacts["cartridge_bin"] = str(output / "cartridge" / "svd_video_64k.bin")

    if args.format in ("tap", "both"):
        tap_args: list[object] = [
            sequence, output / "tap",
            "--fps-num", rate.numerator, "--fps-den", rate.denominator,
            "--keyframe-codec", args.keyframe_codec,
        ]
        if args.pasmo:
            tap_args += ["--pasmo", args.pasmo]
        run("src/player/build_video_tap.py", *tap_args)
        artifacts["tap"] = str(output / "tap" / "svd_video.tap")

    manifest = {
        "source": str(source), "format": args.format,
        "fps_num": rate.numerator, "fps_den": rate.denominator,
        "max_frames": args.max_frames, "start_seconds": args.start_seconds,
        "geometry": args.geometry, "encoder": args.encoder, "auto": args.auto,
        "max_hybrid_bytes": args.max_hybrid_bytes,
        "keyframe_codec": args.keyframe_codec,
        "transport": args.transport if args.format != "tap" else "tap-raster",
        "loop": args.loop, "artifacts": artifacts,
    }
    (output / "build.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("completed TSVideoCodec build")
    for kind, path in artifacts.items():
        print(f"{kind}: {path}")


if __name__ == "__main__":
    main()
