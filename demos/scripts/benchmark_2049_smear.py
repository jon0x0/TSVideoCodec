#!/usr/bin/env python3
"""Compare temporal-persistence profiles for 2049lonely finger trails."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parents[2]
SOURCE = ROOT / "video" / "2049lonely.gif"
OUTPUT = ROOT / "build" / "2049_smear_benchmark"
PROFILES = {
    "current": (0.08, 0.08, 8, 4),
    "reduced": (0.02, 0.02, 2, 2),
    "source_fidelity": (0.0, 0.0, 0, 1),
}


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report = {}
    for name, (attr, pixel, threshold, multiplier) in PROFILES.items():
        directory = OUTPUT / name
        command = [sys.executable, "src/encoder/encode_sequence.py", str(SOURCE), str(directory),
                   "--fps", "20", "--max-frames", "36", "--geometry", "fit",
                   "--dither-mode", "sierra-lite", "--encoder", "native",
                   "--brightness", "-0.02", "--sierra-gamma", "1.3",
                   "--temporal-attr-penalty", str(attr), "--temporal-pixel-penalty", str(pixel),
                   "--background-motion-threshold", str(threshold),
                   "--background-penalty-multiplier", str(multiplier),
                   "--max-hybrid-bytes", "700", "--keep-source-frames"]
        start = time.perf_counter(); subprocess.run(command, cwd=ROOT, check=True)
        elapsed = time.perf_counter() - start
        with (directory / "statistics.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        report[name] = {
            "temporal_attr_penalty": attr, "temporal_pixel_penalty": pixel,
            "background_motion_threshold": threshold, "background_penalty_multiplier": multiplier,
            "mean_rgb_mse": sum(float(row["rgb_mse"]) for row in rows) / len(rows),
            "mean_changed_plane_bytes": sum(int(row["changed_plane_bytes"]) for row in rows[1:]) / 35,
            "mean_hybrid_bytes": sum(int(row["hybrid_bytes"]) for row in rows[1:]) / 35,
            "elapsed_seconds": elapsed,
        }
    (OUTPUT / "benchmark.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
