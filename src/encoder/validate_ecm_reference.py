#!/usr/bin/env python3
"""Round-trip a known ECM TAP through the optimizer and compare its pixels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from import_ecm_tap import import_frame
from svd_ecm import encode_image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tap", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    reference = import_frame(args.tap)
    source = reference.render()
    reconstructed = encode_image(
        source, source_gamma=1.0, dither_strength=0.0, chroma_weight=1.0
    )
    result = reconstructed.render()
    source.save(args.output / "reference.png")
    result.save(args.output / "reencoded.png")
    before = np.asarray(source, dtype=np.int16)
    after = np.asarray(result, dtype=np.int16)
    report = {
        "tap": str(args.tap.resolve()),
        "rgb_mse": float(np.mean((before - after) ** 2)),
        "different_pixels": int(np.count_nonzero(np.any(before != after, axis=2))),
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if report["different_pixels"]:
        raise SystemExit("ECM reference did not round-trip pixel-perfectly")


if __name__ == "__main__":
    main()
