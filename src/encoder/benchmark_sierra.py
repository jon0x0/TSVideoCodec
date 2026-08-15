#!/usr/bin/env python3
"""Sweep practical Sierra Lite source adjustments for one video frame."""

from __future__ import annotations

import argparse
import csv
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from encode_sequence import extract_frames
from svd_ecm import encode_image_sierra_lite


CONFIGS = [
    (-0.04, 0.90, 1.15),
    (-0.02, 0.90, 1.15),
    (0.00, 0.90, 1.15),
    (-0.04, 1.00, 1.15),
    (-0.02, 1.00, 1.15),
    (0.00, 1.00, 1.15),
    (-0.02, 1.00, 1.30),
    (0.00, 1.00, 1.30),
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--start-seconds", type=float, default=3.0)
    parser.add_argument("--geometry", choices=("fit", "crop"), default="crop")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="svd_sierra_") as temporary:
        path = extract_frames(
            args.video, Path(temporary), 1, 1, args.start_seconds, args.geometry
        )[0]
        source = Image.open(path).convert("RGB")
        source.load()
    source.save(args.output / "source.png")
    panels = [("source", source)]
    rows = []
    source_rgb = np.asarray(source, dtype=np.float32)
    for brightness, saturation, gamma in CONFIGS:
        frame = encode_image_sierra_lite(
            source, brightness=brightness, saturation=saturation, gamma=gamma
        )
        preview = frame.render()
        label = f"b{brightness:+.2f}_s{saturation:.2f}_g{gamma:.2f}"
        preview.save(args.output / f"{label}.png")
        rendered = np.asarray(preview, dtype=np.float32)
        rows.append({
            "brightness": brightness, "saturation": saturation, "gamma": gamma,
            "rgb_mse": float(np.mean((source_rgb - rendered) ** 2)),
            "black_fraction": float(np.mean(np.all(rendered == 0, axis=2))),
        })
        panels.append((label, preview))
    sheet = Image.new("RGB", (768, 630), "white")
    draw = ImageDraw.Draw(sheet)
    for index, (label, panel) in enumerate(panels):
        x, y = (index % 3) * 256, (index // 3) * 210
        draw.text((x + 3, y + 3), label, fill="black")
        sheet.paste(panel, (x, y + 18))
    sheet.save(args.output / "comparison.png")
    with (args.output / "metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {args.output / 'comparison.png'}")


if __name__ == "__main__":
    main()
