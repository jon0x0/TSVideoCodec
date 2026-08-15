#!/usr/bin/env python3
"""Measure horizontal reconstruction artifacts in a reproducible image region."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
from PIL import Image


def longest_run(mask: np.ndarray) -> int:
    best = current = 0
    for value in mask:
        current = current + 1 if value else 0
        best = max(best, current)
    return best


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sequence", type=Path)
    parser.add_argument("--x", type=int, required=True)
    parser.add_argument("--y", type=int, required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    previews = sorted(args.sequence.glob("frame_*_preview.png"))
    if not previews:
        raise SystemExit("sequence has no reconstructed preview frames")
    rows = []
    for preview_path in previews:
        source_path = preview_path.with_name(preview_path.name.replace("_preview", "_source"))
        if not source_path.exists():
            raise SystemExit(f"missing source frame {source_path}")
        preview = np.asarray(Image.open(preview_path).convert("RGB"), dtype=np.float32)
        source = np.asarray(Image.open(source_path).convert("RGB"), dtype=np.float32)
        x0, y0 = args.x, args.y
        x1, y1 = x0 + args.width, y0 + args.height
        if x0 < 0 or y0 < 0 or x1 > preview.shape[1] or y1 > preview.shape[0]:
            raise SystemExit("analysis region is outside the frame")
        p = preview[y0:y1, x0:x1]
        s = source[y0:y1, x0:x1]
        squared = np.mean((p - s) ** 2, axis=2)
        # A suspect gray replacement is locally achromatic in the reconstruction,
        # differs materially from its source pixel, and extends horizontally.
        achromatic = (np.max(p, axis=2) - np.min(p, axis=2)) <= 4
        source_chroma = np.max(s, axis=2) - np.min(s, axis=2)
        source_luma = s @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
        source_subject = (source_luma <= 130) & (source_chroma >= 8)
        suspect = achromatic & source_subject & (squared >= 900)
        for local_y in range(args.height):
            rows.append({
                "frame": preview_path.stem.split("_")[1],
                "y": y0 + local_y,
                "row_mse": float(np.mean(squared[local_y])),
                "suspect_gray_pixels": int(np.count_nonzero(suspect[local_y])),
                "longest_suspect_run": longest_run(suspect[local_y]),
            })

    ranked = sorted(rows, key=lambda row: (row["longest_suspect_run"], row["row_mse"]),
                    reverse=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
            writer.writeheader(); writer.writerows(rows)
    for row in ranked[:20]:
        print(f"frame={row['frame']} y={row['y']} run={row['longest_suspect_run']} "
              f"gray={row['suspect_gray_pixels']} mse={row['row_mse']:.1f}")


if __name__ == "__main__":
    main()
