#!/usr/bin/env python3
"""Explain generated source-cell distances to auto-detected flat regions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "encoder"))
from auto_profile import _cell_mean, _cell_variance, adjust  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sequence", type=Path)
    parser.add_argument("--y", type=int, required=True)
    parser.add_argument("--xb", type=int, required=True)
    args = parser.parse_args()
    report = json.loads((args.sequence / "auto_analysis.json").read_text())
    tone = report["tone"]
    sources = sorted(args.sequence.glob("frame_*_source.png"))
    frames = np.stack([adjust(np.asarray(Image.open(path).convert("RGB")),
                                     tone["brightness"], tone["contrast"],
                                     tone["saturation"], tone["gamma"])
                       for path in sources])
    means = _cell_mean(frames)[:, args.y, args.xb]
    variances = np.stack([_cell_variance(frame) for frame in frames])[:, args.y, args.xb]
    print(f"cell {args.y}:{args.xb}")
    for index, region in enumerate(report["completed_regions"]):
        y0, x0, y1, x1 = region["bounds"]
        if y0 <= args.y <= y1 and x0 <= args.xb <= x1:
            colour = np.asarray(region["source_linear_rgb"])
            distances = np.mean((means - colour) ** 2, axis=1)
            print(f"region {index} bounds={region['bounds']} colour={colour.tolist()}")
            print("distances=" + ",".join(f"{value:.8f}" for value in distances))
            print("variances=" + ",".join(f"{value:.8f}" for value in variances))
    print(f"colour threshold={max(0.0025, report['background_match_threshold'] * 2.0):.8f}")


if __name__ == "__main__":
    main()
