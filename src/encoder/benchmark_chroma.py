#!/usr/bin/env python3
"""Scripted chroma-weight sweep for diagnosing ECM color behavior."""

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
    parser.add_argument("--weights", type=float, nargs="+", default=[1.0, 2.0, 3.0, 4.0, 6.0])
    parser.add_argument("--start-seconds", type=float, default=0.0)
    parser.add_argument("--source-gamma", type=float, default=0.5)
    parser.add_argument("--geometry", choices=("fit", "crop"), default="fit")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    with tempfile.TemporaryDirectory(prefix="svd_chroma_") as temporary:
        source_path = extract_frames(
            args.video, Path(temporary), 1.0, 1, args.start_seconds, args.geometry
        )[0]
        source = Image.open(source_path).convert("RGB")
        source.save(args.output / "source.png")
        source_rgb = np.asarray(source, dtype=np.float32)
        saturation = source_rgb.max(axis=2) - source_rgb.min(axis=2)
        percentiles = np.percentile(saturation, [25, 50, 75, 90, 95])
        print("source RGB-range percentiles p25/p50/p75/p90/p95: " + ", ".join(f"{value:.1f}" for value in percentiles))
        panels = []
        for weight in args.weights:
            frame = encode_image(source, chroma_weight=weight, source_gamma=args.source_gamma)
            preview = frame.render()
            label = "adaptive" if weight is None else f"chroma_{weight:g}".replace(".", "_")
            preview.save(args.output / f"{label}.png")
            panels.append((label, preview))
            rendered = np.asarray(preview, dtype=np.float32)
            mse = float(np.mean((source_rgb - rendered) ** 2))
            # Mean channel range is a simple diagnostic for false-color energy.
            color_range = float(np.mean(rendered.max(axis=2) - rendered.min(axis=2)))
            rows.append({"chroma_weight": "adaptive" if weight is None else weight, "rgb_mse": round(mse, 3), "mean_channel_range": round(color_range, 3)})
        sheet = Image.new("RGB", (256 * 3, 210 * 2), "white")
        draw = ImageDraw.Draw(sheet)
        for index, (label, preview) in enumerate(panels):
            x, y = (index % 3) * 256, (index // 3) * 210
            draw.text((x + 3, y + 3), label, fill="black")
            sheet.paste(preview, (x, y + 18))
        sheet.save(args.output / "comparison.png")
    with (args.output / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {args.output / 'summary.csv'}")


if __name__ == "__main__":
    main()
