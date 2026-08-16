#!/usr/bin/env python3
"""Extract a video sequence, encode ECM frames, and report temporal changes."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image

from svd_ecm import ECMFrame, HEIGHT, encode_image, encode_image_sierra_lite, screen_offset
from svd_stream import encode_hybrid, encode_hybrid_plane
from auto_profile import (adjust as adjust_auto, analyze as analyze_auto,
                          apply_foreground_overlays, apply_plate,
                          apply_solid_dark_closure, solidify_upper_background,
                          unadjust as unadjust_auto)


def native_sierra_encoder(
    executable: Path, workspace: Path, source_rgb: np.ndarray, previous: ECMFrame | None,
    stable_cells: np.ndarray | None, *, brightness: float, contrast: float,
    saturation: float, gamma: float, temporal_attr_penalty: float,
    temporal_pixel_penalty: float, stable_penalty_multiplier: float,
    flat_ordered_variance: float,
    flat_solid_variance: float,
    flat_solid_background_distance: float, flat_solid_max_y: int,
    flat_ordered_attribute: int,
    flat_ordered_mix: float,
) -> ECMFrame:
    """Run the dependency-free C11 Sierra core on one prepared RGB24 frame."""
    input_path = workspace / "input.rgb"
    pix_path = workspace / "output.pix"
    atr_path = workspace / "output.atr"
    input_path.write_bytes(np.asarray(source_rgb, dtype=np.uint8).tobytes())
    command = [str(executable), "sierra", str(input_path), str(pix_path), str(atr_path),
               "--brightness", str(brightness), "--contrast", str(contrast),
               "--saturation", str(saturation), "--gamma", str(gamma),
               "--temporal-attr-penalty", str(temporal_attr_penalty),
               "--temporal-pixel-penalty", str(temporal_pixel_penalty),
               "--stable-penalty-multiplier", str(stable_penalty_multiplier)]
    command += ["--flat-ordered-variance", str(flat_ordered_variance)]
    command += ["--flat-solid-variance", str(flat_solid_variance)]
    command += ["--flat-solid-background-distance", str(flat_solid_background_distance),
                "--flat-solid-max-y", str(flat_solid_max_y),
                "--flat-ordered-attribute", str(flat_ordered_attribute),
                "--flat-ordered-mix", str(flat_ordered_mix)]
    if previous is not None:
        previous_pix = workspace / "previous.pix"; previous_atr = workspace / "previous.atr"
        previous_pix.write_bytes(previous.bitmap); previous_atr.write_bytes(previous.attributes)
        command += ["--previous-pix", str(previous_pix), "--previous-atr", str(previous_atr)]
    if stable_cells is not None:
        stable_path = workspace / "stable.cells"
        stable_path.write_bytes(np.asarray(stable_cells, dtype=np.uint8).tobytes())
        command += ["--stable-cells", str(stable_path)]
    subprocess.run(command, check=True)
    return ECMFrame(pix_path.read_bytes(), atr_path.read_bytes())


def native_rate_control_hybrid(
    executable: Path, workspace: Path, previous: ECMFrame, candidate: ECMFrame,
    source_rgb: np.ndarray, byte_budget: int, forced_cells: np.ndarray | None = None,
    forced_cell_bonus: int = 250000,
) -> tuple[ECMFrame, int, int]:
    paths = {name: workspace / name for name in
             ("rate.rgb", "rate_prev.pix", "rate_prev.atr", "rate_cand.pix", "rate_cand.atr",
              "rate_out.pix", "rate_out.atr")}
    paths["rate.rgb"].write_bytes(np.asarray(source_rgb, dtype=np.uint8).tobytes())
    paths["rate_prev.pix"].write_bytes(previous.bitmap); paths["rate_prev.atr"].write_bytes(previous.attributes)
    paths["rate_cand.pix"].write_bytes(candidate.bitmap); paths["rate_cand.atr"].write_bytes(candidate.attributes)
    command = [
        str(executable), "rate-hybrid", str(paths["rate.rgb"]),
        str(paths["rate_prev.pix"]), str(paths["rate_prev.atr"]),
        str(paths["rate_cand.pix"]), str(paths["rate_cand.atr"]),
        str(paths["rate_out.pix"]), str(paths["rate_out.atr"]), str(byte_budget)]
    if forced_cells is not None:
        forced_path = workspace / "forced.cells"
        forced_path.write_bytes(np.asarray(forced_cells, dtype=np.uint8).tobytes())
        command += ["--forced-cells", str(forced_path),
                    "--forced-cell-bonus", str(forced_cell_bonus)]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    fields = result.stdout.split()
    if len(fields) != 2:
        raise RuntimeError(f"invalid native rate-control result: {result.stdout!r}")
    frame = ECMFrame(paths["rate_out.pix"].read_bytes(), paths["rate_out.atr"].read_bytes())
    return frame, int(fields[0]), int(fields[1])


def change_statistics(previous: ECMFrame | None, current: ECMFrame) -> dict[str, int | str]:
    if previous is None:
        return {
            "frame_type": "KEY",
            "unchanged_cells": 0,
            "bitmap_only_cells": 0,
            "attribute_only_cells": 0,
            "both_cells": 6144,
            "changed_plane_bytes": 12288,
        }
    old_bitmap = np.frombuffer(previous.bitmap, dtype=np.uint8)
    old_attrs = np.frombuffer(previous.attributes, dtype=np.uint8)
    new_bitmap = np.frombuffer(current.bitmap, dtype=np.uint8)
    new_attrs = np.frombuffer(current.attributes, dtype=np.uint8)
    bitmap_changed = old_bitmap != new_bitmap
    attrs_changed = old_attrs != new_attrs
    changed = int(np.count_nonzero(bitmap_changed | attrs_changed))
    return {
        "frame_type": "REPEAT" if changed == 0 else "DELTA",
        "unchanged_cells": int(np.count_nonzero(~bitmap_changed & ~attrs_changed)),
        "bitmap_only_cells": int(np.count_nonzero(bitmap_changed & ~attrs_changed)),
        "attribute_only_cells": int(np.count_nonzero(~bitmap_changed & attrs_changed)),
        "both_cells": int(np.count_nonzero(bitmap_changed & attrs_changed)),
        "changed_plane_bytes": int(np.count_nonzero(bitmap_changed) + np.count_nonzero(attrs_changed)),
    }


def rate_control_hybrid(previous: ECMFrame, candidate: ECMFrame, source_rgb: np.ndarray,
                        byte_budget: int) -> tuple[ECMFrame, int, int]:
    """Keep the visually most valuable 8x1 updates within a hybrid payload budget."""
    if byte_budget <= 0:
        payload, _ = encode_hybrid(previous, candidate)
        return candidate, len(payload), 6144
    old_rgb = np.asarray(previous.render(), dtype=np.float32)
    new_rgb = np.asarray(candidate.render(), dtype=np.float32)
    old_error = np.sum((source_rgb - old_rgb) ** 2, axis=2)
    new_error = np.sum((source_rgb - new_rgb) ** 2, axis=2)
    improvements = (old_error - new_error).reshape(HEIGHT, 32, 8).sum(axis=2)
    ranked = np.argsort(improvements.reshape(-1))[::-1]
    ranked = ranked[improvements.reshape(-1)[ranked] > 0]

    def construct(count: int) -> ECMFrame:
        bitmap = bytearray(previous.bitmap)
        attrs = bytearray(previous.attributes)
        for logical in ranked[:count]:
            y, xb = divmod(int(logical), 32)
            offset = screen_offset(y, xb)
            bitmap[offset] = candidate.bitmap[offset]
            attrs[offset] = candidate.attributes[offset]
        return ECMFrame(bytes(bitmap), bytes(attrs))

    low, high = 0, len(ranked)
    best = previous
    best_size = len(encode_hybrid(previous, previous)[0])
    best_count = 0
    while low <= high:
        middle = (low + high) // 2
        trial = construct(middle)
        size = len(encode_hybrid(previous, trial)[0])
        if size <= byte_budget:
            best, best_size, best_count = trial, size, middle
            low = middle + 1
        else:
            high = middle - 1
    return best, best_size, best_count


def _select_plane(previous: ECMFrame, candidate: ECMFrame, source_rgb: np.ndarray,
                  plane_name: str, byte_budget: int, forced: np.ndarray | None = None
                  ) -> tuple[ECMFrame, int, int, np.ndarray]:
    """Select updates for one plane while holding the other plane fixed."""
    old_plane = previous.bitmap if plane_name == "bitmap" else previous.attributes
    target_plane = candidate.bitmap if plane_name == "bitmap" else candidate.attributes
    full = ECMFrame(target_plane, previous.attributes) if plane_name == "bitmap" else ECMFrame(previous.bitmap, target_plane)
    old_rgb = np.asarray(previous.render(), dtype=np.float32)
    new_rgb = np.asarray(full.render(), dtype=np.float32)
    old_error = np.sum((source_rgb - old_rgb) ** 2, axis=2)
    new_error = np.sum((source_rgb - new_rgb) ** 2, axis=2)
    scores = (old_error - new_error).reshape(HEIGHT, 32, 8).sum(axis=2).reshape(-1)
    forced_flat = np.zeros(6144, dtype=bool) if forced is None else forced.reshape(-1)
    scores = scores.copy()
    scores[forced_flat] += 1e15
    ranked = np.argsort(scores)[::-1]
    ranked = ranked[(scores[ranked] > 0) | forced_flat[ranked]]

    def construct(count: int) -> tuple[ECMFrame, np.ndarray]:
        plane = bytearray(old_plane)
        selected = np.zeros(6144, dtype=bool)
        for logical in ranked[:count]:
            y, xb = divmod(int(logical), 32)
            offset = screen_offset(y, xb)
            plane[offset] = target_plane[offset]
            selected[logical] = True
        frame = (ECMFrame(bytes(plane), previous.attributes) if plane_name == "bitmap"
                 else ECMFrame(previous.bitmap, bytes(plane)))
        return frame, selected

    low, high = 0, len(ranked)
    best, best_selected = previous, np.zeros(6144, dtype=bool)
    best_size = len(encode_hybrid_plane(old_plane, old_plane))
    best_count = 0
    while low <= high:
        middle = (low + high) // 2
        trial, selected = construct(middle)
        trial_plane = trial.bitmap if plane_name == "bitmap" else trial.attributes
        size = len(encode_hybrid_plane(old_plane, trial_plane))
        if size <= byte_budget:
            best, best_selected, best_size, best_count = trial, selected, size, middle
            low = middle + 1
        else:
            high = middle - 1
    return best, best_size, best_count, best_selected


def rate_control_planes(previous: ECMFrame, candidate: ECMFrame, source_rgb: np.ndarray,
                        bitmap_budget: int, attribute_budget: int, attribute_age: np.ndarray,
                        max_attribute_age: int) -> tuple[ECMFrame, int, int, int, int, np.ndarray]:
    bitmap_frame, bitmap_bytes, bitmap_cells, _ = _select_plane(
        previous, candidate, source_rgb, "bitmap", bitmap_budget)
    attr_target = ECMFrame(bitmap_frame.bitmap, candidate.attributes)
    forced = attribute_age >= max_attribute_age if max_attribute_age > 0 else None
    result, attr_bytes, attr_cells, attr_selected = _select_plane(
        bitmap_frame, attr_target, source_rgb, "attributes", attribute_budget, forced)
    logical_candidate = np.empty(6144, dtype=np.uint8)
    logical_result = np.empty(6144, dtype=np.uint8)
    for logical in range(6144):
        y, xb = divmod(logical, 32); offset = screen_offset(y, xb)
        logical_candidate[logical] = candidate.attributes[offset]
        logical_result[logical] = result.attributes[offset]
    pending = logical_result != logical_candidate
    new_age = np.where(pending, np.minimum(attribute_age + 1, 255), 0).astype(np.uint8)
    new_age[attr_selected] = 0
    return result, bitmap_bytes, attr_bytes, bitmap_cells, attr_cells, new_age


def parse_source_window(value: str) -> tuple[float, float, float]:
    try:
        parts = tuple(float(part.strip()) for part in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected X,Y,WIDTH") from error
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("expected X,Y,WIDTH")
    return parts


def probe_video_size(video: Path) -> tuple[int, int]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise SystemExit("ffprobe was not found on PATH")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", str(video)],
        check=True, capture_output=True, text=True,
    )
    streams = json.loads(result.stdout).get("streams", [])
    if not streams:
        raise SystemExit("ffprobe found no video stream")
    return int(streams[0]["width"]), int(streams[0]["height"])


def resolve_source_window(
    source_size: tuple[int, int], requested: tuple[float, float, float], normalized: bool,
) -> dict[str, int | float | str]:
    source_width, source_height = source_size
    req_x, req_y, req_extent = requested
    if normalized:
        if not (0 <= req_x < 1 and 0 <= req_y < 1 and req_x < req_extent <= 1):
            raise ValueError("normalized X,Y must be in [0,1), and RIGHT must be greater than X and at most 1")
        x = round(req_x * source_width)
        y = round(req_y * source_height)
        wanted_width = round((req_extent - req_x) * source_width)
        mode = "normalized"
    else:
        if req_x < 0 or req_y < 0 or req_extent <= 0:
            raise ValueError("pixel X and Y must be non-negative, and WIDTH positive")
        if not all(float(value).is_integer() for value in (req_x, req_y, req_extent)):
            raise ValueError("pixel source-window values must be whole numbers")
        x, y, wanted_width = int(req_x), int(req_y), int(req_extent)
        mode = "pixels"
    if x >= source_width or y >= source_height:
        raise ValueError("source-window origin is outside the source frame")

    # The requested width is a maximum. Shrink it when necessary so the fixed
    # upper-left origin still yields a complete 4:3 TS2068 viewport.
    max_width = min(source_width - x, (source_height - y) * 4 // 3)
    width = min(wanted_width, max_width)
    width -= width % 4
    height = width * 3 // 4
    if width < 1 or height < 1:
        raise ValueError("source window is too small after fitting a 4:3 viewport")
    return {
        "mode": mode, "source_width": source_width, "source_height": source_height,
        "requested_x": req_x, "requested_y": req_y,
        "requested_right" if normalized else "requested_width": req_extent,
        "x": x, "y": y, "width": width, "height": height,
        "width_was_reduced": width < wanted_width,
    }


def extract_frames(
    video: Path, directory: Path, fps: float, max_frames: int, start_seconds: float = 0.0,
    geometry: str = "fit", source_window: dict[str, int | float | str] | None = None,
) -> list[Path]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg was not found on PATH")
    filters = [f"fps={fps}"]
    if source_window:
        filters.append(
            f"crop={source_window['width']}:{source_window['height']}:"
            f"{source_window['x']}:{source_window['y']}")
    if geometry == "fit":
        filters.append("scale=256:192:force_original_aspect_ratio=decrease,pad=256:192:(ow-iw)/2:(oh-ih)/2")
    elif geometry == "crop":
        filters.append("scale=256:192:force_original_aspect_ratio=increase,crop=256:192")
    else:
        raise ValueError("geometry must be 'fit' or 'crop'")
    command = [ffmpeg, "-v", "error"]
    if start_seconds > 0:
        command += ["-ss", f"{start_seconds:.6f}"]
    command += ["-i", str(video), "-an", "-vf", ",".join(filters)]
    if max_frames > 0:
        command += ["-frames:v", str(max_frames)]
    command += [str(directory / "source_%05d.png")]
    subprocess.run(command, check=True)
    return sorted(directory.glob("source_*.png"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--auto", action="store_true",
                        help="analyze the complete clip and select generic static-region coding")
    parser.add_argument("--auto-colour-policy", "--auto-color-policy",
                        choices=("faithful", "quiet"), default="faithful",
                        help="preserve source chroma or permit quieter neutral flat regions")
    parser.add_argument("--auto-plate-encoder",
                        choices=("sierra-structure", "sierra-texture", "sierra-hybrid", "sierra", "ordered"),
                        default="ordered",
                        help="encode the static auto plate with Sierra Lite or ordered colour")
    parser.add_argument("--auto-material-dither",
                        choices=("sierra-line", "shell-aware", "ordered-bayer", "solid-dark"),
                        default="sierra-line",
                        help="dither strategy for dark foreground materials")
    parser.add_argument("--auto-solid-upper-background",
                        choices=("off", "blue", "light-blue"),
                        default="off",
                        help="replace stable neutral upper-background plate cells")
    parser.add_argument("--auto-solid-upper-max-y", type=int, default=112,
                        help="exclusive scanline limit for solid upper background")
    parser.add_argument("--fps", type=float, default=12.0)
    parser.add_argument("--start-seconds", type=float, default=0.0)
    parser.add_argument("--max-frames", type=int, default=0, help="zero means all frames")
    parser.add_argument(
        "--change-penalty",
        type=float,
        default=0.0,
        help="per changed bitmap/attribute byte in perceptual-error units",
    )
    parser.add_argument("--keep-source-frames", action="store_true")
    parser.add_argument("--geometry", choices=("fit", "crop"), default="fit")
    source_window = parser.add_mutually_exclusive_group()
    source_window.add_argument(
        "--source-window", type=parse_source_window, metavar="X,Y,RIGHT",
        help="normalized upper-left X,Y and right edge; output viewport is 4:3")
    source_window.add_argument(
        "--source-window-pixels", type=parse_source_window, metavar="X,Y,WIDTH",
        help="pixel upper-left X,Y and maximum width; output viewport is 4:3")
    chroma = parser.add_mutually_exclusive_group()
    chroma.add_argument("--chroma-weight", type=float, default=1.0)
    chroma.add_argument("--adaptive-chroma", action="store_true")
    parser.add_argument("--source-gamma", type=float, default=0.8)
    parser.add_argument("--dither-strength", type=float, default=0.0)
    parser.add_argument("--edge-weight", type=float, default=0.0)
    parser.add_argument("--dither-mode", choices=("sierra-lite", "legacy"), default="sierra-lite")
    parser.add_argument("--encoder", choices=("python", "native"), default="python",
                        help="implementation backend; native currently supports Sierra Lite")
    parser.add_argument("--native-encoder", type=Path,
                        help="path to svdenc executable (defaults to native_encoder/build)")
    parser.add_argument("--brightness", type=float, default=-0.02, help="linear-light offset")
    parser.add_argument("--contrast", type=float, default=1.0)
    parser.add_argument("--saturation", type=float, default=1.0)
    parser.add_argument("--sierra-gamma", type=float, default=1.3)
    parser.add_argument("--temporal-attr-penalty", type=float, default=0.01)
    parser.add_argument("--temporal-pixel-penalty", type=float, default=0.01)
    parser.add_argument("--background-motion-threshold", type=float, default=8.0,
                        help="mean RGB change below which an 8x1 cell is treated as stable")
    parser.add_argument("--background-penalty-multiplier", type=float, default=4.0)
    parser.add_argument("--flat-ordered-variance", type=float, default=0.0,
                        help="use ordered dithering below this linear RGB cell variance; zero disables")
    parser.add_argument("--flat-solid-variance", type=float, default=0.0,
                        help="use one nearest palette colour below this cell variance; zero disables")
    parser.add_argument("--flat-solid-background-distance", type=float, default=float("inf"),
                        help="restrict flat ordered/solid cells to the scanline's left-edge background")
    parser.add_argument("--flat-solid-max-y", type=int, default=192)
    parser.add_argument("--flat-ordered-attribute", type=lambda value: int(value, 0), default=-1,
                        help="fixed ECM attribute for ordered background; -1 selects automatically")
    parser.add_argument("--flat-ordered-mix", type=float, default=-1,
                        help="fixed ordered ink fraction from 0 to 1; -1 selects automatically")
    parser.add_argument("--max-hybrid-bytes", type=int, default=0,
                        help="per-delta compressed-byte budget; zero disables rate control")
    parser.add_argument("--max-cell-age", type=int, default=0,
                        help="force shared-budget cells pending this many frames; zero disables")
    parser.add_argument("--cell-age-bonus", type=int, default=250000,
                        help="rate-distortion priority added to overdue chroma cells")
    parser.add_argument("--cyclic-warmup-passes", type=int, default=0,
                        help="hidden causal passes before output so loop frame zero is rate controlled")
    parser.add_argument("--max-bitmap-bytes", type=int, default=0)
    parser.add_argument("--max-attribute-bytes", type=int, default=0)
    parser.add_argument("--max-attribute-age", type=int, default=0)
    parser.add_argument("--clip-delta-bytes", type=int, default=0,
                        help="global delta budget distributed by source motion; zero disables")
    parser.add_argument("--clip-bitmap-fraction", type=float, default=0.8)
    parser.add_argument("--clip-min-frame-bytes", type=int, default=200)
    parser.add_argument("--clip-max-frame-bytes", type=int, default=0,
                        help="hard per-frame cap for global allocation; zero disables")
    args = parser.parse_args()
    resolved_source_window = None
    requested_source_window = args.source_window or args.source_window_pixels
    if requested_source_window is not None:
        try:
            resolved_source_window = resolve_source_window(
                probe_video_size(args.input), requested_source_window,
                normalized=args.source_window is not None)
        except ValueError as error:
            raise SystemExit(f"invalid source window: {error}") from error
    if args.start_seconds < 0:
        raise SystemExit("--start-seconds must not be negative")
    if args.cyclic_warmup_passes < 0:
        raise SystemExit("--cyclic-warmup-passes must not be negative")
    if not 0 <= args.max_cell_age <= 255:
        raise SystemExit("--max-cell-age must be between zero and 255")
    if args.cell_age_bonus < 0:
        raise SystemExit("--cell-age-bonus must not be negative")
    if args.max_cell_age and args.encoder != "native":
        raise SystemExit("--max-cell-age currently requires --encoder native")
    if args.cyclic_warmup_passes and args.max_hybrid_bytes <= 0:
        raise SystemExit("cyclic warmup currently requires --max-hybrid-bytes")
    if args.encoder == "native" and args.dither_mode != "sierra-lite":
        raise SystemExit("the native backend currently supports only --dither-mode sierra-lite")
    native_executable = args.native_encoder
    if args.encoder == "native" and native_executable is None:
        suffix = ".exe" if sys.platform == "win32" else ""
        native_executable = Path(__file__).parents[1] / "native_encoder" / "build" / f"svdenc{suffix}"
    if native_executable is not None:
        native_executable = native_executable.resolve()
        if not native_executable.is_file():
            raise SystemExit(f"native encoder was not found: {native_executable}")
    args.output.mkdir(parents=True, exist_ok=True)
    # Generated sequence directories are reusable. Remove only artifacts this
    # encoder owns so reducing --max-frames cannot leave stale frames for the
    # stream packer to consume.
    for pattern in ("frame_*.pix", "frame_*.atr", "frame_*_preview.png", "frame_*_source.png"):
        for generated in args.output.glob(pattern):
            generated.unlink()

    rows: list[dict[str, int | str]] = []
    previous: ECMFrame | None = None
    previous_source_rgb: np.ndarray | None = None
    attribute_age = np.zeros(6144, dtype=np.uint8)
    cell_age = np.zeros(6144, dtype=np.uint8)
    with tempfile.TemporaryDirectory(prefix="svd_frames_") as temp_name:
        native_workspace = Path(temp_name) / "native"
        native_workspace.mkdir()
        extracted = extract_frames(
            args.input, Path(temp_name), args.fps, args.max_frames, args.start_seconds, args.geometry,
            resolved_source_window,
        )
        if not extracted:
            raise SystemExit("ffmpeg extracted no video frames")
        source_rgbs = [np.asarray(Image.open(path).convert("RGB"), dtype=np.float32) for path in extracted]
        auto_profile = None
        if args.auto:
            auto_profile = analyze_auto(
                source_rgbs, brightness=args.brightness, contrast=args.contrast,
                saturation=args.saturation, gamma=args.sierra_gamma,
                colour_policy=args.auto_colour_policy)
            if args.auto_plate_encoder in ("sierra", "sierra-structure", "sierra-hybrid", "sierra-texture"):
                plate_rgb = unadjust_auto(
                    auto_profile.detail_reference, args.brightness, args.contrast,
                    args.saturation, args.sierra_gamma)
                if args.encoder == "native":
                    sierra_plate = native_sierra_encoder(
                        native_executable, native_workspace, plate_rgb, None, None,
                        brightness=args.brightness, contrast=args.contrast,
                        saturation=args.saturation, gamma=args.sierra_gamma,
                        temporal_attr_penalty=0, temporal_pixel_penalty=0,
                        stable_penalty_multiplier=1,
                        flat_ordered_variance=0, flat_solid_variance=0,
                        flat_solid_background_distance=float("inf"), flat_solid_max_y=192,
                        flat_ordered_attribute=-1, flat_ordered_mix=-1)
                else:
                    sierra_plate = encode_image_sierra_lite(
                        Image.fromarray(plate_rgb, "RGB"), brightness=args.brightness,
                        contrast=args.contrast, saturation=args.saturation,
                        gamma=args.sierra_gamma)
                if args.auto_plate_encoder in ("sierra-structure", "sierra-hybrid", "sierra-texture"):
                    ordered_rgb = adjust_auto(
                        np.asarray(auto_profile.plate.render()), args.brightness, args.contrast,
                        args.saturation, args.sierra_gamma)
                    sierra_rgb = adjust_auto(
                        np.asarray(sierra_plate.render()), args.brightness, args.contrast,
                        args.saturation, args.sierra_gamma)
                    target = auto_profile.detail_reference
                    ordered_error = np.mean((ordered_rgb - target) ** 2, axis=-1).reshape(HEIGHT, 32, 8).mean(axis=2)
                    sierra_error = np.mean((sierra_rgb - target) ** 2, axis=-1).reshape(HEIGHT, 32, 8).mean(axis=2)
                    bitmap = bytearray(auto_profile.plate.bitmap)
                    attrs = bytearray(auto_profile.plate.attributes)
                    detail_cells = target.reshape(HEIGHT, 32, 8, 3)
                    detail_variance = np.mean(
                        (detail_cells - detail_cells.mean(axis=2, keepdims=True)) ** 2,
                        axis=(2, 3))
                    textured = ((detail_variance > auto_profile.spatial_threshold * 2.0) &
                                auto_profile.base_cells)
                    if args.auto_plate_encoder == "sierra-structure":
                        # Prefer Sierra Lite only where it reconstructs real, stable
                        # sub-cell structure.  Comparing horizontal luminance deltas
                        # rejects attractive-looking but false dither in flat fields.
                        target_y = np.dot(target, (0.299, 0.587, 0.114)).reshape(HEIGHT, 32, 8)
                        ordered_y = np.dot(ordered_rgb, (0.299, 0.587, 0.114)).reshape(HEIGHT, 32, 8)
                        sierra_y = np.dot(sierra_rgb, (0.299, 0.587, 0.114)).reshape(HEIGHT, 32, 8)
                        target_edges = np.diff(target_y, axis=2)
                        ordered_edge_error = np.mean(
                            (np.diff(ordered_y, axis=2) - target_edges) ** 2, axis=2)
                        sierra_edge_error = np.mean(
                            (np.diff(sierra_y, axis=2) - target_edges) ** 2, axis=2)
                        structure = ((detail_variance > auto_profile.spatial_threshold * 0.5) &
                                     auto_profile.base_cells)
                        selected = (structure &
                                    (sierra_error < ordered_error * 1.12) &
                                    (sierra_edge_error < ordered_edge_error * 0.92))
                    else:
                        selected = (textured if args.auto_plate_encoder == "sierra-texture" else
                                    textured & (sierra_error < ordered_error * 0.95))
                    for y, xb in np.argwhere(selected):
                        offset = screen_offset(int(y), int(xb))
                        bitmap[offset] = sierra_plate.bitmap[offset]
                        attrs[offset] = sierra_plate.attributes[offset]
                    auto_profile.report["sierra_plate_cells"] = int(np.count_nonzero(selected))
                    sierra_plate = ECMFrame(bytes(bitmap), bytes(attrs))
                auto_profile = replace(auto_profile, plate=sierra_plate)
            if args.auto_solid_upper_background != "off":
                auto_profile = solidify_upper_background(
                    auto_profile, args.auto_solid_upper_background,
                    args.auto_solid_upper_max_y)
            auto_profile.report["plate_encoder"] = args.auto_plate_encoder
            (args.output / "auto_analysis.json").write_text(
                json.dumps(auto_profile.report, indent=2) + "\n", encoding="utf-8")
            # Raw logical 192x32 masks make auto decisions reproducibly
            # inspectable without reprocessing source media by hand.
            (args.output / "auto_base.cells").write_bytes(
                np.asarray(auto_profile.base_cells, dtype=np.uint8).tobytes())
            (args.output / "auto_frame.cells").write_bytes(
                np.asarray(auto_profile.frame_cells, dtype=np.uint8).tobytes())
            (args.output / "auto_foreground.cells").write_bytes(
                np.asarray(auto_profile.foreground_cells, dtype=np.uint8).tobytes())
            print(f"auto mode selected {auto_profile.report['plate_cells']} stable flat cells; "
                  f"scene cuts={auto_profile.report['scene_cuts']}")
        motion_weights = [0.0] + [
            5.0 + float(np.mean(np.abs(source_rgbs[index] - source_rgbs[index - 1])))
            for index in range(1, len(source_rgbs))
        ]
        clip_remaining = args.clip_delta_bytes
        # A causal rate controller normally gives frame zero unrestricted keyframe
        # quality.  On a loop, later frames then drift away before snapping back to
        # that keyframe.  Hidden passes seed the visible pass with a converged end
        # state, making frame zero obey the same delta budget as the other frames.
        for _ in range(args.cyclic_warmup_passes):
            for index, source_path in enumerate(extracted):
                source_image = Image.open(source_path).convert("RGB")
                source_rgb = source_rgbs[index]
                stable_cells = None
                if previous_source_rgb is not None:
                    motion = np.mean(np.abs(source_rgb - previous_source_rgb), axis=2)
                    stable_cells = np.mean(motion.reshape(HEIGHT, 32, 8), axis=2) < args.background_motion_threshold
                if args.dither_mode == "sierra-lite":
                    if args.encoder == "native":
                        warm = native_sierra_encoder(
                            native_executable, native_workspace, source_rgb, previous, stable_cells,
                            brightness=args.brightness, contrast=args.contrast,
                            saturation=args.saturation, gamma=args.sierra_gamma,
                            temporal_attr_penalty=args.temporal_attr_penalty,
                            temporal_pixel_penalty=args.temporal_pixel_penalty,
                            stable_penalty_multiplier=args.background_penalty_multiplier,
                            flat_ordered_variance=args.flat_ordered_variance,
                            flat_solid_variance=args.flat_solid_variance,
                            flat_solid_background_distance=args.flat_solid_background_distance,
                            flat_solid_max_y=args.flat_solid_max_y,
                            flat_ordered_attribute=args.flat_ordered_attribute,
                            flat_ordered_mix=args.flat_ordered_mix)
                    else:
                        warm = encode_image_sierra_lite(
                            source_image, brightness=args.brightness, contrast=args.contrast,
                            saturation=args.saturation, gamma=args.sierra_gamma,
                            previous=previous, temporal_attr_penalty=args.temporal_attr_penalty,
                            temporal_pixel_penalty=args.temporal_pixel_penalty,
                            stable_cells=stable_cells,
                            stable_penalty_multiplier=args.background_penalty_multiplier,
                        )
                else:
                    warm = encode_image(
                        source_image, previous=previous if args.change_penalty > 0 else None,
                        change_penalty=args.change_penalty,
                        chroma_weight=None if args.adaptive_chroma else args.chroma_weight,
                        source_gamma=args.source_gamma, dither_strength=args.dither_strength,
                        edge_weight=args.edge_weight,
                    )
                if auto_profile is not None:
                    warm = apply_foreground_overlays(
                        warm, auto_profile.plate, auto_profile.foreground_cells[index],
                        auto_profile.adjusted_frames[index], auto_profile.reference,
                        auto_profile.persistent_reference,
                        args.auto_material_dither)
                    warm = apply_plate(warm, auto_profile.plate,
                                       auto_profile.frame_cells[index])
                    if args.auto_material_dither == "solid-dark":
                        warm = apply_solid_dark_closure(
                            warm, auto_profile.base_cells,
                            auto_profile.adjusted_frames[index], auto_profile.reference)
                if previous is not None:
                    if args.encoder == "native":
                        forced = cell_age >= args.max_cell_age if args.max_cell_age else None
                        if auto_profile is not None:
                            auto_forced = auto_profile.base_cells.reshape(-1)
                            forced = auto_forced if forced is None else forced | auto_forced
                        warm, _, _ = native_rate_control_hybrid(
                            native_executable, native_workspace, previous, warm,
                            source_rgb, args.max_hybrid_bytes, forced, args.cell_age_bonus)
                    else:
                        warm, _, _ = rate_control_hybrid(
                            previous, warm, source_rgb, args.max_hybrid_bytes)
                    if auto_profile is not None:
                        warm = apply_plate(warm, auto_profile.plate,
                                           auto_profile.frame_cells[index])
                previous = warm
                previous_source_rgb = source_rgb
        for index, source_path in enumerate(extracted):
            source_image = Image.open(source_path).convert("RGB")
            source_rgb = source_rgbs[index]
            if args.dither_mode == "sierra-lite":
                stable_cells = None
                if previous_source_rgb is not None:
                    motion = np.mean(np.abs(source_rgb - previous_source_rgb), axis=2)
                    stable_cells = np.mean(motion.reshape(HEIGHT, 32, 8), axis=2) < args.background_motion_threshold
                if args.encoder == "native":
                    frame = native_sierra_encoder(
                        native_executable, native_workspace, source_rgb, previous, stable_cells,
                        brightness=args.brightness, contrast=args.contrast,
                        saturation=args.saturation, gamma=args.sierra_gamma,
                        temporal_attr_penalty=args.temporal_attr_penalty,
                        temporal_pixel_penalty=args.temporal_pixel_penalty,
                        stable_penalty_multiplier=args.background_penalty_multiplier,
                        flat_ordered_variance=args.flat_ordered_variance,
                        flat_solid_variance=args.flat_solid_variance,
                        flat_solid_background_distance=args.flat_solid_background_distance,
                        flat_solid_max_y=args.flat_solid_max_y,
                        flat_ordered_attribute=args.flat_ordered_attribute,
                        flat_ordered_mix=args.flat_ordered_mix)
                else:
                    frame = encode_image_sierra_lite(
                        source_image, brightness=args.brightness, contrast=args.contrast,
                        saturation=args.saturation, gamma=args.sierra_gamma,
                        previous=previous, temporal_attr_penalty=args.temporal_attr_penalty,
                        temporal_pixel_penalty=args.temporal_pixel_penalty,
                        stable_cells=stable_cells,
                        stable_penalty_multiplier=args.background_penalty_multiplier,
                    )
            else:
                frame = encode_image(
                    source_image,
                    previous=previous if args.change_penalty > 0 else None,
                    change_penalty=args.change_penalty,
                    chroma_weight=None if args.adaptive_chroma else args.chroma_weight,
                    source_gamma=args.source_gamma,
                    dither_strength=args.dither_strength,
                    edge_weight=args.edge_weight,
                )
            if auto_profile is not None:
                frame = apply_foreground_overlays(
                    frame, auto_profile.plate, auto_profile.foreground_cells[index],
                    auto_profile.adjusted_frames[index], auto_profile.reference,
                    auto_profile.persistent_reference,
                    args.auto_material_dither)
                frame = apply_plate(frame, auto_profile.plate,
                                    auto_profile.frame_cells[index])
                if args.auto_material_dither == "solid-dark":
                    frame = apply_solid_dark_closure(
                        frame, auto_profile.base_cells,
                        auto_profile.adjusted_frames[index], auto_profile.reference)
            selected_cells = 6144
            hybrid_bytes = 0
            bitmap_bytes = attribute_bytes = bitmap_cells = attribute_cells = 0
            allocated_bytes = 0
            if previous is not None and args.clip_delta_bytes > 0:
                if not 0 < args.clip_bitmap_fraction < 1:
                    raise SystemExit("--clip-bitmap-fraction must be between zero and one")
                remaining_frames = len(extracted) - index
                remaining_weight = sum(motion_weights[index:])
                proportional = round(clip_remaining * motion_weights[index] / remaining_weight)
                reserve = args.clip_min_frame_bytes * (remaining_frames - 1)
                allocated_bytes = max(args.clip_min_frame_bytes, proportional)
                allocated_bytes = min(allocated_bytes, max(args.clip_min_frame_bytes, clip_remaining - reserve))
                if args.clip_max_frame_bytes > 0:
                    allocated_bytes = min(allocated_bytes, args.clip_max_frame_bytes)
                bitmap_budget = max(1, round(allocated_bytes * args.clip_bitmap_fraction))
                attribute_budget = max(1, allocated_bytes - bitmap_budget)
                frame, bitmap_bytes, attribute_bytes, bitmap_cells, attribute_cells, attribute_age = rate_control_planes(
                    previous, frame, source_rgb, bitmap_budget, attribute_budget,
                    attribute_age, args.max_attribute_age)
                hybrid_bytes = bitmap_bytes + attribute_bytes
                selected_cells = bitmap_cells + attribute_cells
                clip_remaining -= hybrid_bytes
            elif previous is not None and (args.max_bitmap_bytes > 0 or args.max_attribute_bytes > 0):
                if args.max_bitmap_bytes <= 0 or args.max_attribute_bytes <= 0:
                    raise SystemExit("separate rate control requires both plane byte budgets")
                frame, bitmap_bytes, attribute_bytes, bitmap_cells, attribute_cells, attribute_age = rate_control_planes(
                    previous, frame, source_rgb, args.max_bitmap_bytes, args.max_attribute_bytes,
                    attribute_age, args.max_attribute_age)
                hybrid_bytes = bitmap_bytes + attribute_bytes
                selected_cells = bitmap_cells + attribute_cells
            elif previous is not None and args.max_hybrid_bytes > 0:
                if args.encoder == "native":
                    candidate = frame
                    forced = cell_age >= args.max_cell_age if args.max_cell_age else None
                    if auto_profile is not None:
                        auto_forced = auto_profile.base_cells.reshape(-1)
                        forced = auto_forced if forced is None else forced | auto_forced
                    frame, hybrid_bytes, selected_cells = native_rate_control_hybrid(
                        native_executable, native_workspace, previous, candidate,
                        source_rgb, args.max_hybrid_bytes, forced, args.cell_age_bonus)
                    if args.max_cell_age:
                        pending = np.zeros(6144, dtype=bool)
                        for logical in range(6144):
                            y, xb = divmod(logical, 32); offset = screen_offset(y, xb)
                            pending[logical] = frame.attributes[offset] != candidate.attributes[offset]
                        cell_age = np.where(pending, np.minimum(cell_age + 1, 255), 0).astype(np.uint8)
                else:
                    frame, hybrid_bytes, selected_cells = rate_control_hybrid(
                        previous, frame, source_rgb, args.max_hybrid_bytes)
            prefix = args.output / f"frame_{index:05d}"
            frame.write(prefix)
            rendered = frame.render()
            rendered.save(prefix.with_name(prefix.name + "_preview.png"))
            if args.keep_source_frames:
                shutil.copy2(source_path, prefix.with_name(prefix.name + "_source.png"))
            rendered_rgb = np.asarray(rendered, dtype=np.float32)
            rgb_mse = float(np.mean((source_rgb - rendered_rgb) ** 2))
            row = {"frame": index, **change_statistics(previous, frame), "rgb_mse": f"{rgb_mse:.3f}",
                   "hybrid_bytes": hybrid_bytes, "selected_cells": selected_cells}
            row.update({"bitmap_bytes": bitmap_bytes, "attribute_bytes": attribute_bytes,
                        "bitmap_cells": bitmap_cells, "attribute_cells": attribute_cells,
                        "allocated_bytes": allocated_bytes, "clip_bytes_remaining": clip_remaining})
            rows.append(row)
            previous = frame
            previous_source_rgb = source_rgb

    fields = list(rows[0])
    with (args.output / "statistics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    manifest = {
        "source": str(args.input.resolve()),
        "source_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "fps": args.fps,
        "start_seconds": args.start_seconds,
        "max_frames": args.max_frames,
        "source_window": resolved_source_window,
        "encoded_frames": len(rows),
        "change_penalty": args.change_penalty,
        "chroma_weight": "adaptive" if args.adaptive_chroma else args.chroma_weight,
        "source_gamma": args.source_gamma,
        "dither_strength": args.dither_strength,
        "edge_weight": args.edge_weight,
        "dither_mode": args.dither_mode,
        "encoder_backend": args.encoder,
        "auto": args.auto,
        "auto_colour_policy": args.auto_colour_policy if args.auto else None,
        "auto_material_dither": args.auto_material_dither if args.auto else None,
        "auto_analysis": "auto_analysis.json" if args.auto else None,
        "native_encoder": str(native_executable) if args.encoder == "native" else None,
        "brightness": args.brightness,
        "contrast": args.contrast,
        "saturation": args.saturation,
        "sierra_gamma": args.sierra_gamma,
        "temporal_attr_penalty": args.temporal_attr_penalty,
        "temporal_pixel_penalty": args.temporal_pixel_penalty,
        "background_motion_threshold": args.background_motion_threshold,
        "background_penalty_multiplier": args.background_penalty_multiplier,
        "flat_ordered_variance": args.flat_ordered_variance,
        "flat_solid_variance": args.flat_solid_variance,
        "flat_solid_background_distance": args.flat_solid_background_distance,
        "flat_solid_max_y": args.flat_solid_max_y,
        "auto_solid_upper_background": args.auto_solid_upper_background,
        "auto_solid_upper_max_y": args.auto_solid_upper_max_y,
        "flat_ordered_attribute": args.flat_ordered_attribute,
        "flat_ordered_mix": args.flat_ordered_mix,
        "max_hybrid_bytes": args.max_hybrid_bytes,
        "max_cell_age": args.max_cell_age,
        "cell_age_bonus": args.cell_age_bonus,
        "cyclic_warmup_passes": args.cyclic_warmup_passes,
        "max_bitmap_bytes": args.max_bitmap_bytes,
        "max_attribute_bytes": args.max_attribute_bytes,
        "max_attribute_age": args.max_attribute_age,
        "clip_delta_bytes": args.clip_delta_bytes,
        "clip_bitmap_fraction": args.clip_bitmap_fraction,
        "clip_min_frame_bytes": args.clip_min_frame_bytes,
        "clip_max_frame_bytes": args.clip_max_frame_bytes,
        "geometry": (
            "scale fill 256x192, preserve aspect ratio, center crop"
            if args.geometry == "crop"
            else "scale fit 256x192, preserve aspect ratio, black letterbox"
        ),
    }
    (args.output / "run.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    total_changed = sum(int(row["changed_plane_bytes"]) for row in rows[1:])
    delta_count = max(1, len(rows) - 1)
    mean_mse = sum(float(row["rgb_mse"]) for row in rows) / len(rows)
    print(f"encoded {len(rows)} frames at {args.fps:g} fps")
    if args.dither_mode == "sierra-lite":
        print(
            "Sierra temporal penalties: "
            f"attribute={args.temporal_attr_penalty:g}, pixel={args.temporal_pixel_penalty:g}"
        )
    else:
        print(f"temporal change penalty: {args.change_penalty:g}")
    print(f"mean raw changed plane bytes after keyframe: {total_changed / delta_count:.1f}")
    print(f"mean reconstructed RGB MSE: {mean_mse:.1f}")
    print(f"wrote {args.output / 'statistics.csv'}")
    print(f"wrote {args.output / 'run.json'}")


if __name__ == "__main__":
    main()
