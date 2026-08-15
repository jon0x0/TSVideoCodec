#!/usr/bin/env python3
"""Build 2049lonely.gif as a native-encoded repeating 64K cartridge."""

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
    parser.add_argument("--source", type=Path, default=ROOT / "video" / "2049lonely.gif")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "2049lonely")
    parser.add_argument("--encoder", choices=("native", "python"), default="native")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--max-hybrid-bytes", type=int, default=980)
    parser.add_argument("--decode-tick-compensation", type=int, default=1)
    parser.add_argument("--row-hybrid-updates", action="store_true")
    parser.add_argument("--stop-at-end", action="store_true",
                        help="optional non-looping variant; default is seamless repeat")
    args = parser.parse_args()
    source = args.source.resolve()
    if source.parent != (ROOT / "video").resolve() or source.name.lower() != "2049lonely.gif":
        raise SystemExit("source must be video/2049lonely.gif; video/old is out of scope")
    sequence = args.output / "sequence"; stream = args.output / "video.svd"; cartridge = args.output / "cartridge"
    args.output.mkdir(parents=True, exist_ok=True)
    run("src/encoder/probe_video.py", source, "--output", args.output / "probe.json")
    probe = json.loads((args.output / "probe.json").read_text(encoding="utf-8"))["streams"][0]
    if probe.get("nb_frames") != "36" or probe.get("avg_frame_rate") != "20/1":
        raise SystemExit(f"expected the known 36-frame 20fps GIF, got {probe}")
    output_frames = round(1.8 * args.fps)
    run("src/encoder/encode_sequence.py", source, sequence,
        "--fps", args.fps, "--max-frames", output_frames, "--geometry", "fit",
        "--dither-mode", "sierra-lite", "--encoder", args.encoder,
        "--brightness", -0.02, "--sierra-gamma", 1.3,
        "--temporal-attr-penalty", 0, "--temporal-pixel-penalty", 0,
        "--background-motion-threshold", 0, "--background-penalty-multiplier", 1,
        "--max-hybrid-bytes", args.max_hybrid_bytes, "--max-cell-age", 1,
        "--cell-age-bonus", 500000,
        "--keep-source-frames")
    run("src/encoder/pack_svd.py", sequence, stream, "--fps-num", args.fps,
        "--fps-den", 1, "--delta-format", "hybrid")
    cartridge_options = ["--decode-tick-compensation", args.decode_tick_compensation]
    if args.row_hybrid_updates:
        cartridge_options.append("--row-hybrid-updates")
    cartridge_options += (["--stop-at-end"] if args.stop_at_end else
                          ["--seamless-loop", "--loop-pause-frames", 0])
    run("src/cartridge/build_cartridge.py", sequence, stream, cartridge, *cartridge_options)
    if args.validate:
        run("src/cartridge/validate_cartridge.py", cartridge, sequence, "--frame", "last")
        run("src/player/measure_cartridge_decoder.py", cartridge)
    result = {
        "source": str(source), "source_frames": 36, "frames": output_frames,
        "fps": f"{args.fps}/1", "duration_seconds": 1.8,
        "encoder_backend": args.encoder, "dither": "Sierra Lite",
        "max_hybrid_bytes": args.max_hybrid_bytes,
        "max_cell_age": 1,
        "cell_age_bonus": 500000,
        "decode_tick_compensation": args.decode_tick_compensation,
        "row_hybrid_updates": args.row_hybrid_updates,
        "temporal_attr_penalty": 0, "temporal_pixel_penalty": 0,
        "cyclic_warmup_passes": 0,
        "playback": ("non-looping; final frame held indefinitely" if args.stop_at_end
                     else "seamless continuous repeat"),
        "artifact": str((cartridge / "svd_video_64k.dck").resolve()),
    }
    (args.output / "selection.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"selected cartridge: {result['artifact']}")


if __name__ == "__main__":
    main()
