#!/usr/bin/env python3
"""Run a reproducible temporal-penalty sweep using encode_sequence.py."""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path


def summarize(statistics: Path) -> tuple[float, float]:
    with statistics.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    deltas = rows[1:]
    mean_changed = sum(int(row["changed_plane_bytes"]) for row in deltas) / max(1, len(deltas))
    mean_mse = sum(float(row["rgb_mse"]) for row in rows) / len(rows)
    return mean_changed, mean_mse


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--fps", type=float, default=12.0)
    parser.add_argument("--max-frames", type=int, default=12)
    parser.add_argument("--penalties", type=float, nargs="+", default=[0, 2500, 5000, 10000])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    encoder = Path(__file__).with_name("encode_sequence.py")
    summary_rows = []
    for penalty in args.penalties:
        label = f"p{penalty:g}".replace(".", "_")
        destination = args.output / label
        command = [
            sys.executable, str(encoder), str(args.input), str(destination),
            "--fps", str(args.fps), "--max-frames", str(args.max_frames),
            "--change-penalty", str(penalty),
        ]
        subprocess.run(command, check=True)
        mean_changed, mean_mse = summarize(destination / "statistics.csv")
        summary_rows.append({
            "change_penalty": f"{penalty:g}",
            "mean_changed_plane_bytes": f"{mean_changed:.3f}",
            "mean_rgb_mse": f"{mean_mse:.3f}",
        })
    with (args.output / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0]))
        writer.writeheader()
        writer.writerows(summary_rows)
    print(f"wrote {args.output / 'summary.csv'}")


if __name__ == "__main__":
    main()
