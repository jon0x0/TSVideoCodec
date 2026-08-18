#!/usr/bin/env python3
"""One-command TSVideoCodec front end for TAP and 64 KB cartridge output."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src" / "encoder"))
sys.path.insert(0, str(Path(__file__).parent / "src"))
from audio2ay import load_sounds, parse_events
from fifo_hybrid import pack_fifo_hybrid
from keyframe_codec import encode_packbits
from svd_ecm import ECMFrame
from svd_stream import (encode_delta, encode_hybrid, encode_paired_cells,
                        encode_paired_xor_cells, encode_row_hybrid,
                        encode_sliced_paired_cells)

ROOT = Path(__file__).parent


def run(script: str, *arguments: object) -> None:
    print(f"Starting {script}...", flush=True)
    subprocess.run(
        [sys.executable, str(ROOT / script), *(str(value) for value in arguments)],
        cwd=ROOT,
        check=True,
    )


def probe_source_fps(source: Path) -> float:
    result = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=avg_frame_rate", "-of", "json", str(source),
    ], check=True, capture_output=True, text=True)
    value = json.loads(result.stdout)["streams"][0]["avg_frame_rate"]
    rate = Fraction(value)
    if rate <= 0:
        raise SystemExit(f"ffprobe reported invalid source frame rate {value}")
    return float(rate)


def stored_key_bytes(frame: ECMFrame, codec: str, *, cartridge_fifo: bool = False) -> int:
    packed = len(encode_packbits(frame.bitmap)) + len(encode_packbits(frame.attributes))
    if codec == "raw":
        return 0x3000
    # The cartridge FIFO keyframe loader currently requires both compressed
    # planes to fit in its first 8 KB bank.  build_cartridge.py therefore
    # falls back to raw storage even when packbits was explicitly requested.
    # Capacity probing must make the same decision or --fill-space can consume
    # the 3-4 KB that the real builder needs for that fallback.
    if cartridge_fifo and packed > 0x2000:
        return 0x3000
    if codec == "packbits":
        return packed
    return packed if packed + 256 < 0x3000 else 0x3000


def append_bank_local(cursor: int, sizes: list[int]) -> int:
    """Reserve records that may not cross an 8 KB cartridge payload bank."""
    for size in sizes:
        if size > 0x2000:
            return 1 << 30
        remaining = 0x2000 - (cursor % 0x2000)
        if size > remaining:
            cursor += remaining
        cursor += size
    return cursor


def output_blobs(frames: list[ECMFrame], *, tap: bool, transport: str,
                 update_slices: int, slice_order: str, bounce: bool) -> list[bytes]:
    blobs: list[bytes] = []
    for index in range(1, len(frames)):
        previous, current = frames[index - 1], frames[index]
        if tap:
            blob, _ = (encode_paired_xor_cells(previous, current) if bounce
                       else encode_delta(previous, current))
        elif update_slices > 1:
            blob, _ = encode_sliced_paired_cells(
                previous, current, update_slices, slice_order)
        elif transport == "paired":
            blob, _ = (encode_paired_xor_cells(previous, current) if bounce
                       else encode_paired_cells(previous, current))
        elif transport == "raster":
            blob, _ = encode_delta(previous, current)
        elif transport == "row-hybrid":
            blob, _ = encode_row_hybrid(previous, current)
        else:
            blob, _ = encode_hybrid(previous, current)
        blobs.append(blob)
    if not bounce:
        previous, current = frames[-1], frames[0]
        if tap or transport == "raster":
            loop, _ = encode_delta(previous, current)
        elif update_slices > 1:
            loop, _ = encode_sliced_paired_cells(
                previous, current, update_slices, slice_order)
        elif transport == "paired":
            loop, _ = encode_paired_cells(previous, current)
        elif transport == "row-hybrid":
            loop, _ = encode_row_hybrid(previous, current)
        else:
            loop, _ = encode_hybrid(previous, current)
        blobs.append(loop)
    return blobs


def output_capacity(frames: list[ECMFrame], *, target: str, key_codec: str,
                    transport: str, fifo_packing: bool, update_slices: int,
                    slice_order: str, bounce: bool,
                    audio_sizes: list[int] | None = None) -> dict[str, int | bool]:
    audio_sizes = audio_sizes or []
    tap = target == "tap"
    key_bytes = stored_key_bytes(
        frames[0], key_codec,
        cartridge_fifo=not tap and transport == "hybrid" and fifo_packing)
    blobs = output_blobs(frames, tap=tap, transport=transport,
                         update_slices=update_slices, slice_order=slice_order,
                         bounce=bounce)
    capacity = (0xE000 - 0x7800 - 1024) if tap else 7 * 0x2000
    if not tap and transport == "hybrid" and fifo_packing:
        cursor = append_bank_local(key_bytes, audio_sizes)
        for blob in blobs:
            cursor += len(pack_fifo_hybrid(blob, cursor))
        used = cursor
        largest = 0
    else:
        event_overhead = ((2 * len(audio_sizes) +
                           (2 * len(frames) - 2 if bounce else len(frames)))
                          if tap and audio_sizes else 0)
        used = key_bytes + sum(map(len, blobs)) + sum(audio_sizes) + event_overhead
        largest = 0 if tap else max([*map(len, blobs), *audio_sizes], default=0)
    fits = used <= capacity and (tap or largest <= 0x2000)
    return {"fits": fits, "used": used, "capacity": capacity,
            "key_bytes": key_bytes, "largest": largest}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a GIF or video directly to a TS2068 TAP or cartridge")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path,
                        help="generated working directory and artifact destination")
    parser.add_argument("--format", choices=("cartridge", "tap", "both"),
                        default="cartridge")
    parser.add_argument("--video-mode", choices=("ecm", "attr-32x24", "attr-32x192"),
                        default="ecm",
                        help="full ECM, SVD-ATTR 32x24, or attribute-only 32x192")
    parser.add_argument("--fps", type=float,
                        help="output cadence; defaults to source cadence with --fill-space, otherwise 12")
    parser.add_argument("--max-frames", type=int, default=12,
                        help="zero selects every frame; positive limits extraction before ECM encoding")
    parser.add_argument("--frame-selection", choices=("first", "even"), default="first",
                        help="take the first N samples or spread N evenly across the complete clip")
    parser.add_argument("--bounce", action="store_true",
                        help="play forward then reverse without duplicate endpoints")
    parser.add_argument("--start-seconds", type=float, default=0.0)
    parser.add_argument("--geometry", choices=("fit", "crop"), default="fit")
    source_window = parser.add_mutually_exclusive_group()
    source_window.add_argument(
        "--source-window", metavar="X,Y,RIGHT",
        help="normalized upper-left X,Y and right edge; viewport is 4:3")
    source_window.add_argument(
        "--source-window-pixels", metavar="X,Y,WIDTH",
        help="pixel upper-left X,Y and maximum width; viewport is 4:3")
    parser.add_argument("--encoder", choices=("python", "native"), default="python")
    parser.add_argument("--quality", type=float, default=100.0,
                        help="rate quality 0-100; 100 preserves unrestricted ECM frames")
    parser.add_argument("--max-hybrid-bytes", type=int, default=0,
                        help="expert per-frame reconstructed delta ceiling; zero disables")
    parser.add_argument("--clip-delta-bytes", type=int, default=0,
                        help="total byte budget shared across all non-key frames")
    parser.add_argument("--clip-min-frame-bytes", type=int, default=200)
    parser.add_argument("--clip-max-frame-bytes", type=int, default=0)
    parser.add_argument("--keyframe-codec", choices=("raw", "packbits", "auto"),
                        default="auto", help="cartridge initial-frame storage")
    parser.add_argument("--dither-mode", choices=("sierra-lite", "legacy"),
                        default="sierra-lite")
    parser.add_argument("--temporal-attr-penalty", type=float, default=0.01)
    parser.add_argument("--temporal-pixel-penalty", type=float, default=0.01)
    parser.add_argument("--auto", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--auto-colour-policy", "--auto-color-policy",
                        choices=("faithful", "quiet"), default="faithful")
    parser.add_argument("--auto-plate-encoder",
                        choices=("sierra-structure", "sierra-texture", "sierra-hybrid",
                                 "sierra", "ordered"), default="ordered")
    parser.add_argument("--auto-material-dither",
                        choices=("sierra-line", "shell-aware", "ordered-bayer", "solid-dark"),
                        default="sierra-line")
    parser.add_argument("--auto-static-plate", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="apply auto-detected persistent regions; disable to diagnose false static masks")
    parser.add_argument("--background-motion-threshold", type=float, default=8.0)
    parser.add_argument("--background-penalty-multiplier", type=float, default=4.0)
    parser.add_argument("--clean-cell-error", type=float, default=0.0,
                        help="suppress Sierra noise in cells already represented well by their ECM colours")
    parser.add_argument("--native-colour-snap-error", "--native-color-snap-error", type=float, default=0.0,
                        help="snap near-native pixels without disabling Sierra diffusion for intermediate colours")
    parser.add_argument("--max-cell-age", type=int, default=None,
                        help="force deferred bitmap/colour restoration after this many frames; "
                             "defaults to 4 for native --auto, 0 disables")
    parser.add_argument("--cell-age-bonus", type=int, default=250000)
    parser.add_argument("--transport",
                        choices=("hybrid", "paired", "row-hybrid", "raster"),
                        default="paired",
                        help="cartridge update transport")
    parser.add_argument("--update-slices", type=int, choices=(1, 2), default=1,
                        help="spread paired cartridge updates over two 60 Hz ticks")
    parser.add_argument("--slice-order", choices=("interlaced", "bands"),
                        default="interlaced",
                        help="two-slice intermediate layout")
    parser.add_argument("--fifo-packing", action="store_true",
                        help="pack hybrid cartridge deltas contiguously across banks")
    parser.add_argument("--fill-space", action="store_true",
                        help="maximize quality within selected TAP/cartridge capacity")
    parser.add_argument("--fit-cartridge", dest="fill_space", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--reuse-sequence", action="store_true",
                        help="reuse output/sequence ECM frames and resume fitting/packaging")
    parser.add_argument("--loop", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--loop-transition", choices=("delta", "keyframe"), default="delta",
                        help="last-to-first delta or replay the original keyframe")
    parser.add_argument("--loop-pause-frames", type=int, default=0)
    parser.add_argument("--pasmo", default=None,
                        help="Pasmo executable; defaults to PASMO or PATH")
    parser.add_argument("--audio2ay", "--audio2ay-sound", action="append", type=Path,
                        default=[], metavar="FILE",
                        help="add an audio2ay .dat sound (repeatable; indices are zero-based)")
    parser.add_argument("--audio2ay-play", action="append", default=[],
                        metavar="FRAME:SOUND_INDEX",
                        help="trigger a loaded sound on a zero-based playback frame")
    args = parser.parse_args()

    if args.max_cell_age is None:
        args.max_cell_age = 4 if args.encoder == "native" and args.auto else 0

    if args.fps is not None and args.fps <= 0:
        parser.error("--fps must be positive")
    if args.max_frames < 0:
        parser.error("--max-frames cannot be negative")
    if args.frame_selection == "even" and args.max_frames <= 0:
        parser.error("--frame-selection even requires a positive --max-frames")
    if args.max_hybrid_bytes < 0:
        parser.error("--max-hybrid-bytes cannot be negative")
    if not 0 < args.quality <= 100:
        parser.error("--quality must be greater than zero and at most 100")
    if args.quality < 100 and (args.max_hybrid_bytes or args.clip_delta_bytes):
        parser.error("--quality below 100 cannot be combined with explicit byte budgets")
    if not 0 <= args.max_cell_age <= 255:
        parser.error("--max-cell-age must be between zero and 255")
    if args.max_cell_age and args.encoder != "native":
        parser.error("--max-cell-age currently requires --encoder native")
    if args.clean_cell_error < 0:
        parser.error("--clean-cell-error cannot be negative")
    if args.clean_cell_error and args.encoder != "native":
        parser.error("--clean-cell-error currently requires --encoder native")
    if args.native_colour_snap_error < 0:
        parser.error("--native-colour-snap-error cannot be negative")
    if args.native_colour_snap_error and args.encoder != "native":
        parser.error("--native-colour-snap-error currently requires --encoder native")
    if args.clip_delta_bytes < 0 or args.clip_min_frame_bytes < 1 or args.clip_max_frame_bytes < 0:
        parser.error("clip byte budgets must be non-negative and minimum must be positive")
    if args.clip_delta_bytes and args.max_hybrid_bytes:
        parser.error("--clip-delta-bytes and --max-hybrid-bytes are mutually exclusive")
    if args.format in ("tap", "both") and not args.loop:
        parser.error("the current TAP player is looping; --no-loop is cartridge-only")
    if args.format in ("tap", "both") and args.loop_transition == "keyframe":
        parser.error("--loop-transition keyframe is currently cartridge-only")
    if args.fifo_packing and args.transport != "hybrid":
        parser.error("--fifo-packing currently requires --transport hybrid")
    if args.fill_space and args.loop_transition != "delta":
        parser.error("--fill-space requires --loop-transition delta")
    if args.bounce and not args.loop:
        parser.error("--bounce requires looping playback")
    if args.bounce and args.loop_transition != "delta":
        parser.error("--bounce uses reversible deltas and requires --loop-transition delta")
    if args.audio2ay_play and not args.audio2ay:
        parser.error("--audio2ay-play requires at least one --audio2ay sound")
    try:
        audio_sounds = load_sounds(args.audio2ay)
    except ValueError as error:
        parser.error(str(error))
    if len(audio_sounds) > 255:
        parser.error("at most 255 audio2ay sounds can be packed")

    source = args.input.resolve()
    if not source.is_file():
        parser.error(f"input does not exist: {source}")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sequence = output / "sequence"
    stream = output / "video.svd"
    effective_fps = (probe_source_fps(source) if args.fill_space and args.fps is None
                     else args.fps if args.fps is not None else 12.0)
    if args.update_slices > 1:
        if args.format != "cartridge" or args.transport != "paired":
            parser.error("--update-slices 2 currently requires cartridge --transport paired")
        if args.bounce:
            parser.error("--update-slices 2 does not yet support bounce playback")
        if effective_fps > 30:
            parser.error("--update-slices 2 requires an output rate of 30 fps or less")
    rate = Fraction(str(effective_fps)).limit_denominator(255)

    selected_max_frames = args.max_frames
    encoder_args: list[object] = [
        source, sequence,
        "--fps", effective_fps,
        "--video-mode", args.video_mode,
        "--max-frames", selected_max_frames,
        "--frame-selection", args.frame_selection,
        "--start-seconds", args.start_seconds,
        "--geometry", args.geometry,
        "--encoder", args.encoder,
        "--dither-mode", args.dither_mode,
        "--temporal-attr-penalty", args.temporal_attr_penalty,
        "--temporal-pixel-penalty", args.temporal_pixel_penalty,
        "--auto-colour-policy", args.auto_colour_policy,
        "--auto-plate-encoder", args.auto_plate_encoder,
        "--auto-material-dither", args.auto_material_dither,
        "--background-motion-threshold", args.background_motion_threshold,
        "--background-penalty-multiplier", args.background_penalty_multiplier,
        "--clean-cell-error", args.clean_cell_error,
        "--native-colour-snap-error", args.native_colour_snap_error,
        "--max-cell-age", args.max_cell_age,
        "--cell-age-bonus", args.cell_age_bonus,
        "--max-hybrid-bytes", 0 if args.fill_space else args.max_hybrid_bytes,
        "--quality", 100 if args.fill_space else args.quality,
    ]
    encoder_args.append("--auto-static-plate" if args.auto_static_plate else
                        "--no-auto-static-plate")
    if args.source_window:
        encoder_args += ["--source-window", args.source_window]
    elif args.source_window_pixels:
        encoder_args += ["--source-window-pixels", args.source_window_pixels]
    if args.clip_delta_bytes and not args.fill_space:
        encoder_args += ["--clip-delta-bytes", args.clip_delta_bytes,
                         "--clip-min-frame-bytes", args.clip_min_frame_bytes,
                         "--clip-max-frame-bytes", args.clip_max_frame_bytes]
    effective_auto = args.auto and args.video_mode == "ecm"
    if effective_auto:
        encoder_args.append("--auto")
    if args.reuse_sequence:
        if not list(sequence.glob("frame_*.pix")):
            parser.error(f"--reuse-sequence found no ECM frames in {sequence}")
        print(f"Reusing encoded ECM frames in {sequence}", flush=True)
    else:
        run("src/encoder/encode_sequence.py", *encoder_args)

    encoded_paths = sorted(sequence.glob("frame_*.pix"))
    playback_frame_count = (2 * len(encoded_paths) - 2 if args.bounce
                            else len(encoded_paths))
    try:
        audio_events = parse_events(
            args.audio2ay_play, len(audio_sounds), playback_frame_count)
    except ValueError as error:
        parser.error(str(error))
    audio_sizes = [len(sound.data) for sound in audio_sounds]

    calculated_clip_budget = 0
    capacity_fit_applied = False
    if args.fill_space:
        candidates = output / "sequence_candidates"
        if not args.reuse_sequence or not list(candidates.glob("frame_*.pix")):
            if candidates.exists():
                shutil.rmtree(candidates)
            shutil.copytree(sequence, candidates)
        candidate_paths = sorted(candidates.glob("frame_*.pix"))
        candidate_frames = [ECMFrame(path.read_bytes(), path.with_suffix(".atr").read_bytes())
                            for path in candidate_paths]
        if len(candidate_frames) < 2:
            parser.error("--fill-space requires at least two selected frames")
        if args.bounce and 2 * len(candidate_frames) - 2 > 255:
            parser.error("bounce playback exceeds the 255-entry cartridge frame table")

        def transport_blobs(items: list[ECMFrame], tap: bool) -> list[bytes]:
            blobs: list[bytes] = []
            for index in range(1, len(items)):
                if tap:
                    blob, _ = (encode_paired_xor_cells(items[index - 1], items[index])
                               if args.bounce else encode_delta(items[index - 1], items[index]))
                elif args.update_slices > 1:
                    blob, _ = encode_sliced_paired_cells(
                        items[index - 1], items[index], args.update_slices, args.slice_order)
                elif args.transport == "paired":
                    blob, _ = (encode_paired_xor_cells(items[index - 1], items[index])
                               if args.bounce else encode_paired_cells(items[index - 1], items[index]))
                elif args.transport == "raster":
                    blob, _ = encode_delta(items[index - 1], items[index])
                elif args.transport == "row-hybrid":
                    blob, _ = encode_row_hybrid(items[index - 1], items[index])
                else:
                    blob, _ = encode_hybrid(items[index - 1], items[index])
                blobs.append(blob)
            if not args.bounce:
                if tap or args.transport == "raster":
                    loop, _ = encode_delta(items[-1], items[0])
                elif args.update_slices > 1:
                    loop, _ = encode_sliced_paired_cells(
                        items[-1], items[0], args.update_slices, args.slice_order)
                elif args.transport == "paired":
                    loop, _ = encode_paired_cells(items[-1], items[0])
                elif args.transport == "row-hybrid":
                    loop, _ = encode_row_hybrid(items[-1], items[0])
                else:
                    loop, _ = encode_hybrid(items[-1], items[0])
                blobs.append(loop)
            return blobs

        def capacity_status(items: list[ECMFrame]) -> tuple[bool, str]:
            reports = []
            okay = True
            if args.format in ("tap", "both"):
                key_bytes = stored_key_bytes(items[0], args.keyframe_codec)
                blobs = transport_blobs(items, True)
                playback_count = 2 * len(items) - 2 if args.bounce else len(items)
                audio_overhead = (sum(audio_sizes) + 2 * len(audio_sizes) + playback_count
                                  if audio_sizes else 0)
                used = key_bytes + sum(map(len, blobs)) + audio_overhead
                capacity = 0xE000 - 0x7800 - 1024
                reports.append(f"TAP {used}/{capacity}")
                okay &= used <= capacity
            if args.format in ("cartridge", "both"):
                key_bytes = stored_key_bytes(
                    items[0], args.keyframe_codec,
                    cartridge_fifo=args.transport == "hybrid" and args.fifo_packing)
                blobs = transport_blobs(items, False)
                capacity = 7 * 0x2000
                if args.transport == "hybrid" and args.fifo_packing:
                    cursor = append_bank_local(key_bytes, audio_sizes)
                    for blob in blobs:
                        cursor += len(pack_fifo_hybrid(blob, cursor))
                    used = cursor
                    largest = 0
                else:
                    used = key_bytes + sum(map(len, blobs)) + sum(audio_sizes)
                    largest = max([*map(len, blobs), *audio_sizes], default=0)
                    okay &= largest <= 0x2000
                reports.append(f"cartridge {used}/{capacity}, largest={largest}")
                okay &= used <= capacity
            return bool(okay), "; ".join(reports)

        unrestricted_ok, unrestricted_report = capacity_status(candidate_frames)
        print(f"fill-space unrestricted probe: {unrestricted_report}", flush=True)
        full_budget = sum(len(encode_hybrid(candidate_frames[i - 1], candidate_frames[i])[0])
                          for i in range(1, len(candidate_frames)))
        upper = max(args.clip_min_frame_bytes * (len(candidate_frames) - 1),
                    round(full_budget * args.quality / 100.0))
        minimum = args.clip_min_frame_bytes * (len(candidate_frames) - 1)
        calculated_clip_budget = upper
        if not unrestricted_ok or args.quality < 100:
            capacity_fit_applied = True
            low, high, best = minimum, upper, 0
            for attempt in range(1, 11):
                if low > high:
                    break
                trial = (low + high) // 2
                run("src/encoder/fit_sequence.py", sequence, "--targets", candidates,
                    "--clip-delta-bytes", trial,
                    "--clip-min-frame-bytes", args.clip_min_frame_bytes,
                    "--clip-max-frame-bytes", args.clip_max_frame_bytes,
                    "--encoder", args.encoder,
                    "--max-cell-age", args.max_cell_age,
                    "--cell-age-bonus", args.cell_age_bonus)
                paths = sorted(sequence.glob("frame_*.pix"))
                fitted = [ECMFrame(path.read_bytes(), path.with_suffix(".atr").read_bytes())
                          for path in paths]
                fits, report = capacity_status(fitted)
                print(f"fill-space search {attempt}: budget={trial}; {report}; "
                      f"{'fits' if fits else 'too large'}", flush=True)
                if fits:
                    best = trial; low = trial + 1
                else:
                    high = trial - 1
            if not best:
                parser.error("selected frames cannot fit at the minimum quality budget")
            calculated_clip_budget = best
            run("src/encoder/fit_sequence.py", sequence, "--targets", candidates,
                "--clip-delta-bytes", best,
                "--clip-min-frame-bytes", args.clip_min_frame_bytes,
                "--clip-max-frame-bytes", args.clip_max_frame_bytes,
                "--encoder", args.encoder,
                "--max-cell-age", args.max_cell_age,
                "--cell-age-bonus", args.cell_age_bonus)
            print(f"fill-space achieved approximately {100.0 * best / full_budget:.1f}% "
                  f"of unrestricted hybrid bytes", flush=True)

    final_paths = sorted(sequence.glob("frame_*.pix"))
    final_frames = [ECMFrame(path.read_bytes(), path.with_suffix(".atr").read_bytes())
                    for path in final_paths]
    targets = (["tap"] if args.format == "tap" else ["cartridge"]
               if args.format == "cartridge" else ["tap", "cartridge"])

    def metrics_for(items: list[ECMFrame]) -> list[tuple[str, dict[str, int | bool]]]:
        return [(target, output_capacity(
            items, target=target, key_codec=args.keyframe_codec,
            transport=args.transport, fifo_packing=args.fifo_packing,
            update_slices=args.update_slices, slice_order=args.slice_order,
            bounce=args.bounce, audio_sizes=audio_sizes)) for target in targets]

    metrics = metrics_for(final_frames)
    for target, values in metrics:
        detail = f", largest record={values['largest']}" if target == "cartridge" else ""
        print(f"capacity preflight: {target} {values['used']}/{values['capacity']} bytes"
              f"{detail}", flush=True)
    if not all(bool(values["fits"]) for _, values in metrics):
        suggested_frames = 1
        for count in range(len(final_frames) - 1, 0, -1):
            if all(bool(values["fits"]) for _, values in metrics_for(final_frames[:count])):
                suggested_frames = count
                break
        ratios = []
        for target, values in metrics:
            delta_used = max(1, int(values["used"]) - int(values["key_bytes"]))
            delta_capacity = max(1, int(values["capacity"]) - int(values["key_bytes"]))
            ratio = delta_capacity / delta_used
            if target == "cartridge" and int(values["largest"]) > 0:
                ratio = min(ratio, 0x2000 / int(values["largest"]))
            ratios.append(ratio)
        estimated_quality = max(1, min(99, int(args.quality * min(ratios) * 0.97)))
        parser.error(
            "encoded output does not fit the selected format capacity. "
            f"Estimated alternatives: --max-frames {suggested_frames} at quality "
            f"{args.quality:g}, or keep all {len(final_frames)} frames with "
            f"--quality {estimated_quality}. Use --fill-space for an exact "
            "capacity search.")

    artifacts: dict[str, str] = {}
    if args.format in ("cartridge", "both"):
        run("src/encoder/pack_svd.py", sequence, stream,
            "--fps-num", rate.numerator, "--fps-den", rate.denominator,
            "--delta-format", "hybrid")
        cartridge_args: list[object] = [sequence, stream, output / "cartridge"]
        cartridge_args += ["--keyframe-codec", args.keyframe_codec]
        if args.bounce:
            cartridge_args += ["--bounce", "--loop-pause-frames",
                               args.loop_pause_frames]
        elif args.loop and args.loop_transition == "delta":
            cartridge_args += ["--seamless-loop", "--loop-pause-frames",
                               args.loop_pause_frames]
        elif not args.loop:
            cartridge_args.append("--stop-at-end")
        transport_flag = {
            "hybrid": None,
            "paired": "--paired-cell-updates",
            "row-hybrid": "--row-hybrid-updates",
            "raster": "--raster-updates",
        }[args.transport]
        if transport_flag:
            cartridge_args.append(transport_flag)
        if args.update_slices > 1:
            cartridge_args += ["--update-slices", args.update_slices,
                               "--slice-order", args.slice_order]
        if args.fifo_packing:
            cartridge_args.append("--fifo-packing")
        if args.pasmo:
            cartridge_args += ["--pasmo", args.pasmo]
        for sound in audio_sounds:
            cartridge_args += ["--audio2ay", sound.path]
        for frame, sound_index in sorted(audio_events.items()):
            cartridge_args += ["--audio2ay-play", f"{frame}:{sound_index}"]
        run("src/cartridge/build_cartridge.py", *cartridge_args)
        artifacts["dck"] = str(output / "cartridge" / "svd_video_64k.dck")
        artifacts["cartridge_bin"] = str(output / "cartridge" / "svd_video_64k.bin")

    if args.format in ("tap", "both"):
        tap_args: list[object] = [
            sequence, output / "tap",
            "--fps-num", rate.numerator, "--fps-den", rate.denominator,
            "--keyframe-codec", args.keyframe_codec,
        ]
        if args.bounce:
            tap_args.append("--bounce")
        if args.pasmo:
            tap_args += ["--pasmo", args.pasmo]
        for sound in audio_sounds:
            tap_args += ["--audio2ay", sound.path]
        for frame, sound_index in sorted(audio_events.items()):
            tap_args += ["--audio2ay-play", f"{frame}:{sound_index}"]
        run("src/player/build_video_tap.py", *tap_args)
        artifacts["tap"] = str(output / "tap" / "svd_video.tap")

    manifest = {
        "source": str(source), "format": args.format,
        "video_mode": args.video_mode,
        "fps_num": rate.numerator, "fps_den": rate.denominator,
        "max_frames": selected_max_frames, "start_seconds": args.start_seconds,
        "frame_selection": args.frame_selection,
        "bounce": args.bounce,
        "update_slices": args.update_slices,
        "slice_order": args.slice_order,
        "geometry": args.geometry, "encoder": args.encoder, "auto": effective_auto,
        "source_window": args.source_window,
        "source_window_pixels": args.source_window_pixels,
        "max_hybrid_bytes": args.max_hybrid_bytes,
        "temporal_attr_penalty": args.temporal_attr_penalty,
        "temporal_pixel_penalty": args.temporal_pixel_penalty,
        "auto_static_plate": args.auto_static_plate,
        "auto_plate_encoder": args.auto_plate_encoder,
        "auto_material_dither": args.auto_material_dither,
        "background_motion_threshold": args.background_motion_threshold,
        "clean_cell_error": args.clean_cell_error,
        "native_colour_snap_error": args.native_colour_snap_error,
        "max_cell_age": args.max_cell_age,
        "clip_delta_bytes": args.clip_delta_bytes,
        "fill_space": args.fill_space,
        "quality": args.quality,
        "reuse_sequence": args.reuse_sequence,
        "calculated_clip_delta_bytes": calculated_clip_budget,
        "capacity_fit_applied": capacity_fit_applied,
        "keyframe_codec": args.keyframe_codec,
        "transport": args.transport if args.format != "tap" else "tap-raster",
        "fifo_packing": args.fifo_packing,
        "loop": args.loop, "loop_transition": args.loop_transition,
        "audio2ay": [str(sound.path) for sound in audio_sounds],
        "audio2ay_events": {str(frame): index for frame, index in audio_events.items()},
        "sound_toggle_key": "S" if audio_sounds else None,
        "artifacts": artifacts,
    }
    (output / "build.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("completed TSVideoCodec build")
    for kind, path in artifacts.items():
        print(f"{kind}: {path}")


if __name__ == "__main__":
    main()
