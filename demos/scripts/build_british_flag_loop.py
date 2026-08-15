#!/usr/bin/env python3
"""Build the complete 40-frame british-flag-2.gif loop as TAP or cartridge."""

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
                        default=ROOT / "video" / "british-flag-2.gif")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "build" / "british_flag_loop")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--encoder", choices=("native", "python"), default="native")
    args = parser.parse_args()
    source = args.source.resolve()
    if source.parent != (ROOT / "video").resolve():
        raise SystemExit("source must be directly inside video/; video/old is out of scope")
    if source.name.lower() != "british-flag-2.gif":
        raise SystemExit("this reproducible profile is for british-flag-2.gif")
    args.output.mkdir(parents=True, exist_ok=True)
    sequence = args.output / "sequence"
    stream = args.output / "video.svd"
    tap_output = args.output / "tap"
    cartridge_output = args.output / "cartridge"

    run("src/encoder/probe_video.py", source, "--output", args.output / "probe.json")
    probe = json.loads((args.output / "probe.json").read_text())
    video = probe["streams"][0]
    if video.get("nb_frames") != "40" or video.get("avg_frame_rate") != "20/1":
        raise SystemExit(f"expected the known 40-frame 20fps GIF, got {video}")
    run("src/encoder/encode_sequence.py", source, sequence,
        "--fps", 20, "--max-frames", 40, "--geometry", "fit",
        "--dither-mode", "sierra-lite", "--encoder", args.encoder, "--brightness", -0.02,
        "--sierra-gamma", 1.3, "--temporal-attr-penalty", 0.08,
        "--temporal-pixel-penalty", 0.08, "--background-motion-threshold", 8,
        "--background-penalty-multiplier", 4, "--max-hybrid-bytes", 700,
        "--cyclic-warmup-passes", 3,
        "--keep-source-frames")
    run("src/encoder/pack_svd.py", sequence, stream, "--fps-num", 20,
        "--fps-den", 1, "--delta-format", "hybrid")

    selected = "tap"
    fallback_reason = None
    try:
        run("src/player/build_video_tap.py", sequence, tap_output,
            "--fps-num", 20, "--fps-den", 1)
    except subprocess.CalledProcessError as error:
        selected = "cartridge"
        fallback_reason = f"raster TAP build did not fit safe contiguous RAM (exit {error.returncode})"
        run("src/cartridge/build_cartridge.py", sequence, stream, cartridge_output,
            "--seamless-loop", "--loop-pause-frames", 0)

    if args.validate:
        if selected == "tap":
            run("src/player/validate_video_tap.py", tap_output, sequence)
            run("src/player/measure_tap_cadence.py", tap_output)
        else:
            run("src/cartridge/validate_cartridge.py", cartridge_output, sequence, "--frame", "last")
            run("src/player/measure_cartridge_decoder.py", cartridge_output)
    result = {
        "source": str(source), "source_frames": 40, "source_fps": "20/1",
        "loop_seconds": 2.0, "geometry": "scale fit 256x192 with letterbox",
        "dither": "Sierra Lite", "brightness": -0.02, "gamma": 1.3,
        "temporal_attr_penalty": 0.08, "temporal_pixel_penalty": 0.08,
        "max_hybrid_bytes": 700, "cyclic_warmup_passes": 3,
        "encoder_backend": args.encoder,
        "selected_transport": selected, "fallback_reason": fallback_reason,
        "artifact": str((tap_output / "svd_video.tap") if selected == "tap" else
                        (cartridge_output / "svd_video_64k.dck")),
    }
    (args.output / "selection.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"selected {selected}: {result['artifact']}")


if __name__ == "__main__":
    main()
