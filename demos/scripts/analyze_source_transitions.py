#!/usr/bin/env python3
"""Extract source frames and report adjacent/loop transition magnitudes."""

from __future__ import annotations

import argparse
import csv
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    video = Path(__file__).parents[2] / "video"
    if source.parent != video.resolve() or source.parent.name.lower() == "old":
        raise SystemExit("source must be a media file directly inside video/")
    args.output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="svd_transitions_") as temporary:
        pattern = Path(temporary) / "frame_%05d.png"
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(source),
                        "-vsync", "0", str(pattern)], check=True)
        paths = sorted(Path(temporary).glob("frame_*.png"))
        frames = [np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
                  for path in paths]

    rows = []
    for index, current in enumerate(frames):
        previous_index = (index - 1) % len(frames)
        difference = current - frames[previous_index]
        rows.append({
            "to_frame": index,
            "from_frame": previous_index,
            "rgb_mse": f"{np.mean(difference * difference):.6f}",
            "mean_absolute_change": f"{np.mean(np.abs(difference)):.6f}",
            "changed_pixel_fraction": f"{np.mean(np.any(difference != 0, axis=2)):.6f}",
        })
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    for row in sorted(rows, key=lambda item: float(item["rgb_mse"]), reverse=True)[:10]:
        print(row)
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
