#!/usr/bin/env python3
"""Build the rotating Earth GIF as a seamless 64K SVD cartridge."""

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
    parser.add_argument("--source", type=Path,
                        default=ROOT / "video" / "Rotating_earth_animated_transparent.gif")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "rotating_earth")
    parser.add_argument("--fps", type=int, default=10)
    parser.add_argument("--max-hybrid-bytes", type=int, default=400)
    parser.add_argument("--brightness", type=float, default=0.04)
    parser.add_argument("--encoder", choices=("native", "python"), default="native")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve()
    expected = (ROOT / "video" / "Rotating_earth_animated_transparent.gif").resolve()
    if source != expected or source.parent != (ROOT / "video").resolve():
        raise SystemExit("source must be video/Rotating_earth_animated_transparent.gif")
    args.output.mkdir(parents=True, exist_ok=True)
    sequence = args.output / "sequence"; stream = args.output / "video.svd"; cartridge = args.output / "cartridge"
    run("src/encoder/probe_video.py", source, "--output", args.output / "probe.json")
    probe = json.loads((args.output / "probe.json").read_text(encoding="utf-8"))["streams"][0]
    if probe.get("nb_frames") != "240" or probe.get("avg_frame_rate") != "25/1":
        raise SystemExit(f"expected the known 240-frame 25fps GIF, got {probe}")
    output_frames = round(9.6 * args.fps)
    run("src/encoder/encode_sequence.py", source, sequence,
        "--fps", args.fps, "--max-frames", output_frames, "--geometry", "fit",
        "--dither-mode", "sierra-lite", "--encoder", args.encoder,
        "--brightness", args.brightness, "--sierra-gamma", 1.3,
        "--temporal-attr-penalty", 0, "--temporal-pixel-penalty", 0,
        "--background-motion-threshold", 0, "--background-penalty-multiplier", 1,
        "--max-hybrid-bytes", args.max_hybrid_bytes, "--cyclic-warmup-passes", 2,
        "--keep-source-frames")
    run("src/encoder/pack_svd.py", sequence, stream, "--fps-num", args.fps,
        "--fps-den", 1, "--delta-format", "hybrid")
    run("src/cartridge/build_cartridge.py", sequence, stream, cartridge,
        "--seamless-loop", "--loop-pause-frames", 0)
    if args.validate:
        run("src/cartridge/validate_cartridge.py", cartridge, sequence, "--frame", "last")
        run("src/player/measure_cartridge_decoder.py", cartridge)
        run("src/player/measure_cartridge_cadence.py", cartridge)
    result = {
        "source": str(source), "source_frames": 240, "source_fps": "25/1",
        "frames": output_frames, "fps": f"{args.fps}/1", "duration_seconds": 9.6,
        "encoder_backend": args.encoder, "dither": "Sierra Lite",
        "brightness": args.brightness, "gamma": 1.3,
        "max_hybrid_bytes": args.max_hybrid_bytes, "cyclic_warmup_passes": 2,
        "playback": "seamless continuous repeat",
        "artifact": str((cartridge / "svd_video_64k.dck").resolve()),
    }
    (args.output / "selection.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"selected cartridge: {result['artifact']}")


if __name__ == "__main__":
    main()
