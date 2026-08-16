import importlib.util
from pathlib import Path

import pytest


def load_encoder():
    path = Path(__file__).parents[1] / "src" / "encoder" / "encode_sequence.py"
    spec = importlib.util.spec_from_file_location("encode_sequence", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_normalized_window_uses_right_edge_and_derives_four_three_height():
    window = load_encoder().resolve_source_window((1920, 1080), (0.3, 0.3, 0.6), True)
    assert (window["x"], window["y"]) == (576, 324)
    assert (window["width"], window["height"]) == (576, 432)
    assert window["width_was_reduced"] is False


def test_pixel_window_uses_requested_width_when_it_fits():
    window = load_encoder().resolve_source_window((1920, 1080), (100, 100, 800), False)
    assert (window["x"], window["y"], window["width"], window["height"]) == (100, 100, 800, 600)
    assert window["width_was_reduced"] is False


def test_normalized_window_rejects_invalid_origin():
    with pytest.raises(ValueError, match="normalized"):
        load_encoder().resolve_source_window((640, 480), (1.0, 0.2, 0.5), True)
