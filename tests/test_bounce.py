import importlib.util
from pathlib import Path


def load_builder():
    path = Path(__file__).parents[1] / "src" / "cartridge" / "build_cartridge.py"
    spec = importlib.util.spec_from_file_location("build_cartridge_bounce", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def apply_indices(indices, starting_state=None):
    states = []
    state = starting_state
    for delta in indices:
        if delta == 0:
            state = 0
        else:
            assert state in (delta - 1, delta)
            state = delta if state == delta - 1 else delta - 1
        states.append(state)
    return states


def test_bounce_reuses_forward_xor_deltas_in_reverse():
    builder = load_builder()
    initial = builder.bounce_table_indices(4, True)
    loop = builder.bounce_table_indices(4, False)
    assert initial == [0, 1, 2, 3, 3, 2]
    assert loop == [1, 1, 2, 3, 3, 2]
    assert apply_indices(initial) == [0, 1, 2, 3, 2, 1]
    assert apply_indices(loop, starting_state=1) == [0, 1, 2, 3, 2, 1]
