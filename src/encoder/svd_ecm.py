"""Exact TS2068 Extended Color Mode frame model and still-frame encoder.

Both 6144-byte planes use the ZX/Timex display-file address permutation. At a
given offset, the bitmap byte selects between colours in the attribute byte at
the same offset in the second display file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

WIDTH, HEIGHT = 256, 192
PLANE_SIZE = 6144

# Canonical digital RGB preview palette. Real displays/emulators differ in
# analogue colourimetry, so this table is deliberately centralized.
PALETTE = np.array(
    [
        [0, 0, 0], [0, 0, 205], [205, 0, 0], [205, 0, 205],
        [0, 205, 0], [0, 205, 205], [205, 205, 0], [205, 205, 205],
        [96, 96, 96], [2, 0, 253], [255, 2, 1], [255, 2, 253],
        [0, 255, 28], [2, 255, 255], [255, 255, 29], [255, 255, 255],
    ],
    dtype=np.uint8,
)


def screen_offset(y: int, x_byte: int) -> int:
    """Return the TS display-file offset for logical row and byte column."""
    if not (0 <= y < HEIGHT and 0 <= x_byte < 32):
        raise ValueError("screen coordinate out of range")
    return ((y & 0xC0) << 5) | ((y & 0x07) << 8) | ((y & 0x38) << 2) | x_byte


def _logical_to_bitmap_plane(values: np.ndarray) -> np.ndarray:
    plane = np.empty(PLANE_SIZE, dtype=np.uint8)
    for y in range(HEIGHT):
        start = screen_offset(y, 0)
        plane[start : start + 32] = values[y]
    return plane


def _bitmap_plane_to_logical(plane: np.ndarray) -> np.ndarray:
    if np.asarray(plane).size != PLANE_SIZE:
        raise ValueError("an ECM plane must contain exactly 6144 bytes")
    result = np.empty((HEIGHT, 32), dtype=np.uint8)
    flat = np.asarray(plane, dtype=np.uint8).reshape(-1)
    for y in range(HEIGHT):
        start = screen_offset(y, 0)
        result[y] = flat[start : start + 32]
    return result


def _logical_to_attribute_plane(values: np.ndarray) -> np.ndarray:
    return _logical_to_bitmap_plane(np.asarray(values, dtype=np.uint8))


def _attribute_plane_to_logical(plane: np.ndarray) -> np.ndarray:
    return _bitmap_plane_to_logical(np.asarray(plane, dtype=np.uint8))


def attribute_colours(attribute: int) -> tuple[int, int]:
    """Return palette indices (paper, ink); FLASH is intentionally ignored."""
    bright = 8 if attribute & 0x40 else 0
    return bright | ((attribute >> 3) & 7), bright | (attribute & 7)


@dataclass(frozen=True)
class ECMFrame:
    bitmap: bytes
    attributes: bytes

    def __post_init__(self) -> None:
        if len(self.bitmap) != PLANE_SIZE or len(self.attributes) != PLANE_SIZE:
            raise ValueError("bitmap and attribute planes must each be 6144 bytes")

    def render(self) -> Image.Image:
        bitmap = _bitmap_plane_to_logical(np.frombuffer(self.bitmap, dtype=np.uint8))
        attrs = _attribute_plane_to_logical(np.frombuffer(self.attributes, dtype=np.uint8))
        rgb = np.empty((HEIGHT, WIDTH, 3), dtype=np.uint8)
        for y in range(HEIGHT):
            for xb in range(32):
                paper, ink = attribute_colours(int(attrs[y, xb]))
                bits = np.unpackbits(bitmap[y, xb : xb + 1], bitorder="big")
                rgb[y, xb * 8 : xb * 8 + 8] = PALETTE[np.where(bits, ink, paper)]
        return Image.fromarray(rgb, "RGB")

    def write(self, prefix: Path) -> None:
        prefix = Path(prefix)
        prefix.parent.mkdir(parents=True, exist_ok=True)
        prefix.with_suffix(".pix").write_bytes(self.bitmap)
        prefix.with_suffix(".atr").write_bytes(self.attributes)


def encode_attribute_video(source_rgb: np.ndarray, logical_rows: int) -> ECMFrame:
    """Encode fixed-checker attribute video with 32x24 or 32x192 cells."""
    if logical_rows not in (24, 192):
        raise ValueError("attribute video rows must be 24 or 192")
    rgb = np.asarray(source_rgb, dtype=np.float32)
    if rgb.shape != (HEIGHT, WIDTH, 3):
        raise ValueError("attribute video source must be 256x192 RGB")
    targets = (rgb.reshape(24, 8, 32, 8, 3).mean(axis=(1, 3))
               if logical_rows == 24 else
               rgb.reshape(192, 32, 8, 3).mean(axis=2))
    candidate_attrs = []
    mixtures = []
    palette = PALETTE.astype(np.float32)
    for bright_index in range(2):
        base = 8 if bright_index else 0
        for paper in range(8):
            for ink in range(8):
                candidate_attrs.append((0x40 if bright_index else 0) | (paper << 3) | ink)
                mixtures.append((palette[base | paper] + palette[base | ink]) * 0.5)
    mixture_array = np.asarray(mixtures, dtype=np.float32)
    errors = np.sum((targets[:, :, None, :] - mixture_array[None, None, :, :]) ** 2,
                    axis=3)
    selected = np.asarray(candidate_attrs, dtype=np.uint8)[np.argmin(errors, axis=2)]
    if logical_rows == 24:
        selected = np.repeat(selected, 8, axis=0)
    bitmap = np.empty((HEIGHT, 32), dtype=np.uint8)
    bitmap[0::2] = 0xAA
    bitmap[1::2] = 0x55
    return ECMFrame(bytes(_logical_to_bitmap_plane(bitmap)),
                    bytes(_logical_to_attribute_plane(selected)))


def _perceptual(rgb: np.ndarray, chroma_weight: float) -> np.ndarray:
    """Map RGB to a simple Y/Cb/Cr-like space with structure-biased weights."""
    matrix = np.array(
        [[0.299, 0.587, 0.114], [-0.168736, -0.331264, 0.5], [0.5, -0.418688, -0.081312]],
        dtype=np.float32,
    )
    transformed = rgb.astype(np.float32) @ matrix.T
    return transformed * np.array([1.0, chroma_weight, chroma_weight], dtype=np.float32)


_BAYER_8X8 = (np.array(
    [
        [0, 48, 12, 60, 3, 51, 15, 63],
        [32, 16, 44, 28, 35, 19, 47, 31],
        [8, 56, 4, 52, 11, 59, 7, 55],
        [40, 24, 36, 20, 43, 27, 39, 23],
        [2, 50, 14, 62, 1, 49, 13, 61],
        [34, 18, 46, 30, 33, 17, 45, 29],
        [10, 58, 6, 54, 9, 57, 5, 53],
        [42, 26, 38, 22, 41, 25, 37, 21],
    ], dtype=np.float32
) + 0.5) / 64.0


def prepare_source(image: Image.Image, source_gamma: float = 0.8) -> np.ndarray:
    """Resize and apply a deterministic display-compensation transfer curve.

    ECM has no intermediate channel levels.  Raising dark and middle values
    before palette fitting prevents natural video from collapsing into black.
    A value of 1.0 disables the transfer curve.
    """
    if source_gamma <= 0:
        raise ValueError("source_gamma must be positive")
    source = image.convert("RGB").resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    rgb = np.asarray(source, dtype=np.float32)
    luma = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    target_luma = np.power(luma / 255.0, source_gamma) * 255.0
    # Apply one scale factor to all channels so the shadow lift preserves hue
    # instead of independently pulling R/G/B toward white.
    scale = np.divide(target_luma, luma, out=np.ones_like(luma), where=luma > 0)
    return np.clip(rgb * scale[:, :, None] + 0.5, 0, 255).astype(np.uint8)


def _srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    value = np.asarray(rgb, dtype=np.float32) / 255.0
    return np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)


def _prepare_sierra_source(
    image: Image.Image, brightness: float, contrast: float, saturation: float, gamma: float
) -> np.ndarray:
    if contrast <= 0 or saturation < 0 or gamma <= 0:
        raise ValueError("contrast and gamma must be positive; saturation must not be negative")
    source = image.convert("RGB").resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    linear = _srgb_to_linear(np.asarray(source))
    adjusted = (linear - 0.18) * contrast + 0.18 + brightness
    luma = adjusted @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    adjusted = luma[:, :, None] + (adjusted - luma[:, :, None]) * saturation
    return np.clip(np.maximum(adjusted, 0.0) ** (1.0 / gamma), 0.0, 1.0)


def encode_image_sierra_lite(
    image: Image.Image,
    brightness: float = 0.0,
    contrast: float = 1.0,
    saturation: float = 1.0,
    gamma: float = 1.0,
    serpentine: bool = True,
    previous: ECMFrame | None = None,
    temporal_attr_penalty: float = 0.0,
    temporal_pixel_penalty: float = 0.0,
    stable_cells: np.ndarray | None = None,
    stable_penalty_multiplier: float = 1.0,
) -> ECMFrame:
    """Encode with global linear-light Sierra Lite error diffusion.

    Attribute pairs are selected by the source block's squared distance to
    each legal paper/ink mixing segment. Quantization error then propagates
    across 8x1 cell boundaries using Sierra Lite's 1/2, 1/4, 1/4 kernel.
    """
    if temporal_attr_penalty < 0 or temporal_pixel_penalty < 0 or stable_penalty_multiplier < 1:
        raise ValueError("temporal penalties must not be negative")
    if stable_cells is not None and np.asarray(stable_cells).shape != (HEIGHT, 32):
        raise ValueError("stable_cells must have shape (192, 32)")
    source = _prepare_sierra_source(image, brightness, contrast, saturation, gamma)
    palette = _srgb_to_linear(PALETTE)
    candidates = []
    for bright in (0, 0x40):
        base = 8 if bright else 0
        for paper in range(8):
            for ink in range(8):
                candidates.append((bright | (paper << 3) | ink, base | paper, base | ink))
    candidate_attrs = np.array([item[0] for item in candidates], dtype=np.uint8)
    papers = palette[[item[1] for item in candidates]]
    inks = palette[[item[2] for item in candidates]]
    axes = inks - papers
    denominators = np.sum(axes * axes, axis=1)

    logical_attrs = np.empty((HEIGHT, 32), dtype=np.uint8)
    cell_papers = np.empty((HEIGHT, 32, 3), dtype=np.float32)
    cell_inks = np.empty((HEIGHT, 32, 3), dtype=np.float32)
    previous_bitmap = previous_attrs = None
    if previous is not None:
        previous_bitmap = _bitmap_plane_to_logical(np.frombuffer(previous.bitmap, dtype=np.uint8))
        previous_attrs = _attribute_plane_to_logical(np.frombuffer(previous.attributes, dtype=np.uint8))
    for y in range(HEIGHT):
        for xb in range(32):
            cell = source[y, xb * 8 : xb * 8 + 8]
            numerator = np.sum((cell[:, None, :] - papers[None, :, :]) * axes[None, :, :], axis=2)
            projection = np.divide(
                numerator, denominators[None, :], out=np.zeros_like(numerator),
                where=denominators[None, :] > 1e-12,
            )
            projection = np.clip(projection, 0.0, 1.0)
            closest = papers[None, :, :] + projection[:, :, None] * axes[None, :, :]
            errors = np.sum((cell[:, None, :] - closest) ** 2, axis=(0, 2))
            if previous_attrs is not None:
                multiplier = stable_penalty_multiplier if stable_cells is not None and stable_cells[y, xb] else 1.0
                errors += temporal_attr_penalty * multiplier * (
                    candidate_attrs != (previous_attrs[y, xb] & 0x7F)
                )
            best = int(np.argmin(errors))
            logical_attrs[y, xb] = candidate_attrs[best]
            cell_papers[y, xb] = papers[best]
            cell_inks[y, xb] = inks[best]

    work = source.copy()
    logical_bitmap = np.zeros((HEIGHT, 32), dtype=np.uint8)
    for y in range(HEIGHT):
        rtl = serpentine and (y & 1) == 1
        x_values = range(WIDTH - 1, -1, -1) if rtl else range(WIDTH)
        direction = -1 if rtl else 1
        for x in x_values:
            xb = x >> 3
            value = np.clip(work[y, x], 0.0, 1.0)
            paper = cell_papers[y, xb]
            ink = cell_inks[y, xb]
            ink_error = float(np.sum((value - ink) ** 2))
            paper_error = float(np.sum((value - paper) ** 2))
            if previous_bitmap is not None:
                multiplier = stable_penalty_multiplier if stable_cells is not None and stable_cells[y, xb] else 1.0
                old_ink = bool(previous_bitmap[y, xb] & (0x80 >> (x & 7)))
                if old_ink:
                    paper_error += temporal_pixel_penalty * multiplier
                else:
                    ink_error += temporal_pixel_penalty * multiplier
            use_ink = ink_error < paper_error
            chosen = ink if use_ink else paper
            if use_ink:
                logical_bitmap[y, xb] |= 0x80 >> (x & 7)
            error = value - chosen
            nx = x + direction
            if 0 <= nx < WIDTH:
                work[y, nx] += error * 0.5
            if y + 1 < HEIGHT:
                back = x - direction
                if 0 <= back < WIDTH:
                    work[y + 1, back] += error * 0.25
                work[y + 1, x] += error * 0.25

    return ECMFrame(
        bytes(_logical_to_bitmap_plane(logical_bitmap)),
        bytes(_logical_to_attribute_plane(logical_attrs)),
    )


def encode_image(
    image: Image.Image,
    previous: ECMFrame | None = None,
    change_penalty: float = 0.0,
    chroma_weight: float | None = 1.0,
    source_gamma: float = 0.8,
    dither_strength: float = 0.0,
    edge_weight: float = 0.0,
) -> ECMFrame:
    """Optimize every legal 8x1 ECM cell.

    When ``previous`` is supplied, scoring is against the reconstructed prior
    state. ``change_penalty`` is charged independently for changing the bitmap
    and attribute byte. The search includes retaining the previous bitmap with
    every legal attribute, which is necessary for true attribute-only choices.
    """
    if change_penalty < 0:
        raise ValueError("change_penalty must not be negative")
    if chroma_weight is not None and chroma_weight <= 0:
        raise ValueError("chroma_weight must be positive")
    if not 0 <= dither_strength <= 1:
        raise ValueError("dither_strength must be between zero and one")
    if edge_weight < 0:
        raise ValueError("edge_weight must not be negative")
    source_rgb = prepare_source(image, source_gamma)
    source_luma = source_rgb.astype(np.float32) @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    gradient_x = np.zeros_like(source_luma)
    gradient_y = np.zeros_like(source_luma)
    gradient_x[:, 1:-1] = np.abs(source_luma[:, 2:] - source_luma[:, :-2]) * 0.5
    gradient_y[1:-1, :] = np.abs(source_luma[2:, :] - source_luma[:-2, :]) * 0.5
    gradient = np.hypot(gradient_x, gradient_y)
    gradient_scale = max(1.0, float(np.percentile(gradient, 90)))
    edge_map = np.clip(gradient / gradient_scale, 0.0, 1.0)
    pixels_base = _perceptual(source_rgb, 1.0)
    palette_base = _perceptual(PALETTE, 1.0)
    logical_bitmap = np.empty((HEIGHT, 32), dtype=np.uint8)
    logical_attrs = np.empty((HEIGHT, 32), dtype=np.uint8)
    previous_bitmap = previous_attrs = None
    if previous is not None:
        previous_bitmap = _bitmap_plane_to_logical(np.frombuffer(previous.bitmap, dtype=np.uint8))
        previous_attrs = _attribute_plane_to_logical(np.frombuffer(previous.attributes, dtype=np.uint8))

    candidates = []
    for bright in (0, 0x40):
        base = 8 if bright else 0
        for paper in range(8):
            for ink in range(8):
                candidates.append((bright | (paper << 3) | ink, base | paper, base | ink))
    candidate_attrs = np.array([item[0] for item in candidates], dtype=np.uint8)
    paper_indices = [item[1] for item in candidates]
    ink_indices = [item[2] for item in candidates]

    for y in range(HEIGHT):
        for xb in range(32):
            rgb_cell = source_rgb[y, xb * 8 : xb * 8 + 8]
            if chroma_weight is None:
                saturation = float(np.mean(rgb_cell.max(axis=1).astype(np.int16) - rgb_cell.min(axis=1)))
                # Neutral material needs strong protection from complementary
                # green/magenta false color. Saturated source material retains
                # more of the original detail-favoring chroma tradeoff.
                blend = min(1.0, max(0.0, (saturation - 5.0) / 20.0))
                cell_chroma_weight = 1.0 + blend * (0.65 - 1.0)
            else:
                cell_chroma_weight = chroma_weight
            weights = np.array([1.0, cell_chroma_weight, cell_chroma_weight], dtype=np.float32)
            cell = pixels_base[y, xb * 8 : xb * 8 + 8] * weights
            palette = palette_base * weights
            paper_colours = palette[paper_indices]
            ink_colours = palette[ink_indices]
            paper_error = np.sum((cell[:, None, :] - paper_colours[None, :, :]) ** 2, axis=2)
            ink_error = np.sum((cell[:, None, :] - ink_colours[None, :, :]) ** 2, axis=2)
            # Project each pixel onto the paper-to-ink colour axis. Ordered
            # thresholds distribute intermediate levels in both dimensions;
            # this avoids the long horizontal bands produced by independent
            # nearest-colour decisions in each 8x1 cell.
            colour_axis = ink_colours - paper_colours
            denominator = np.sum(colour_axis * colour_axis, axis=1)
            numerator = np.sum(
                (cell[:, None, :] - paper_colours[None, :, :]) * colour_axis[None, :, :], axis=2
            )
            projection = np.divide(
                numerator, denominator[None, :], out=np.zeros_like(numerator), where=denominator[None, :] > 0
            )
            ordered = _BAYER_8X8[y & 7]
            cell_edges = edge_map[y, xb * 8 : xb * 8 + 8]
            thresholds = 0.5 + dither_strength * (1.0 - cell_edges) * (ordered - 0.5)
            use_ink = projection > thresholds[:, None]
            optimal_masks = np.packbits(use_ink.T, axis=1, bitorder="big")[:, 0]
            pixel_importance = 1.0 + edge_weight * cell_edges
            optimal_errors = (
                np.where(use_ink, ink_error, paper_error) * pixel_importance[:, None]
            ).sum(axis=0)

            if previous is None:
                best = int(np.argmin(optimal_errors))
                logical_bitmap[y, xb] = optimal_masks[best]
                logical_attrs[y, xb] = candidate_attrs[best]
                continue

            old_bitmap = int(previous_bitmap[y, xb])
            old_attr = int(previous_attrs[y, xb] & 0x7F)
            attr_cost = (candidate_attrs != old_attr).astype(np.float32) * change_penalty

            # Path A: use each attribute's visually optimal bitmap.
            optimal_scores = optimal_errors + attr_cost
            optimal_scores += (optimal_masks != old_bitmap).astype(np.float32) * change_penalty

            # Path B: retain the old bitmap and consider every attribute. This
            # explicitly represents unchanged and attribute-only updates.
            old_bits = np.unpackbits(np.array([old_bitmap], dtype=np.uint8), bitorder="big").astype(bool)
            fixed_errors = (
                np.where(old_bits[:, None], ink_error, paper_error) * pixel_importance[:, None]
            ).sum(axis=0)
            fixed_scores = fixed_errors + attr_cost

            best_optimal = int(np.argmin(optimal_scores))
            best_fixed = int(np.argmin(fixed_scores))
            if fixed_scores[best_fixed] <= optimal_scores[best_optimal]:
                logical_bitmap[y, xb] = old_bitmap
                logical_attrs[y, xb] = candidate_attrs[best_fixed]
            else:
                logical_bitmap[y, xb] = optimal_masks[best_optimal]
                logical_attrs[y, xb] = candidate_attrs[best_optimal]

    return ECMFrame(
        bytes(_logical_to_bitmap_plane(logical_bitmap)),
        bytes(_logical_to_attribute_plane(logical_attrs)),
    )
