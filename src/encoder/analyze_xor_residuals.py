#!/usr/bin/env python3
"""Report repeated-value structure in ECM XOR residuals."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sequence", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    frames = [path.read_bytes() + path.with_suffix(".atr").read_bytes()
              for path in sorted(args.sequence.glob("frame_*.pix"))]
    lengths, values = Counter(), Counter()
    changed = 0
    for previous, current in zip(frames, frames[1:]):
        delta = bytes(a ^ b for a, b in zip(previous, current))
        changed += sum(value != 0 for value in delta)
        position = 0
        while position < len(delta):
            if not delta[position]:
                position += 1
                continue
            end = position + 1
            while end < len(delta) and delta[end] == delta[position]:
                end += 1
            if end - position >= 2:
                lengths[end - position] += 1
                values[delta[position]] += end - position
            position = end
    report = {
        "frames": len(frames), "changed_bytes": changed,
        "repeated_bytes": sum(values.values()), "repeated_runs": sum(lengths.values()),
        "top_values": values.most_common(16), "run_lengths": lengths.most_common(),
    }
    output = args.output or args.sequence / "xor_residuals.json"
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
