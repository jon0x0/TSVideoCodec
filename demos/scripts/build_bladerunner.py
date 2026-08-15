#!/usr/bin/env python3
"""Build bladerunner.gif as a repeating 64K cartridge with a hard-cut restart."""

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
                        default=ROOT / "video" / "bladerunner.gif")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "bladerunner")
    parser.add_argument("--max-hybrid-bytes", type=int, default=1400)
    parser.add_argument("--brightness", type=float, default=0.03)
    parser.add_argument("--encoder", choices=("native", "python"), default="native")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve()
    if source != (ROOT / "video" / "bladerunner.gif").resolve():
        raise SystemExit("source must be video/bladerunner.gif; video/old is out of scope")
    args.output.mkdir(parents=True, exist_ok=True)
    sequence = args.output / "sequence"
    stream = args.output / "video.svd"
    cartridge = args.output / "cartridge"

    run("src/encoder/probe_video.py", source, "--output", args.output / "probe.json")
    probe = json.loads((args.output / "probe.json").read_text(encoding="utf-8"))["streams"][0]
    if probe.get("nb_frames") != "50" or probe.get("avg_frame_rate") != "25/1":
        raise SystemExit(f"expected the known 50-frame 25fps GIF, got {probe}")
    run("demos/scripts/analyze_source_transitions.py", source,
        args.output / "source_transitions.csv")
    run("src/encoder/encode_sequence.py", source, sequence,
        "--fps", 12.5, "--max-frames", 26, "--geometry", "fit",
        "--dither-mode", "sierra-lite", "--encoder", args.encoder,
        "--brightness", args.brightness, "--sierra-gamma", 1.3,
        "--temporal-attr-penalty", 0, "--temporal-pixel-penalty", 0,
        "--background-motion-threshold", 0, "--background-penalty-multiplier", 1,
        "--max-hybrid-bytes", args.max_hybrid_bytes, "--keep-source-frames")
    # A nominal 35/3 scheduler plus one missed-tick compensation measures close
    # to the 25/2 source sampling cadence on this clip's decoder workload.
    run("src/encoder/pack_svd.py", sequence, stream,
        "--fps-num", 35, "--fps-den", 3, "--delta-format", "hybrid")
    # Deliberately omit --seamless-loop: the source wrap is a hard cut. Restarting
    # through the keyframe prevents rate-controlled residue from the closing shot.
    run("src/cartridge/build_cartridge.py", sequence, stream, cartridge,
        "--loop-pause-frames", 0, "--decode-tick-compensation", 1)
    if args.validate:
        run("src/cartridge/validate_cartridge.py", cartridge, sequence, "--frame", "last")
        run("src/player/measure_cartridge_decoder.py", cartridge)
        run("src/player/measure_cartridge_cadence.py", cartridge)
    result = {
        "source": str(source), "source_frames": 50, "source_fps": "25/1",
        "frames": 26, "source_sampling_fps": "25/2", "duration_seconds": 2.08,
        "nominal_scheduler_fps": "35/3",
        "encoder_backend": args.encoder, "dither": "Sierra Lite",
        "brightness": args.brightness, "gamma": 1.3,
        "max_hybrid_bytes": args.max_hybrid_bytes,
        "decode_tick_compensation": 1,
        "playback": "continuous repeat with deliberate hard-cut keyframe restart",
        "artifact": str((cartridge / "svd_video_64k.dck").resolve()),
    }
    (args.output / "selection.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"selected cartridge: {result['artifact']}")


if __name__ == "__main__":
    main()
