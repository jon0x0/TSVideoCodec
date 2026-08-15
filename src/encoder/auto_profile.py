"""Deterministic whole-clip analysis for SVD ``--auto`` mode."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from svd_ecm import ECMFrame, HEIGHT, PALETTE, attribute_colours, screen_offset


@dataclass(frozen=True)
class AutoProfile:
    reference: np.ndarray
    detail_reference: np.ndarray
    persistent_reference: np.ndarray
    adjusted_frames: np.ndarray
    base_cells: np.ndarray
    frame_cells: np.ndarray
    foreground_cells: np.ndarray
    plate: ECMFrame
    spatial_threshold: float
    match_threshold: float
    scene_cuts: tuple[int, ...]
    report: dict[str, object]


def _linear(rgb: np.ndarray) -> np.ndarray:
    value = np.asarray(rgb, dtype=np.float32) / 255.0
    return np.where(value <= 0.04045, value / 12.92,
                    ((value + 0.055) / 1.055) ** 2.4)


def adjust(rgb: np.ndarray, brightness: float, contrast: float,
           saturation: float, gamma: float) -> np.ndarray:
    value = _linear(rgb)
    value = (value - 0.5) * contrast + 0.5 + brightness
    luminance = value @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    value = luminance[..., None] + saturation * (value - luminance[..., None])
    return np.clip(value, 0, 1) ** (1.0 / gamma)


def unadjust(value: np.ndarray, brightness: float, contrast: float,
             saturation: float, gamma: float) -> np.ndarray:
    """Return sRGB bytes which reproduce an adjusted-linear auto reference."""
    linear = np.clip(np.asarray(value, dtype=np.float32), 0, 1) ** gamma
    luminance = linear @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    if saturation > 0:
        linear = luminance[..., None] + (linear - luminance[..., None]) / saturation
    linear = (linear - 0.5) / contrast + 0.5 - brightness
    linear = np.clip(linear, 0, 1)
    srgb = np.where(linear <= 0.0031308, linear * 12.92,
                    1.055 * linear ** (1.0 / 2.4) - 0.055)
    return np.clip(np.rint(srgb * 255), 0, 255).astype(np.uint8)


def _cell_mean(values: np.ndarray) -> np.ndarray:
    return values.reshape(*values.shape[:-2], 32, 8, 3).mean(axis=-2)


def _cell_variance(values: np.ndarray) -> np.ndarray:
    reshaped = values.reshape(*values.shape[:-2], 32, 8, 3)
    return np.mean((reshaped - reshaped.mean(axis=-2, keepdims=True)) ** 2,
                   axis=(-2, -1))


def _ordered_plate(reference: np.ndarray, chroma_weight: float) -> ECMFrame:
    palette = _linear(PALETTE)
    bitmap = bytearray(6144)
    attrs = bytearray(6144)
    bayer = np.array([
        [0, 48, 12, 60, 3, 51, 15, 63], [32, 16, 44, 28, 35, 19, 47, 31],
        [8, 56, 4, 52, 11, 59, 7, 55], [40, 24, 36, 20, 43, 27, 39, 23],
        [2, 50, 14, 62, 1, 49, 13, 61], [34, 18, 46, 30, 33, 17, 45, 29],
        [10, 58, 6, 54, 9, 57, 5, 53], [42, 26, 38, 22, 41, 25, 37, 21],
    ], dtype=np.float32)
    row_candidates = []
    for row in range(8):
        phases = []
        for phase in range(8):
            candidate_attrs = []; candidate_bitmaps = []; candidate_rgb = []
            order = np.argsort(np.roll(bayer[row], phase))
            patterns = []
            for count in range(9):
                bits = np.zeros(8, dtype=bool); bits[order[:count]] = True
                patterns.append(bits)
            for bright in (0, 8):
                for paper_base in range(8):
                    paper = bright | paper_base
                    for ink_base in range(8):
                        ink = bright | ink_base
                        attribute = (0x40 if bright else 0) | (paper_base << 3) | ink_base
                        for bits in patterns:
                            candidate_attrs.append(attribute)
                            candidate_bitmaps.append(int(np.packbits(bits, bitorder="big")[0]))
                            candidate_rgb.append(np.where(bits[:, None], palette[ink], palette[paper]))
            phases.append((np.asarray(candidate_attrs, dtype=np.uint8),
                           np.asarray(candidate_bitmaps, dtype=np.uint8),
                           np.asarray(candidate_rgb, dtype=np.float32)))
        row_candidates.append(phases)
    for y in range(HEIGHT):
        for xb in range(32):
            phase = (xb * 3 + (y >> 3) * 5) & 7
            candidate_attrs, candidate_bitmaps, candidate_rgb = row_candidates[y & 7][phase]
            target = reference[y, xb * 8:xb * 8 + 8]
            target_mean = np.mean(target, axis=0)
            rendered_mean = np.mean(candidate_rgb, axis=1)
            residual = rendered_mean - target_mean[None, ...]
            luma = residual @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
            chroma = residual - luma[..., None]
            # Flat natural colours are otherwise biased toward neutral gray by
            # luminance-dominated RGB error. Preserve their source chroma without
            # prescribing any particular hue or semantic region type. Ordered
            # dithering is judged by its perceived mean; pattern energy is a
            # smaller secondary cost rather than per-pixel color error.
            pattern_energy = np.mean((candidate_rgb - rendered_mean[:, None, :]) ** 2,
                                     axis=(1, 2))
            error = luma * luma + chroma_weight * np.mean(chroma * chroma, axis=1)
            error += (0.015 if chroma_weight > 1 else 2.0) * pattern_energy
            best = int(np.argmin(error))
            offset = screen_offset(y, xb)
            bitmap[offset] = int(candidate_bitmaps[best]); attrs[offset] = int(candidate_attrs[best])
    return ECMFrame(bytes(bitmap), bytes(attrs))


def _complete_regions(reference: np.ndarray, base: np.ndarray, adjusted: np.ndarray,
                      frame_variance: np.ndarray, colour_threshold: float,
                      spatial_threshold: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[dict[str, object]]]:
    """Consolidate large regions and recover their temporarily occluded holes."""
    result = reference.copy()
    detail = reference.copy()
    means = _cell_mean(reference)
    frame_means = _cell_mean(adjusted)
    temporal_activity = np.mean(np.var(frame_means, axis=0), axis=-1)
    completed_base = base.copy()
    completed_active = np.zeros((len(adjusted), HEIGHT, 32), dtype=bool)
    visited = np.zeros((HEIGHT, 32), dtype=bool)
    regions = []
    for start_y, start_x in np.argwhere(base):
        if visited[start_y, start_x]:
            continue
        stack = [(int(start_y), int(start_x))]; visited[start_y, start_x] = True
        component = []
        while stack:
            y, x = stack.pop(); component.append((y, x))
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if not (0 <= ny < HEIGHT and 0 <= nx < 32) or visited[ny, nx] or not base[ny, nx]:
                    continue
                if float(np.mean((means[ny, nx] - means[y, x]) ** 2)) <= colour_threshold:
                    visited[ny, nx] = True; stack.append((ny, nx))
        if len(component) >= 32:
            colour = np.median(np.asarray([means[y, x] for y, x in component]), axis=0)
            ys = [item[0] for item in component]; xs = [item[1] for item in component]
            y0, y1, x0, x1 = min(ys), max(ys), min(xs), max(xs)
            region_distance = np.mean((frame_means - colour) ** 2, axis=-1)
            # A large flat region can contain a gentle source gradient even
            # though adjacent cells are locally similar. Compare against the
            # component median with enough headroom for that accumulated drift.
            region_match = region_distance <= colour_threshold * 6.0
            # One directly observed, spatially flat match is sufficient to
            # recover plate behind fast motion in a short clip. Requiring a
            # percentage of frames leaves persistent trails when an object
            # exposes a cell for only one or two frames.
            eligible = np.any(region_match, axis=0)
            bounds = np.zeros((HEIGHT, 32), dtype=bool); bounds[y0:y1 + 1, x0:x1 + 1] = True
            eligible &= bounds
            observed_eligible = eligible.copy()
            for y, x in component:
                eligible[y, x] = True
                # Proven flat seed cells share the robust region colour. This
                # removes source/dither fluctuations from a static plate. Do
                # not apply this to all eligible cells: inferred edge cells
                # may carry real geometry such as diagonal region boundaries.
                result[y, x * 8:x * 8 + 8] = colour
            # Cells directly observed as this flat colour in multiple source
            # frames are equally safe to consolidate (for example a background
            # exposed after a foreground object moves). This precedes inferred-hole
            # completion so real geometric boundary cells remain untouched.
            for y, x in np.argwhere(observed_eligible):
                result[y, x * 8:x * 8 + 8] = colour
            # A compact object may cover the same cells for the whole clip, so
            # temporal sampling never reveals the plate beneath it. Complete
            # only small holes enclosed within the large region; large subjects
            # remain outside this simplified overlay path.
            holes = bounds & ~eligible
            hole_seen = np.zeros((HEIGHT, 32), dtype=bool)
            completed_holes = 0
            for hole_y, hole_x in np.argwhere(holes):
                if hole_seen[hole_y, hole_x]:
                    continue
                stack_holes = [(int(hole_y), int(hole_x))]
                hole_seen[hole_y, hole_x] = True; hole_component = []
                while stack_holes:
                    hy, hx = stack_holes.pop(); hole_component.append((hy, hx))
                    for ny, nx in ((hy - 1, hx), (hy + 1, hx), (hy, hx - 1), (hy, hx + 1)):
                        if (0 <= ny < HEIGHT and 0 <= nx < 32 and holes[ny, nx] and
                                not hole_seen[ny, nx]):
                            hole_seen[ny, nx] = True; stack_holes.append((ny, nx))
                # Only infer plate behind a genuinely enclosed, temporally
                # changing occluder. A small static island can be a real edge
                # (for example a diagonal boundary in a patterned region), and filling it
                # destroys geometry. Components touching the region bounds are
                # exposed boundaries rather than enclosed holes.
                touches_bounds = any(hy in (y0, y1) or hx in (x0, x1)
                                     for hy, hx in hole_component)
                moving_cells = [(hy, hx) for hy, hx in hole_component
                                if temporal_activity[hy, hx] > colour_threshold * 0.20]
                # A fast small object may expose only one cell strongly in a
                # short loop. Any convincing temporal activity is enough;
                # truly static geometric islands still have none.
                has_motion = bool(moving_cells)
                if len(hole_component) <= 48 and not touches_bounds and has_motion:
                    for hy, hx in hole_component:
                        eligible[hy, hx] = True
                        result[hy, hx * 8:hx * 8 + 8] = colour
                        detail[hy, hx * 8:hx * 8 + 8] = colour
                    completed_holes += len(hole_component)
            completed_base |= eligible
            completed_active |= region_match & eligible[None, ...]
            regions.append({"seed_cells": len(component), "completed_cells": int(np.count_nonzero(eligible)),
                            "compact_hole_cells": completed_holes,
                            "bounds": [y0, x0, y1, x1],
                            "source_linear_rgb": [float(item) for item in colour]})
    # Small regions retain the original per-location reference behavior.
    residual = completed_base & ~np.any(completed_active, axis=0)
    local_distance = np.mean((frame_means - _cell_mean(reference)) ** 2, axis=-1)
    completed_active |= ((local_distance <= colour_threshold) & residual[None, ...] &
                         (frame_variance <= spatial_threshold * 2.0))
    return result, detail, completed_base, completed_active, sorted(
        regions, key=lambda item: int(item["seed_cells"]), reverse=True)


def analyze(frames: list[np.ndarray], *, brightness: float, contrast: float,
            saturation: float, gamma: float, colour_policy: str = "faithful") -> AutoProfile:
    adjusted = np.stack([adjust(frame, brightness, contrast, saturation, gamma)
                         for frame in frames])
    reference = np.median(adjusted, axis=0)
    persistent_reference = np.quantile(adjusted, 0.70, axis=0).astype(np.float32)
    reference_cells = _cell_mean(reference)
    means = _cell_mean(adjusted)
    distances = np.mean((means - reference_cells) ** 2, axis=-1)
    reference_variance = _cell_variance(reference)
    spatial_threshold = max(0.001, float(np.percentile(reference_variance, 45)) * 2.0)
    noise_floor = float(np.percentile(distances, 35))
    match_threshold = max(0.0015, noise_floor * 6.0)
    matches = distances <= match_threshold
    temporal_activity = np.mean(np.var(means, axis=0), axis=-1)
    temporal_activity_threshold = max(0.0005, match_threshold * 0.50)
    base = (np.mean(matches, axis=0) >= 0.60) & (reference_variance <= spatial_threshold)
    frame_variance = np.stack([_cell_variance(frame) for frame in adjusted])
    active = matches & base[None, ...] & (frame_variance <= spatial_threshold * 2.0)

    transitions = np.mean((adjusted[1:] - adjusted[:-1]) ** 2, axis=(1, 2, 3))
    median = float(np.median(transitions)) if len(transitions) else 0.0
    mad = float(np.median(np.abs(transitions - median))) if len(transitions) else 0.0
    cut_threshold = median + max(6.0 * mad, median * 2.0)
    cuts = tuple(int(index + 1) for index, value in enumerate(transitions)
                 if value > cut_threshold and value > 0.01)
    plate_reference, detail_reference, base, completed_active, regions = _complete_regions(
        reference, base, adjusted, frame_variance,
        max(0.0025, match_threshold * 2.0), spatial_threshold)
    active |= completed_active
    chroma_weight = 8.0 if colour_policy == "faithful" else 0.75
    plate = _ordered_plate(plate_reference, chroma_weight)
    plate_achromatic = np.zeros((HEIGHT, 32), dtype=bool)
    for y in range(HEIGHT):
        for xb in range(32):
            paper, ink = attribute_colours(plate.attributes[screen_offset(y, xb)])
            plate_achromatic[y, xb] = ((paper & 7) in (0, 7) and
                                       (ink & 7) in (0, 7))
    frame_luma = means @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    frame_chroma = np.max(means, axis=-1) - np.min(means, axis=-1)
    dark_material_foreground = (base[None, ...] & plate_achromatic[None, ...] &
                                (temporal_activity[None, ...] > temporal_activity_threshold) &
                                (frame_luma > 0.055) & (frame_luma < 0.22))
    active &= ~dark_material_foreground
    report = {
        "version": 2,
        "method": "robust temporal median plus adaptive flat/stable cell classification",
        "colour_policy": colour_policy,
        "plate_chroma_weight": chroma_weight,
        "frame_count": len(frames),
        "spatial_variance_threshold": spatial_threshold,
        "background_match_threshold": match_threshold,
        "temporal_activity_threshold": temporal_activity_threshold,
        "plate_cells": int(np.count_nonzero(base)),
        "plate_fraction": float(np.mean(base)),
        "mean_active_plate_cells": float(np.mean(np.count_nonzero(active, axis=(1, 2)))),
        "mean_dark_material_foreground_cells": float(
            np.mean(np.count_nonzero(dark_material_foreground, axis=(1, 2)))),
        "completed_regions": regions,
        "scene_cuts": list(cuts),
        "scene_cut_threshold": cut_threshold,
        "tone": {"brightness": brightness, "contrast": contrast,
                 "saturation": saturation, "gamma": gamma},
        "decisions": [
            "ordered plate colors fitted from adjusted source pixels using legal ECM pairs",
            "Sierra candidate retained outside automatically classified plate cells",
            "plate restored when current source returns close to robust reference",
        ],
    }
    foreground = base[None, ...] & ~active
    report["mean_foreground_overlay_cells"] = float(
        np.mean(np.count_nonzero(foreground, axis=(1, 2))))
    return AutoProfile(plate_reference, detail_reference, persistent_reference,
                       adjusted, base, active, foreground, plate, spatial_threshold,
                       match_threshold, cuts, report)


def apply_plate(frame: ECMFrame, plate: ECMFrame, cells: np.ndarray) -> ECMFrame:
    bitmap = bytearray(frame.bitmap); attrs = bytearray(frame.attributes)
    for y, xb in np.argwhere(cells):
        offset = screen_offset(int(y), int(xb))
        bitmap[offset] = plate.bitmap[offset]; attrs[offset] = plate.attributes[offset]
    return ECMFrame(bytes(bitmap), bytes(attrs))


def solidify_upper_background(profile: AutoProfile, colour: str,
                              max_y: int) -> AutoProfile:
    """Replace stable neutral upper-background cells with one legal ECM colour."""
    palette_index = {"blue": 9, "light-blue": 14}[colour]
    if colour == "light-blue":
        # Bright cyan/white ordered dither is the closest stable light-blue
        # impression available from the fixed TS2068 palette.
        attribute = 0x40 | (6 << 3) | 7
    else:
        bright = 0x40 if palette_index & 8 else 0
        base_colour = palette_index & 7
        attribute = bright | (base_colour << 3) | base_colour
    means = _cell_mean(profile.reference)
    luma = means @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    rows = np.arange(HEIGHT)[:, None]
    selected = (profile.base_cells & (rows < max_y) &
                (luma >= 0.12) & (luma <= 0.90))
    bitmap = bytearray(profile.plate.bitmap)
    attrs = bytearray(profile.plate.attributes)
    for y, xb in np.argwhere(selected):
        offset = screen_offset(int(y), int(xb))
        bitmap[offset] = ((0x55 if int(y) & 1 else 0xAA)
                          if colour == "light-blue" else 0)
        attrs[offset] = attribute
    report = dict(profile.report)
    report["solid_upper_background"] = {
        "colour": colour, "palette_index": palette_index,
        "max_y": max_y, "cells": int(np.count_nonzero(selected)),
        "selection": "stable non-black upper-background cells",
    }
    return AutoProfile(profile.reference, profile.detail_reference,
                       profile.persistent_reference, profile.adjusted_frames,
                       profile.base_cells, profile.frame_cells,
                       profile.foreground_cells, ECMFrame(bytes(bitmap), bytes(attrs)),
                       profile.spatial_threshold, profile.match_threshold,
                       profile.scene_cuts, report)


def apply_solid_dark_closure(frame: ECMFrame, cells: np.ndarray,
                             source_adjusted: np.ndarray,
                             background_reference: np.ndarray) -> ECMFrame:
    """Final post-plate black silhouette closure with blue-detail retention."""
    bitmap = bytearray(frame.bitmap); attrs = bytearray(frame.attributes)
    weights = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    for y, xb in np.argwhere(cells):
        y = int(y); xb = int(xb); offset = screen_offset(y, xb)
        target = source_adjusted[y, xb * 8:xb * 8 + 8]
        reference = background_reference[y, xb * 8:xb * 8 + 8]
        occupied = np.mean((target - reference) ** 2, axis=1) > 0.01
        if np.count_nonzero(occupied) < 4:
            continue
        mean = np.mean(target[occupied], axis=0)
        mean_luma = float(mean @ weights)
        mean_chroma = float(np.max(mean) - np.min(mean))
        blue = (occupied & (target[:, 2] > target[:, 0] + 0.04) &
                (target[:, 2] > target[:, 1] + 0.04))
        if mean_luma >= 0.45 or (mean_chroma >= 0.16 and not np.any(blue)):
            continue
        if np.count_nonzero(occupied) == 8:
            attrs[offset] = 1 if np.any(blue) else 0
            bitmap[offset] = int(np.packbits(blue, bitorder="big")[0])
        else:
            # Preserve the existing pair at boundaries. Only source-occupied
            # pixels switch to its darker endpoint, preserving the region outline.
            paper, ink = attribute_colours(attrs[offset])
            paper_luma = float(_linear(PALETTE[paper]) @ weights)
            ink_luma = float(_linear(PALETTE[ink]) @ weights)
            bits = np.unpackbits(np.asarray([bitmap[offset]], dtype=np.uint8),
                                 bitorder="big").astype(bool)
            bits[occupied] = ink_luma < paper_luma
            bitmap[offset] = int(np.packbits(bits, bitorder="big")[0])
    return ECMFrame(bytes(bitmap), bytes(attrs))


def _coherent_bits(background_error: np.ndarray, foreground_error: np.ndarray,
                   transition_cost: float = 0.01) -> tuple[np.ndarray, float]:
    """Find the least-error 8-pixel assignment including a real edge cost."""
    costs = np.empty((8, 2), dtype=np.float64)
    back = np.zeros((8, 2), dtype=np.uint8)
    costs[0] = (background_error[0], foreground_error[0])
    for x in range(1, 8):
        for state, error in enumerate((background_error[x], foreground_error[x])):
            candidates = costs[x - 1] + transition_cost * (np.arange(2) != state)
            previous = int(np.argmin(candidates))
            costs[x, state] = candidates[previous] + error
            back[x, state] = previous
    state = int(np.argmin(costs[-1]))
    bits = np.zeros(8, dtype=bool)
    for x in range(7, -1, -1):
        bits[x] = bool(state)
        state = int(back[x, state]) if x else 0
    return bits, float(np.min(costs[-1]))


def _foreground_colour_error(target: np.ndarray, colour: np.ndarray,
                             chroma_weight: float = 6.0) -> np.ndarray:
    """Per-pixel error that preserves hue in dark moving foregrounds."""
    residual = target - colour
    luma = residual @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    chroma = residual - luma[:, None]
    return luma * luma + chroma_weight * np.mean(chroma * chroma, axis=1)


def apply_foreground_overlays(frame: ECMFrame, plate: ECMFrame, cells: np.ndarray,
                              source_adjusted: np.ndarray,
                              background_reference: np.ndarray,
                              persistent_reference: np.ndarray | None = None,
                              material_dither: str = "sierra-line") -> ECMFrame:
    """Encode occluders with one palette colour anchored to the static plate."""
    bitmap = bytearray(frame.bitmap); attrs = bytearray(frame.attributes)
    palette = _linear(PALETTE)
    # Recover a persistent material layer beneath transient dark occlusion.
    # A robust upper temporal quantile retains a gray/brown subject surface,
    # while consistently black structure (hair, outlines) remains black.
    luma_weights = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    if persistent_reference is not None and material_dither != "solid-dark":
        live_luma = source_adjusted @ luma_weights
        persistent_luma = persistent_reference @ luma_weights
        persistent_chroma = (np.max(persistent_reference, axis=2) -
                             np.min(persistent_reference, axis=2))
        restore = ((persistent_luma - live_luma > 0.055) &
                   (persistent_luma > 0.075) & (persistent_luma < 0.55) &
                   (persistent_chroma < 0.20))
        source_adjusted = np.where(restore[..., None], persistent_reference, source_adjusted)
    forced_colours: dict[tuple[int, int], int] = {}
    transition_costs: dict[tuple[int, int], float] = {}
    component_sizes: dict[tuple[int, int], int] = {}
    seen = np.zeros((HEIGHT, 32), dtype=bool)
    for start_y, start_x in np.argwhere(cells):
        if seen[start_y, start_x]:
            continue
        stack = [(int(start_y), int(start_x))]; seen[start_y, start_x] = True
        component = []
        while stack:
            cy, cx = stack.pop(); component.append((cy, cx))
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if (0 <= ny < HEIGHT and 0 <= nx < 32 and cells[ny, nx] and not seen[ny, nx]):
                    seen[ny, nx] = True; stack.append((ny, nx))
        component_pixels = np.concatenate([
            source_adjusted[cy, cx * 8:cx * 8 + 8] for cy, cx in component
        ])
        mean_chroma = float(np.mean(np.max(component_pixels, axis=1) -
                                   np.min(component_pixels, axis=1)))
        component_transition = 0.01 if mean_chroma > 0.25 else 0.0025
        for coordinate in component:
            transition_costs[coordinate] = component_transition
            component_sizes[coordinate] = len(component)
        if len(component) <= 32 and mean_chroma > 0.25:
            first_offset = screen_offset(*component[0])
            first_paper, first_ink = attribute_colours(plate.attributes[first_offset])
            first_bits = np.unpackbits(
                np.frombuffer(plate.bitmap[first_offset:first_offset + 1], dtype=np.uint8),
                bitorder="big")
            first_background = first_ink if int(np.count_nonzero(first_bits)) > 4 else first_paper
            bright = first_background & 8
            colour_errors = []
            for base in range(8):
                colour = bright | base; total = 0.0
                for cy, cx in component:
                    offset = screen_offset(cy, cx)
                    paper, ink = attribute_colours(plate.attributes[offset])
                    bits = np.unpackbits(
                        np.frombuffer(plate.bitmap[offset:offset + 1], dtype=np.uint8), bitorder="big")
                    background = ink if int(np.count_nonzero(bits)) > 4 else paper
                    target = source_adjusted[cy, cx * 8:cx * 8 + 8]
                    bg_error = np.sum((target - palette[background]) ** 2, axis=1)
                    fg_error = np.sum((target - palette[colour]) ** 2, axis=1)
                    total += float(np.sum(np.minimum(bg_error, fg_error)))
                colour_errors.append(total)
            selected = bright | int(np.argmin(colour_errors))
            for coordinate in component:
                forced_colours[coordinate] = selected
    for y, xb in np.argwhere(cells):
        y = int(y); xb = int(xb); offset = screen_offset(y, xb)
        target = source_adjusted[y, xb * 8:xb * 8 + 8]
        reference = background_reference[y, xb * 8:xb * 8 + 8]
        occupied = np.mean((target - reference) ** 2, axis=1) > 0.01
        # Preserve full Sierra detail only in a substantially occupied interior
        # cell. Boundary cells remain plate-anchored to prevent spill noise and
        # guarantee clean restoration after the object moves.
        if (y, xb) not in forced_colours and np.count_nonzero(occupied) >= 6:
            best_error = float("inf"); best_attribute = 0; best_bitmap = 0
            for bright in (0, 8):
                for paper_base in range(8):
                    paper = bright | paper_base
                    paper_error = _foreground_colour_error(target, palette[paper])
                    for ink_base in range(8):
                        ink = bright | ink_base
                        ink_error = _foreground_colour_error(target, palette[ink])
                        bits, error = _coherent_bits(paper_error, ink_error, 0.0025)
                        if error < best_error:
                            best_error = error
                            best_attribute = ((0x40 if bright else 0) |
                                              (paper_base << 3) | ink_base)
                            best_bitmap = int(np.packbits(bits, bitorder="big")[0])
            attrs[offset] = best_attribute
            bitmap[offset] = best_bitmap
            continue
        plate_paper, plate_ink = attribute_colours(plate.attributes[offset])
        plate_bits = np.unpackbits(np.frombuffer(plate.bitmap[offset:offset + 1], dtype=np.uint8),
                                   bitorder="big")
        background = plate_ink if int(np.count_nonzero(plate_bits)) > 4 else plate_paper
        bright = background & 8
        best_error = float("inf"); best_colour = background; best_bits = np.zeros(8, dtype=bool)
        choices = ([forced_colours[(y, xb)]] if (y, xb) in forced_colours
                   else [bright | base for base in range(8)])
        for colour in choices:
            if (y, xb) in forced_colours or component_sizes.get((y, xb), 0) <= 64:
                background_error = np.sum((target - palette[background]) ** 2, axis=1)
                foreground_error = np.sum((target - palette[colour]) ** 2, axis=1)
            else:
                background_error = _foreground_colour_error(target, palette[background])
                foreground_error = _foreground_colour_error(target, palette[colour])
            # Optimize the bits with the transition cost included. Previously
            # the cost was added only after choosing nearest-colour pixels, so
            # it could select a colour but could not actually remove spill.
            bits, error = _coherent_bits(background_error, foreground_error,
                                         transition_costs.get((y, xb), 0.0025))
            if error < best_error:
                best_error = error; best_colour = colour; best_bits = bits
        attrs[offset] = (0x40 if bright else 0) | ((background & 7) << 3) | (best_colour & 7)
        bitmap[offset] = int(np.packbits(best_bits, bitorder="big")[0])

    # Second-stage row-context correction for dark chromatic interiors. The
    # byte-local optimizer can choose black/gray independently in adjacent
    # bytes, producing long achromatic bars even though the source has a clear
    # hue. Refit only completely occupied foreground bytes whose current pair
    # is achromatic; this deliberately excludes chromatic or high-luminance edges.
    bayer = np.array((0, 4, 1, 5, 2, 6, 3, 7), dtype=np.int8)
    for y, xb in np.argwhere(cells):
        y = int(y); xb = int(xb); offset = screen_offset(y, xb)
        target = source_adjusted[y, xb * 8:xb * 8 + 8]
        reference = background_reference[y, xb * 8:xb * 8 + 8]
        occupied = np.mean((target - reference) ** 2, axis=1) > 0.01
        occupied_count = int(np.count_nonzero(occupied))
        if material_dither == "solid-dark":
            if occupied_count != 8:
                continue
            target_luma_pixels = target @ luma_weights
            if float(np.mean(target_luma_pixels[occupied])) >= 0.24:
                continue
            blue = (occupied & (target[:, 2] > target[:, 0] + 0.04) &
                    (target[:, 2] > target[:, 1] + 0.04))
            attrs[offset] = 1 if np.any(blue) else 0
            bitmap[offset] = int(np.packbits(blue, bitorder="big")[0])
            continue
        if occupied_count != 8:
            continue
        paper, ink = attribute_colours(attrs[offset])
        if (paper & 7) not in (0, 7) or (ink & 7) not in (0, 7):
            continue
        target_mean = np.mean(target, axis=0)
        target_luma = float(target_mean @ luma_weights)
        target_chroma = float(np.max(target_mean) - np.min(target_mean))
        if target_luma <= 0.055 or target_luma >= 0.22:
            continue
        best_error = float("inf"); best_base = 0; best_count = 0
        # Low-chroma shell material is represented as sparse gray. Chromatic
        # material retains a denser red/blue/yellow endpoint as appropriate.
        # Sierra-Line-style material synthesis: moderately chromatic dark
        # material alternates a gray pair with a chromatic pair on adjacent
        # scanlines. Strong chroma (for example a blue eye or red clothing)
        # stays chromatic, and neutral material stays gray.
        line_gray = (target_chroma <= 0.008 or
                     (material_dither == "sierra-line" and
                      target_chroma < 0.14 and (y & 1) == 0))
        bases = (7,) if line_gray else range(1, 7)
        counts = range(1, 4) if line_gray else range(2, 5)
        for base in bases:
            for count in counts:
                rendered_mean = palette[base] * (count / 8.0)
                error = float(_foreground_colour_error(
                    target_mean[None, :], rendered_mean, 18.0)[0])
                # Keep the correction sparse: its purpose is perceived dark
                # chroma, not a bright colored patch inside the silhouette.
                error += count * 0.0001
                if error < best_error:
                    best_error = error; best_base = base; best_count = count
        # Put chroma on the source pixels most representative of the coloured
        # material instead of scattering it through the dark face opening.
        # A small ordered phase term gives deterministic tie-breaking without
        # overriding source structure.
        phase = (xb * 3 + y * 5) & 7
        if material_dither == "ordered-bayer":
            bits = np.roll(bayer, phase) < best_count
        else:
            black_error = _foreground_colour_error(target, palette[0])
            colour_error = _foreground_colour_error(target, palette[best_base])
            shell_score = black_error - colour_error
            shell_score += np.roll(bayer.astype(np.float32), phase) * 1.0e-5
            selected_pixels = np.argsort(shell_score)[-best_count:]
            bits = np.zeros(8, dtype=bool); bits[selected_pixels] = True
        attrs[offset] = best_base
        bitmap[offset] = int(np.packbits(bits, bitorder="big")[0])

    return ECMFrame(bytes(bitmap), bytes(attrs))
