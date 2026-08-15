#!/usr/bin/env python3
"""Compare the portable C Sierra encoder with the Python reference."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src" / "encoder"))
from svd_ecm import ECMFrame, encode_image_sierra_lite  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--encoder", type=Path,
                        default=ROOT / "src" / "native_encoder" / "build" / "svdenc.exe")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "build" / "native_encoder_comparison.json")
    args = parser.parse_args()
    image = Image.open(args.image).convert("RGB")
    prepared = image.resize((256, 192), Image.Resampling.LANCZOS)
    settings = dict(brightness=-0.02, contrast=1.0, saturation=1.0, gamma=1.3)
    start = time.perf_counter()
    reference = encode_image_sierra_lite(prepared, **settings)
    python_seconds = time.perf_counter() - start
    with tempfile.TemporaryDirectory(prefix="svd_native_test_") as name:
        directory = Path(name)
        rgb = directory / "input.rgb"; pix = directory / "output.pix"; atr = directory / "output.atr"
        rgb.write_bytes(np.asarray(prepared, dtype=np.uint8).tobytes())
        command = [str(args.encoder), "sierra", str(rgb), str(pix), str(atr),
                   "--brightness", "-0.02", "--contrast", "1", "--saturation", "1", "--gamma", "1.3"]
        start = time.perf_counter(); subprocess.run(command, check=True)
        native_seconds = time.perf_counter() - start
        native_pix, native_atr = pix.read_bytes(), atr.read_bytes()
    bitmap_mismatches = sum(a != b for a, b in zip(reference.bitmap, native_pix))
    attribute_mismatches = sum(a != b for a, b in zip(reference.attributes, native_atr))
    reference_rgb = np.asarray(reference.render(), dtype=np.int16)
    native_rgb = np.asarray(ECMFrame(native_pix, native_atr).render(), dtype=np.int16)
    rendered_channel_mismatches = int(np.count_nonzero(reference_rgb != native_rgb))
    rendered_mse = float(np.mean((reference_rgb - native_rgb) ** 2))
    report = {
        "image": str(args.image.resolve()), "encoder": str(args.encoder.resolve()),
        "bitmap_mismatches": bitmap_mismatches,
        "attribute_mismatches": attribute_mismatches,
        "plane_exact": bitmap_mismatches == 0 and attribute_mismatches == 0,
        "rendered_channel_mismatches": rendered_channel_mismatches,
        "rendered_mse": rendered_mse,
        "render_exact": rendered_channel_mismatches == 0,
        "python_seconds": python_seconds, "native_seconds": native_seconds,
        "speedup": python_seconds / native_seconds,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not report["render_exact"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
