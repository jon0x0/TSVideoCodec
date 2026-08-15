#!/usr/bin/env python3
"""Measure source and reconstructed cyclic frame transitions reproducibly."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "src" / "encoder"))

from svd_ecm import ECMFrame  # noqa: E402
from svd_stream import encode_hybrid  # noqa: E402


def mse(first: Path, second: Path) -> float:
    a = np.asarray(Image.open(first).convert("RGB"), dtype=np.float32)
    b = np.asarray(Image.open(second).convert("RGB"), dtype=np.float32)
    if a.shape != b.shape:
        raise ValueError(f"image dimensions differ: {first} and {second}")
    return float(np.mean((a - b) ** 2))


def transitions(paths: list[Path]) -> list[float]:
    return [mse(paths[index - 1], paths[index]) for index in range(len(paths))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sequence", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = sorted(args.sequence.glob("frame_*_source.png"))
    preview = sorted(args.sequence.glob("frame_*_preview.png"))
    pix = sorted(args.sequence.glob("frame_*.pix"))
    atr = sorted(args.sequence.glob("frame_*.atr"))
    if not source or len(source) != len(preview) or len(source) != len(pix) or len(source) != len(atr):
        raise SystemExit("sequence must contain matching source, preview, pix, and atr frames")
    source_changes = transitions(source)
    preview_changes = transitions(preview)
    frames = [ECMFrame(p.read_bytes(), a.read_bytes()) for p, a in zip(pix, atr)]
    loop_payload, _ = encode_hybrid(frames[-1], frames[0])

    def summary(values: list[float]) -> dict[str, float]:
        ordinary = np.asarray(values[1:], dtype=np.float64)
        return {
            "loop_mse": values[0],
            "ordinary_median_mse": float(np.median(ordinary)),
            "ordinary_mean_mse": float(np.mean(ordinary)),
            "loop_to_median_ratio": values[0] / float(np.median(ordinary)),
            "largest_ordinary_mse": float(np.max(ordinary)),
        }

    report = {
        "frames": len(source),
        "source": summary(source_changes),
        "reconstructed": summary(preview_changes),
        "last_to_first_hybrid_bytes": len(loop_payload),
        "source_transition_mse": source_changes,
        "reconstructed_transition_mse": preview_changes,
    }
    output = args.output or args.sequence.parent / "loop_transition.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
