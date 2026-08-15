#!/usr/bin/env python3
"""Export the 13 ordered source frames from one 1.1-second Kahnankas loop."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[2]
FRAME_COUNT = 13
LOOP_NUMERATOR = 11
LOOP_DENOMINATOR = 10  # 1.1 seconds
FPS_NUMERATOR = 130
FPS_DENOMINATOR = 11
SOURCE_FPS = 50
SAMPLE_PHASE_SECONDS = 0.063


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, default=ROOT / "video" / "Kahnankas.mp4")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "build" / "kahnankas_13frame_source")
    args = parser.parse_args()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg was not found on PATH")
    args.output.mkdir(parents=True, exist_ok=True)
    for path in args.output.glob("frame_*.png"):
        path.unlink()
    source_dir = args.output / "source_50hz"
    source_dir.mkdir(exist_ok=True)
    for path in source_dir.glob("source_*.png"):
        path.unlink()
    subprocess.run([
        ffmpeg, "-v", "error", "-i", str(args.video), "-an", "-t", "1.1",
        "-vf", f"fps={SOURCE_FPS}", "-frames:v", "55",
        str(source_dir / "source_%02d.png")], check=True)
    source_frames = sorted(source_dir.glob("source_*.png"))
    source_indices = [round(((SAMPLE_PHASE_SECONDS + index * LOOP_NUMERATOR /
                              LOOP_DENOMINATOR / FRAME_COUNT) %
                             (LOOP_NUMERATOR / LOOP_DENOMINATOR)) * SOURCE_FPS) % 55
                      for index in range(FRAME_COUNT)]
    for output_index, source_index in enumerate(source_indices, 1):
        shutil.copy2(source_frames[source_index], args.output / f"frame_{output_index:02d}.png")
    frames = sorted(args.output.glob("frame_*.png"))
    if len(frames) != FRAME_COUNT:
        raise SystemExit(f"expected {FRAME_COUNT} frames, extracted {len(frames)}")
    selected_video = args.output / "selected_loop.mkv"
    subprocess.run([
        ffmpeg, "-y", "-v", "error", "-framerate",
        f"{FPS_NUMERATOR}/{FPS_DENOMINATOR}", "-i", str(args.output / "frame_%02d.png"),
        "-frames:v", str(FRAME_COUNT), "-c:v", "ffv1", "-pix_fmt", "rgb24",
        str(selected_video),
    ], check=True)
    manifest = {
        "source": str(args.video.resolve()),
        "loop_seconds": LOOP_NUMERATOR / LOOP_DENOMINATOR,
        "frame_count": FRAME_COUNT,
        "frame_rate": f"{FPS_NUMERATOR}/{FPS_DENOMINATOR}",
        "frame_interval_seconds": FPS_DENOMINATOR / FPS_NUMERATOR,
        "sample_phase_seconds": SAMPLE_PHASE_SECONDS,
        "sample_times_seconds": [round((SAMPLE_PHASE_SECONDS + index * FPS_DENOMINATOR /
                                         FPS_NUMERATOR) % 1.1, 6)
                                 for index in range(FRAME_COUNT)],
        "source_50hz_indices_zero_based": source_indices,
        "frames": [path.name for path in frames],
        "lossless_selected_video": selected_video.name,
    }
    (args.output / "frames.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"exported {len(frames)} ordered frames to {args.output}")
    print(f"wrote {args.output / 'frames.json'}")


if __name__ == "__main__":
    main()
