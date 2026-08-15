#!/usr/bin/env python3
"""Build juggler.gif as a seamless 64K SVD cartridge."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]


def run(*args: object) -> None:
    subprocess.run([sys.executable, *(str(arg) for arg in args)], cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "video" / "juggler.gif")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "juggler")
    parser.add_argument("--max-hybrid-bytes", type=int, default=1400)
    parser.add_argument("--decode-tick-compensation", type=int, choices=(0, 1), default=1)
    parser.add_argument("--brightness", type=float, default=0.0)
    parser.add_argument("--encoder", choices=("native", "python"), default="native")
    parser.add_argument("--auto-colour-policy", "--auto-color-policy",
                        choices=("faithful", "quiet"), default="faithful")
    parser.add_argument("--auto-plate-encoder",
                          choices=("sierra-structure", "sierra-texture", "sierra-hybrid", "sierra", "ordered"),
                          default="ordered")
    parser.add_argument("--auto-material-dither",
                        choices=("sierra-line", "shell-aware", "ordered-bayer", "solid-dark"),
                        default="sierra-line")
    parser.add_argument("--solid-blue-sky", action="store_true",
                        help="use solid bright blue for stable upper background")
    parser.add_argument("--light-blue-sky", action="store_true",
                        help="use stable bright cyan/white dither for a light-blue sky")
    parser.add_argument("--raster-updates", action="store_true",
                        help="pair bitmap/attribute writes in visible raster order")
    parser.add_argument("--row-hybrid-updates", action="store_true",
                        help="decode bitmap and attributes together one raster row at a time")
    parser.add_argument("--paired-cell-updates", action="store_true",
                        help="replace each changed bitmap/colour cell together in raster order")
    parser.add_argument("--reverse-paired-cell-updates", action="store_true",
                        help="replace paired bitmap/colour cells from bottom to top")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    if args.solid_blue_sky and args.light_blue_sky:
        raise SystemExit("sky colour options are mutually exclusive")
    if sum((args.raster_updates, args.row_hybrid_updates, args.paired_cell_updates,
            args.reverse_paired_cell_updates)) > 1:
        raise SystemExit("update transport options are mutually exclusive")
    source = args.source.resolve()
    if source != (ROOT / "video" / "juggler.gif").resolve():
        raise SystemExit("source must be video/juggler.gif; video/old is out of scope")
    args.output.mkdir(parents=True, exist_ok=True)
    sequence = args.output / "sequence"; stream = args.output / "video.svd"; cartridge = args.output / "cartridge"
    run("src/encoder/probe_video.py", source, "--output", args.output / "probe.json")
    probe = json.loads((args.output / "probe.json").read_text(encoding="utf-8"))["streams"][0]
    if probe.get("nb_frames") != "12" or probe.get("avg_frame_rate") != "50/3":
        raise SystemExit(f"expected the known 12-frame 50/3fps GIF, got {probe}")
    run("src/encoder/encode_sequence.py", source, sequence,
        "--fps", 50 / 3, "--max-frames", 12, "--geometry", "fit",
        "--dither-mode", "sierra-lite", "--encoder", args.encoder,
        "--auto", "--auto-colour-policy", args.auto_colour_policy,
        "--auto-plate-encoder", args.auto_plate_encoder,
        "--auto-material-dither", args.auto_material_dither,
        *( ["--auto-solid-upper-background",
            "light-blue" if args.light_blue_sky else "blue"]
           if (args.solid_blue_sky or args.light_blue_sky) else [] ),
        "--brightness", args.brightness, "--sierra-gamma", 1.3,
        "--temporal-attr-penalty", 0, "--temporal-pixel-penalty", 0,
        "--background-motion-threshold", 0, "--background-penalty-multiplier", 1,
        "--max-hybrid-bytes", args.max_hybrid_bytes, "--cyclic-warmup-passes", 2,
        "--keep-source-frames")
    run("src/encoder/pack_svd.py", sequence, stream, "--fps-num", 50,
        "--fps-den", 3, "--delta-format", "hybrid")
    cartridge_options = ["--seamless-loop", "--loop-pause-frames", 0,
                         "--decode-tick-compensation", args.decode_tick_compensation]
    if args.raster_updates:
        cartridge_options.append("--raster-updates")
    if args.row_hybrid_updates:
        cartridge_options.append("--row-hybrid-updates")
    if args.paired_cell_updates:
        cartridge_options.append("--paired-cell-updates")
    if args.reverse_paired_cell_updates:
        cartridge_options.append("--reverse-paired-cell-updates")
    run("src/cartridge/build_cartridge.py", sequence, stream, cartridge, *cartridge_options)
    if args.validate:
        run("src/cartridge/validate_cartridge.py", cartridge, sequence, "--frame", "last")
        run("src/player/measure_cartridge_decoder.py", cartridge)
        run("src/player/measure_cartridge_cadence.py", cartridge)
    result = {
        "source": str(source), "frames": 12, "fps": "50/3", "duration_seconds": 0.72,
        "encoder_backend": args.encoder, "dither": "Sierra Lite", "brightness": args.brightness,
        "max_hybrid_bytes": args.max_hybrid_bytes, "cyclic_warmup_passes": 2,
        "auto": True, "auto_colour_policy": args.auto_colour_policy,
        "auto_plate_encoder": args.auto_plate_encoder,
        "auto_material_dither": args.auto_material_dither,
        "solid_blue_sky": args.solid_blue_sky,
        "light_blue_sky": args.light_blue_sky,
        "raster_updates": args.raster_updates,
        "row_hybrid_updates": args.row_hybrid_updates,
        "paired_cell_updates": args.paired_cell_updates,
        "reverse_paired_cell_updates": args.reverse_paired_cell_updates,
        "auto_analysis": str((sequence / "auto_analysis.json").resolve()),
        "decode_tick_compensation": args.decode_tick_compensation,
        "playback": "seamless continuous repeat",
        "artifact": str((cartridge / "svd_video_64k.dck").resolve()),
    }
    (args.output / "selection.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"selected cartridge: {result['artifact']}")


if __name__ == "__main__":
    main()
