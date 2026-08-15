import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parents[1] / "src" / "encoder"))
from svd_ecm import (
    ECMFrame, PALETTE, PLANE_SIZE, _logical_to_attribute_plane, encode_image, screen_offset,
)
from encode_sequence import change_statistics


def test_screen_offsets_cover_plane_once():
    offsets = [screen_offset(y, x) for y in range(192) for x in range(32)]
    assert len(set(offsets)) == PLANE_SIZE
    assert min(offsets) == 0 and max(offsets) == PLANE_SIZE - 1


def test_ecm_attribute_plane_uses_screen_order():
    logical = np.arange(PLANE_SIZE, dtype=np.uint16).reshape(192, 32).astype(np.uint8)
    plane = _logical_to_attribute_plane(logical)
    for y in range(192):
        assert np.array_equal(plane[screen_offset(y, 0):screen_offset(y, 0) + 32], logical[y])


def test_solid_red_round_trip():
    frame = encode_image(Image.new("RGB", (256, 192), (255, 0, 0)))
    rendered = np.asarray(frame.render())
    assert np.all(rendered == PALETTE[10])


def test_plane_lengths_and_determinism():
    image = Image.fromarray(np.tile(np.arange(256, dtype=np.uint8), (192, 1)))
    first = encode_image(image)
    second = encode_image(image)
    assert isinstance(first, ECMFrame)
    assert len(first.bitmap) == len(first.attributes) == PLANE_SIZE
    assert first == second


def test_change_categories():
    zero = ECMFrame(bytes(PLANE_SIZE), bytes(PLANE_SIZE))
    bitmap = bytearray(PLANE_SIZE)
    attrs = bytearray(PLANE_SIZE)
    bitmap[1] = 1
    attrs[2] = 2
    bitmap[3] = 3
    attrs[3] = 3
    changed = ECMFrame(bytes(bitmap), bytes(attrs))
    stats = change_statistics(zero, changed)
    assert stats["bitmap_only_cells"] == 1
    assert stats["attribute_only_cells"] == 1
    assert stats["both_cells"] == 1
    assert stats["unchanged_cells"] == PLANE_SIZE - 3
    assert stats["changed_plane_bytes"] == 4


def test_temporal_penalty_can_retain_reconstructed_state():
    old = encode_image(Image.new("RGB", (256, 192), (205, 0, 0)))
    changed_source = Image.new("RGB", (256, 192), (255, 0, 0))
    retained = encode_image(changed_source, previous=old, change_penalty=1e9)
    assert retained == old
