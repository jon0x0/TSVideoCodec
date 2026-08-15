#!/usr/bin/env python3
"""Reproducibly compare tone compensation and ordered-dither settings."""

from __future__ import annotations

import argparse
import csv
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from encode_sequence import extract_frames
from svd_ecm import encode_image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--start-seconds", type=float, default=3.0)
    parser.add_argument("--geometry", choices=("fit", "crop"), default="fit")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    variants = [
        (1.0, 0.0, 1.0),
        (0.8, 0.5, 1.0),
        (0.7, 0.5, 1.0),
        (0.6, 0.5, 1.0),
        (0.8, 0.5, 0.75),
        (0.7, 0.5, 0.75),
        (0.6, 0.5, 0.75),
        (0.7, 0.25, 0.75),
        (0.7, 0.75, 0.75),
    ]

    with tempfile.TemporaryDirectory(prefix="svd_quality_") as temporary:
        source_path = extract_frames(
            args.video, Path(temporary), 1.0, 1, args.start_seconds, args.geometry
        )[0]
        source = Image.open(source_path).convert("RGB")
        source.save(args.output / "source.png")
        panels = [source]
        labels = ["source"]
        rows = []
        source_rgb = np.asarray(source, dtype=np.float32)
        for gamma, dither, chroma in variants:
            frame = encode_image(
                source, source_gamma=gamma, dither_strength=dither, chroma_weight=chroma
            )
            preview = frame.render()
            name = f"g{gamma:g}_d{dither:g}_c{chroma:g}"
            preview.save(args.output / f"{name}.png")
            rgb = np.asarray(preview, dtype=np.float32)
            rows.append({
                "gamma": gamma,
                "dither_strength": dither,
                "chroma_weight": chroma,
                "rgb_mse": float(np.mean((source_rgb - rgb) ** 2)),
                "black_fraction": float(np.mean(np.all(rgb == 0, axis=2))),
                "mean_luma": float(np.mean(rgb @ np.array([0.299, 0.587, 0.114]))),
            })
            panels.append(preview)
            labels.append(name)

    sheet = Image.new("RGB", (256 * 3, (192 + 18) * 4), "white")
    draw = ImageDraw.Draw(sheet)
    for index, (panel, label) in enumerate(zip(panels, labels)):
        x = (index % 3) * 256
        y = (index // 3) * 210
        sheet.paste(panel, (x, y + 18))
        draw.text((x + 3, y + 3), label, fill="black")
    sheet.save(args.output / "comparison.png")
    with (args.output / "metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {args.output / 'comparison.png'}")
    print(f"wrote {args.output / 'metrics.csv'}")


if __name__ == "__main__":
    main()
