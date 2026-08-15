#!/usr/bin/env python3
"""Benchmark Python and C encoder backends on an identical short sequence."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parents[2]


def encode(source: Path, output: Path, backend: str, frames: int) -> float:
    command = [sys.executable, str(ROOT / "src" / "encoder" / "encode_sequence.py"), str(source), str(output),
               "--fps", "20", "--max-frames", str(frames), "--geometry", "fit",
               "--dither-mode", "sierra-lite", "--encoder", backend,
               "--brightness", "-0.02", "--sierra-gamma", "1.3",
               "--temporal-attr-penalty", "0.08", "--temporal-pixel-penalty", "0.08",
               "--background-motion-threshold", "8", "--background-penalty-multiplier", "4",
               "--max-hybrid-bytes", "700"]
    start = time.perf_counter(); subprocess.run(command, cwd=ROOT, check=True)
    return time.perf_counter() - start


def metrics(directory: Path) -> dict[str, float]:
    with (directory / "statistics.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return {
        "mean_rgb_mse": sum(float(row["rgb_mse"]) for row in rows) / len(rows),
        "mean_hybrid_bytes": sum(int(row["hybrid_bytes"]) for row in rows[1:]) / max(1, len(rows) - 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "video" / "british-flag-2.gif")
    parser.add_argument("--frames", type=int, default=10)
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "native_benchmark")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    python_time = encode(args.source, args.output / "python", "python", args.frames)
    native_time = encode(args.source, args.output / "native", "native", args.frames)
    report = {
        "source": str(args.source.resolve()), "frames": args.frames,
        "python_seconds": python_time, "native_seconds": native_time,
        "speedup": python_time / native_time,
        "python": metrics(args.output / "python"), "native": metrics(args.output / "native"),
    }
    (args.output / "benchmark.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
