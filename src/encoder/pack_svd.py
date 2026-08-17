#!/usr/bin/env python3
"""Pack script-generated .pix/.atr sequence planes into an SVD v0 stream."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from svd_ecm import ECMFrame
from svd_stream import decode_stream, encode_stream


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sequence", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--fps-num", type=int, default=12)
    parser.add_argument("--fps-den", type=int, default=1)
    parser.add_argument("--delta-format", choices=("runs", "sparse", "xor", "hybrid"), default="hybrid")
    args = parser.parse_args()
    pix_files = sorted(args.sequence.glob("frame_*.pix"))
    frames = []
    for pix in pix_files:
        atr = pix.with_suffix(".atr")
        if not atr.exists():
            raise SystemExit(f"missing paired plane: {atr}")
        frames.append(ECMFrame(pix.read_bytes(), atr.read_bytes()))
    print(f"Packing {len(frames)} frames into an SVD {args.delta_format} stream...", flush=True)
    stream, stats = encode_stream(frames, args.fps_num, args.fps_den, args.delta_format)
    decoded, fps = decode_stream(stream)
    if decoded != frames or fps != (args.fps_num, args.fps_den):
        raise SystemExit("internal SVD round-trip verification failed")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(stream)
    rows = [{"frame": index, **item.__dict__} for index, item in enumerate(stats)]
    report = args.output.with_suffix(".csv")
    with report.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    metadata = {
        "format": "SVD v0 provisional",
        "frames": len(frames),
        "fps_num": args.fps_num,
        "fps_den": args.fps_den,
        "stream_bytes": len(stream),
        "round_trip_verified": True,
        "delta_format": args.delta_format,
    }
    args.output.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"packed {len(frames)} frames into {len(stream)} bytes")
    print("reference decoder round-trip verified")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
