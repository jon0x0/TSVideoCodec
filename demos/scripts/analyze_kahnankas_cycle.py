#!/usr/bin/env python3
"""Extract and measure every 50 Hz frame in one 1.1-second loop."""

from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).parents[2]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, default=ROOT / "video" / "Kahnankas.mp4")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "kahnankas_cycle_55")
    args = parser.parse_args()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg was not found on PATH")
    args.output.mkdir(parents=True, exist_ok=True)
    for path in args.output.glob("source_*.png"):
        path.unlink()
    subprocess.run([
        ffmpeg, "-v", "error", "-i", str(args.video), "-an", "-t", "1.1",
        "-vf", "fps=50", "-frames:v", "55", str(args.output / "source_%02d.png")], check=True)
    paths = sorted(args.output.glob("source_*.png"))
    arrays = [np.asarray(Image.open(path).convert("RGB"), dtype=np.float32) for path in paths]
    rows = []
    for index, current in enumerate(arrays):
        previous = arrays[index - 1] if index else arrays[-1]
        difference = current - previous
        rows.append({
            "index": index,
            "time_seconds": f"{index / 50:.3f}",
            "mean_absolute_change": f"{np.mean(np.abs(difference)):.6f}",
            "rgb_mse": f"{np.mean(difference * difference):.6f}",
            "file": paths[index].name,
        })
    with (args.output / "changes.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    ranked = sorted(rows[1:], key=lambda row: float(row["rgb_mse"]), reverse=True)
    print("largest consecutive changes:")
    for row in ranked[:20]:
        print(row)

    # A 13-frame output grid cannot align exactly with the 50 Hz source because
    # 55/13 is fractional.  Test every 1 ms phase within one output interval
    # and score the resulting cyclic sequence by its weakest adjacent change.
    # This keeps output presentation times uniform; only the sampling phase is
    # selected to avoid two neighboring samples falling in a source hold.
    interval = 1.1 / 13
    candidates = []
    for phase_ms in range(round(interval * 1000)):
        phase = phase_ms / 1000
        indices = [round(((phase + index * interval) % 1.1) * 50) % len(arrays)
                   for index in range(13)]
        changes = []
        for index, current_index in enumerate(indices):
            previous_index = indices[index - 1]
            difference = arrays[current_index] - arrays[previous_index]
            changes.append(float(np.mean(difference * difference)))
        candidates.append({
            "phase_ms": phase_ms,
            "indices": indices,
            "minimum_adjacent_mse": min(changes),
            "mean_adjacent_mse": sum(changes) / len(changes),
            "adjacent_mse": changes,
        })
    candidates.sort(key=lambda item: (item["minimum_adjacent_mse"],
                                      item["mean_adjacent_mse"]), reverse=True)
    print("best uniform-grid phases:")
    for candidate in candidates[:5]:
        print(candidate)
    print(f"wrote {len(paths)} frames and {args.output / 'changes.csv'}")


if __name__ == "__main__":
    main()
