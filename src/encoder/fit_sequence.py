#!/usr/bin/env python3
"""Rate-control an existing unrestricted ECM sequence to one clip budget."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

from encode_sequence import native_rate_control_hybrid, rate_control_hybrid
from svd_ecm import ECMFrame, screen_offset
from svd_stream import encode_hybrid
from progress import progress, progress_done


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sequence", type=Path)
    parser.add_argument("--targets", type=Path,
                        help="unrestricted candidate sequence; defaults to output sequence")
    parser.add_argument("--clip-delta-bytes", type=int, required=True)
    parser.add_argument("--clip-min-frame-bytes", type=int, default=200)
    parser.add_argument("--clip-max-frame-bytes", type=int, default=0)
    parser.add_argument("--encoder", choices=("python", "native"), default="python")
    parser.add_argument("--native-encoder", type=Path,
                        help="path to svdenc executable")
    parser.add_argument("--max-cell-age", type=int, default=0)
    parser.add_argument("--cell-age-bonus", type=int, default=250000)
    args = parser.parse_args()

    native_executable = args.native_encoder
    if args.encoder == "native" and native_executable is None:
        suffix = ".exe" if sys.platform == "win32" else ""
        native_executable = (Path(__file__).parents[1] / "native_encoder" /
                             "build" / f"svdenc{suffix}")
    if native_executable is not None:
        native_executable = native_executable.resolve()
        if not native_executable.is_file():
            raise SystemExit(f"native encoder was not found: {native_executable}")

    target_dir = args.targets or args.sequence
    target_prefixes = sorted(target_dir.glob("frame_*.pix"))
    prefixes = [args.sequence / path.name for path in target_prefixes]
    args.sequence.mkdir(parents=True, exist_ok=True)
    targets = [ECMFrame(path.read_bytes(), path.with_suffix(".atr").read_bytes())
               for path in target_prefixes]
    if len(targets) < 2:
        raise SystemExit("fitting requires at least two ECM frames")
    minimum = args.clip_min_frame_bytes * (len(targets) - 1)
    if args.clip_delta_bytes < minimum:
        raise SystemExit(f"clip budget {args.clip_delta_bytes} is below minimum {minimum}")

    rendered = [np.asarray(frame.render(), dtype=np.float32) for frame in targets]
    targets[0].write(prefixes[0].with_suffix(""))
    targets[0].render().save(prefixes[0].with_name(prefixes[0].stem + "_preview.png"))
    weights = [0.0] + [
        5.0 + float(np.mean(np.abs(rendered[i] - rendered[i - 1])))
        for i in range(1, len(rendered))
    ]
    remaining = args.clip_delta_bytes
    previous = targets[0]
    cell_age = np.zeros(6144, dtype=np.uint8)
    report = []
    with tempfile.TemporaryDirectory(prefix="svd_fit_") as temporary:
        native_workspace = Path(temporary)
        for index in range(1, len(targets)):
            progress(f"Rate-controlling frame {index}/{len(targets) - 1} "
                     f"({args.encoder})")
            remaining_frames = len(targets) - index
            remaining_weight = sum(weights[index:])
            proportional = round(remaining * weights[index] / remaining_weight)
            reserve = args.clip_min_frame_bytes * (remaining_frames - 1)
            allocation = max(args.clip_min_frame_bytes, proportional)
            allocation = min(allocation, max(args.clip_min_frame_bytes, remaining - reserve))
            if args.clip_max_frame_bytes:
                allocation = min(allocation, args.clip_max_frame_bytes)
            if args.encoder == "native":
                assert native_executable is not None
                forced = cell_age >= args.max_cell_age if args.max_cell_age else None
                fitted, stored_bytes, selected = native_rate_control_hybrid(
                    native_executable, native_workspace, previous, targets[index],
                    rendered[index], allocation, forced, args.cell_age_bonus)
            else:
                fitted, stored_bytes, selected = rate_control_hybrid(
                    previous, targets[index], rendered[index], allocation)
            prefix = prefixes[index]
            fitted.write(prefix.with_suffix(""))
            fitted.render().save(prefix.with_name(prefix.stem + "_preview.png"))
            exact_bytes = len(encode_hybrid(previous, fitted)[0])
            if args.max_cell_age:
                pending = np.zeros(6144, dtype=bool)
                for logical in range(6144):
                    y, xb = divmod(logical, 32)
                    offset = screen_offset(y, xb)
                    pending[logical] = (fitted.bitmap[offset] != targets[index].bitmap[offset] or
                                        fitted.attributes[offset] != targets[index].attributes[offset])
                cell_age = np.where(pending, np.minimum(cell_age + 1, 255), 0).astype(np.uint8)
            remaining -= exact_bytes
            report.append({"frame": index, "allocated_bytes": allocation,
                           "update_bytes": exact_bytes, "selected_cells": selected})
            previous = fitted

    progress_done(f"Completed rate control of {len(targets) - 1} frame updates")

    (args.sequence / "fit_report.json").write_text(json.dumps({
        "clip_delta_bytes": args.clip_delta_bytes,
        "used_delta_bytes": sum(row["update_bytes"] for row in report),
        "remaining_delta_bytes": remaining,
        "encoder": args.encoder,
        "frames": report,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"fitted {len(targets)} frames: used {args.clip_delta_bytes - remaining} "
          f"of {args.clip_delta_bytes} delta bytes", flush=True)


if __name__ == "__main__":
    main()
