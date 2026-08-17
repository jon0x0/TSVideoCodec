import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parents[1] / "src" / "encoder"))
from svd_ecm import encode_attribute_video, screen_offset


def test_attribute_profiles_keep_a_stationary_bitmap():
    first = np.zeros((192, 256, 3), dtype=np.float32)
    second = np.full((192, 256, 3), 255, dtype=np.float32)
    for rows in (24, 192):
        a = encode_attribute_video(first, rows)
        b = encode_attribute_video(second, rows)
        assert a.bitmap == b.bitmap
        assert a.attributes != b.attributes


def test_32x24_profile_repeats_each_attribute_for_eight_scanlines():
    source = np.zeros((192, 256, 3), dtype=np.float32)
    source[:96, :, 2] = 255
    source[96:, :, 0] = 255
    frame = encode_attribute_video(source, 24)
    for block in range(24):
        for xb in range(32):
            values = {frame.attributes[screen_offset(block * 8 + row, xb)]
                      for row in range(8)}
            assert len(values) == 1
