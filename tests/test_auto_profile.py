from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parents[1] / "src" / "encoder"))

from auto_profile import (  # noqa: E402
    _coherent_bits, _ordered_plate, analyze, apply_foreground_overlays, apply_plate,
)
from svd_ecm import ECMFrame, screen_offset  # noqa: E402


def test_auto_plate_is_source_derived_and_restores_flat_cells():
    frames = []
    for x in (80, 88, 96):
        image = np.full((192, 256, 3), (170, 205, 240), dtype=np.float32)
        image[70:82, x:x + 8] = (240, 30, 20)
        frames.append(image)
    profile = analyze(frames, brightness=0, contrast=1, saturation=1, gamma=1)
    assert profile.report["plate_cells"] > 5000
    assert profile.frame_cells[:, 10, 10].all()
    assert not profile.frame_cells[0, 75, 10]

    blank = ECMFrame(bytes(6144), bytes(6144))
    restored = apply_plate(blank, profile.plate, profile.frame_cells[0])
    offset = screen_offset(10, 10)
    assert (restored.bitmap[offset], restored.attributes[offset]) == (
        profile.plate.bitmap[offset], profile.plate.attributes[offset])


def test_ordered_plate_scores_the_blended_colour_not_individual_dots():
    target = np.empty((192, 256, 3), dtype=np.float32)
    target[:] = (0.55, 0.55, 0.99)
    plate = _ordered_plate(target, 8.0)
    offset = screen_offset(40, 0)
    assert plate.bitmap[offset] not in (0x00, 0xFF)


def test_foreground_overlay_anchors_pixels_to_plate_colour():
    plate = ECMFrame(bytes(6144), bytes(6144))
    source = np.zeros((192, 256, 3), dtype=np.float32)
    source[20, 82:86] = (1, 0, 0)
    cells = np.zeros((192, 32), dtype=bool); cells[20, 10] = True
    result = apply_foreground_overlays(plate, plate, cells, source, np.zeros_like(source))
    offset = screen_offset(20, 10)
    bits = np.unpackbits(np.frombuffer(result.bitmap[offset:offset + 1], dtype=np.uint8),
                         bitorder="big")
    assert not bits[:2].any() and not bits[6:].any()
    assert bits[2:6].all()


def test_foreground_assignment_suppresses_weak_isolated_spill():
    background = np.zeros(8); foreground = np.ones(8)
    background[3] = 0.01; foreground[3] = 0.0
    bits, _ = _coherent_bits(background, foreground)
    assert not bits.any()

    background[2:6] = 1.0; foreground[2:6] = 0.0
    bits, _ = _coherent_bits(background, foreground)
    assert bits[2:6].all()
