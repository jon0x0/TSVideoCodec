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
    parser.add_argument("--bounce", action="store_true",
                        help="play forward then reverse without duplicate endpoints")
    parser.add_argument("--start-seconds", type=float, default=0.0)
    parser.add_argument("--geometry", choices=("fit", "crop"), default="fit")
    source_window = parser.add_mutually_exclusive_group()
    source_window.add_argument(
        "--source-window", metavar="X,Y,RIGHT",
        help="normalized upper-left X,Y and right edge; viewport is 4:3")
    source_window.add_argument(
        "--source-window-pixels", metavar="X,Y,WIDTH",
        help="pixel upper-left X,Y and maximum width; viewport is 4:3")
    parser.add_argument("--encoder", choices=("python", "native"), default="python")
    parser.add_argument("--max-hybrid-bytes", type=int, default=1400,
                        help="per-frame reconstructed delta budget; zero disables")
    parser.add_argument("--clip-delta-bytes", type=int, default=0,
                        help="total byte budget shared across all non-key frames")
    parser.add_argument("--clip-min-frame-bytes", type=int, default=200)
    parser.add_argument("--clip-max-frame-bytes", type=int, default=0)
    parser.add_argument("--keyframe-codec", choices=("raw", "packbits", "auto"),
                        default="auto", help="cartridge initial-frame storage")
    parser.add_argument("--dither-mode", choices=("sierra-lite", "legacy"),
                        default="sierra-lite")
    parser.add_argument("--temporal-attr-penalty", type=float, default=0.01)
    parser.add_argument("--temporal-pixel-penalty", type=float, default=0.01)
    parser.add_argument("--auto", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--transport",
                        choices=("hybrid", "paired", "row-hybrid", "raster"),
                        default="paired",
                        help="cartridge update transport")
    parser.add_argument("--fifo-packing", action="store_true",
                        help="pack hybrid cartridge deltas contiguously across banks")
    parser.add_argument("--loop", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--loop-transition", choices=("delta", "keyframe"), default="delta",
                        help="last-to-first delta or replay the original keyframe")
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
    if args.clip_delta_bytes < 0 or args.clip_min_frame_bytes < 1 or args.clip_max_frame_bytes < 0:
        parser.error("clip byte budgets must be non-negative and minimum must be positive")
    if args.clip_delta_bytes and args.max_hybrid_bytes:
        parser.error("--clip-delta-bytes and --max-hybrid-bytes are mutually exclusive")
    if args.format in ("tap", "both") and not args.loop:
        parser.error("the current TAP player is looping; --no-loop is cartridge-only")
    if args.format in ("tap", "both") and args.loop_transition == "keyframe":
        parser.error("--loop-transition keyframe is currently cartridge-only")
    if args.fifo_packing and args.transport != "hybrid":
        parser.error("--fifo-packing currently requires --transport hybrid")
    if args.bounce and not args.loop:
        parser.error("--bounce requires looping playback")
    if args.bounce and args.loop_transition != "delta":
        parser.error("--bounce uses reversible deltas and requires --loop-transition delta")

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
        "--temporal-attr-penalty", args.temporal_attr_penalty,
        "--temporal-pixel-penalty", args.temporal_pixel_penalty,
        "--max-hybrid-bytes", args.max_hybrid_bytes,
    ]
    if args.source_window:
        encoder_args += ["--source-window", args.source_window]
    elif args.source_window_pixels:
        encoder_args += ["--source-window-pixels", args.source_window_pixels]
    if args.clip_delta_bytes:
        encoder_args += ["--clip-delta-bytes", args.clip_delta_bytes,
                         "--clip-min-frame-bytes", args.clip_min_frame_bytes,
                         "--clip-max-frame-bytes", args.clip_max_frame_bytes]
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
        if args.bounce:
            cartridge_args.append("--bounce")
        elif args.loop and args.loop_transition == "delta":
            cartridge_args += ["--seamless-loop", "--loop-pause-frames",
                               args.loop_pause_frames]
        elif not args.loop:
            cartridge_args.append("--stop-at-end")
        transport_flag = {
            "hybrid": None,
            "paired": "--paired-cell-updates",
            "row-hybrid": "--row-hybrid-updates",
            "raster": "--raster-updates",
        }[args.transport]
        if transport_flag:
            cartridge_args.append(transport_flag)
        if args.fifo_packing:
            cartridge_args.append("--fifo-packing")
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
        if args.bounce:
            tap_args.append("--bounce")
        if args.pasmo:
            tap_args += ["--pasmo", args.pasmo]
        run("src/player/build_video_tap.py", *tap_args)
        artifacts["tap"] = str(output / "tap" / "svd_video.tap")

    manifest = {
        "source": str(source), "format": args.format,
        "fps_num": rate.numerator, "fps_den": rate.denominator,
        "max_frames": args.max_frames, "start_seconds": args.start_seconds,
        "bounce": args.bounce,
        "geometry": args.geometry, "encoder": args.encoder, "auto": args.auto,
        "source_window": args.source_window,
        "source_window_pixels": args.source_window_pixels,
        "max_hybrid_bytes": args.max_hybrid_bytes,
        "temporal_attr_penalty": args.temporal_attr_penalty,
        "temporal_pixel_penalty": args.temporal_pixel_penalty,
        "clip_delta_bytes": args.clip_delta_bytes,
        "keyframe_codec": args.keyframe_codec,
        "transport": args.transport if args.format != "tap" else "tap-raster",
        "fifo_packing": args.fifo_packing,
        "loop": args.loop, "loop_transition": args.loop_transition,
        "artifacts": artifacts,
    }
    (output / "build.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("completed TSVideoCodec build")
    for kind, path in artifacts.items():
        print(f"{kind}: {path}")


if __name__ == "__main__":
    main()
