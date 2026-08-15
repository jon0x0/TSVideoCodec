#!/usr/bin/env python3
"""Measure temporal Sierra hysteresis against quality and changed bytes."""

from __future__ import annotations

import argparse
import csv
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from encode_sequence import change_statistics, extract_frames
from svd_ecm import encode_image_sierra_lite

CONFIGS = [(0, 0), (0.005, 0.005), (0.01, 0.01), (0.03, 0.03), (0.08, 0.08),
           (0.15, 0.15), (0.3, 0.3)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--start-seconds", type=float, default=3.0)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="svd_temporal_") as temporary:
        paths = extract_frames(args.video, Path(temporary), 12, 3, args.start_seconds, "crop")
        sources = []
        for path in paths:
            image = Image.open(path).convert("RGB")
            image.load()
            sources.append(image)
    rows = []
    for attr_penalty, pixel_penalty in CONFIGS:
        previous = None
        changed = []
        mses = []
        for source in sources:
            frame = encode_image_sierra_lite(
                source, brightness=-0.02, gamma=1.3, previous=previous,
                temporal_attr_penalty=attr_penalty,
                temporal_pixel_penalty=pixel_penalty,
            )
            if previous is not None:
                changed.append(change_statistics(previous, frame)["changed_plane_bytes"])
            mses.append(float(np.mean((np.asarray(source, dtype=np.float32) -
                                       np.asarray(frame.render(), dtype=np.float32)) ** 2)))
            previous = frame
        rows.append({
            "attr_penalty": attr_penalty, "pixel_penalty": pixel_penalty,
            "mean_changed_plane_bytes": sum(changed) / len(changed),
            "mean_rgb_mse": sum(mses) / len(mses),
        })
    with (args.output / "metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {args.output / 'metrics.csv'}")


if __name__ == "__main__":
    main()
