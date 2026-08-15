#!/usr/bin/env python3
"""Record reproducible ffprobe metadata for an input video."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise SystemExit("ffprobe was not found on PATH")
    result = subprocess.run([
        ffprobe, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate,nb_frames,duration:format=duration",
        "-of", "json", str(args.input)], check=True, capture_output=True, text=True)
    metadata = json.loads(result.stdout)
    metadata["source"] = str(args.input.resolve())
    output = args.output or Path("build") / "video_probe.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata, indent=2))
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
