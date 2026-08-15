import importlib.util
import json
import sys
from pathlib import Path


def load_cli():
    path = Path(__file__).parents[1] / "tsvideocodec.py"
    spec = importlib.util.spec_from_file_location("tsvideocodec", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_one_command_builds_both_outputs(monkeypatch, tmp_path):
    cli = load_cli()
    source = tmp_path / "input.gif"
    source.write_bytes(b"GIF89a")
    output = tmp_path / "output"
    calls = []
    monkeypatch.setattr(cli, "run", lambda script, *args: calls.append((script, args)))
    monkeypatch.setattr(sys, "argv", [
        "tsvideocodec.py", str(source), str(output), "--format", "both",
        "--fps", "12.5", "--max-frames", "8", "--geometry", "crop",
        "--encoder", "native", "--transport", "row-hybrid",
        "--pasmo", "custom-pasmo",
    ])

    cli.main()

    assert [call[0] for call in calls] == [
        "src/encoder/encode_sequence.py",
        "src/encoder/pack_svd.py",
        "src/cartridge/build_cartridge.py",
        "src/player/build_video_tap.py",
    ]
    cartridge_args = calls[2][1]
    assert "--row-hybrid-updates" in cartridge_args
    assert cartridge_args[-2:] == ("--pasmo", "custom-pasmo")
    tap_args = calls[3][1]
    assert "--fps-num" in tap_args and 25 in tap_args
    assert "--fps-den" in tap_args and 2 in tap_args

    manifest = json.loads((output / "build.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "both"
    assert (manifest["fps_num"], manifest["fps_den"]) == (25, 2)
    assert manifest["artifacts"]["dck"].endswith("svd_video_64k.dck")
    assert manifest["artifacts"]["tap"].endswith("svd_video.tap")
