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


def recording_run(calls):
    def run(script, *args):
        calls.append((script, args))
        if script == "src/encoder/encode_sequence.py":
            sequence = Path(args[1])
            sequence.mkdir(parents=True, exist_ok=True)
            for index in range(2):
                prefix = sequence / f"frame_{index:05d}"
                prefix.with_suffix(".pix").write_bytes(bytes(0x1800))
                prefix.with_suffix(".atr").write_bytes(bytes(0x1800))
    return run


def test_fifo_capacity_uses_raw_keyframe_when_packbits_exceeds_one_bank():
    cli = load_cli()
    # Compressible enough for normal auto-PackBits selection, but too large for
    # the cartridge FIFO keyframe loader's single-bank compressed representation.
    plane = bytes(0x800) + bytes((index & 1 for index in range(0x1000)))
    frame = cli.ECMFrame(plane, plane)
    packed = (len(cli.encode_packbits(frame.bitmap)) +
              len(cli.encode_packbits(frame.attributes)))

    assert 0x2000 < packed + 256 < 0x3000
    assert cli.stored_key_bytes(frame, "auto") == packed
    assert cli.stored_key_bytes(frame, "auto", cartridge_fifo=True) == 0x3000
    assert cli.stored_key_bytes(frame, "packbits", cartridge_fifo=True) == 0x3000


def test_one_command_builds_both_outputs(monkeypatch, tmp_path):
    cli = load_cli()
    source = tmp_path / "input.gif"
    source.write_bytes(b"GIF89a")
    output = tmp_path / "output"
    calls = []
    monkeypatch.setattr(cli, "run", recording_run(calls))
    monkeypatch.setattr(sys, "argv", [
        "tsvideocodec.py", str(source), str(output), "--format", "both",
        "--fps", "12.5", "--max-frames", "8", "--geometry", "crop",
        "--source-window", "0.3,0.3,0.6",
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
    encoder_args = calls[0][1]
    assert encoder_args[encoder_args.index("--max-hybrid-bytes") + 1] == 0
    assert encoder_args[encoder_args.index("--quality") + 1] == 100.0
    window_index = encoder_args.index("--source-window")
    assert encoder_args[window_index + 1] == "0.3,0.3,0.6"
    cartridge_args = calls[2][1]
    assert "--row-hybrid-updates" in cartridge_args
    assert cartridge_args[-2:] == ("--pasmo", "custom-pasmo")
    tap_args = calls[3][1]
    assert "--fps-num" in tap_args and 25 in tap_args
    assert "--fps-den" in tap_args and 2 in tap_args

    manifest = json.loads((output / "build.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "both"
    assert manifest["source_window"] == "0.3,0.3,0.6"
    assert (manifest["fps_num"], manifest["fps_den"]) == (25, 2)
    assert manifest["artifacts"]["dck"].endswith("svd_video_64k.dck")
    assert manifest["artifacts"]["tap"].endswith("svd_video.tap")


def test_bounce_is_a_player_option_for_cartridge_and_tap(monkeypatch, tmp_path):
    cli = load_cli()
    source = tmp_path / "input.gif"
    source.write_bytes(b"GIF89a")
    calls = []
    monkeypatch.setattr(cli, "run", recording_run(calls))
    monkeypatch.setattr(sys, "argv", [
        "tsvideocodec.py", str(source), str(tmp_path / "output"),
        "--format", "both", "--bounce",
    ])

    cli.main()

    assert "--bounce" not in calls[0][1]
    assert "--bounce" in calls[2][1]
    pause_index = calls[2][1].index("--loop-pause-frames")
    assert calls[2][1][pause_index + 1] == 0
    assert "--bounce" in calls[3][1]


def test_two_slice_updates_are_forwarded_to_paired_cartridge(monkeypatch, tmp_path):
    cli = load_cli()
    source = tmp_path / "input.gif"
    source.write_bytes(b"GIF89a")
    calls = []
    monkeypatch.setattr(cli, "run", recording_run(calls))
    monkeypatch.setattr(sys, "argv", [
        "tsvideocodec.py", str(source), str(tmp_path / "output"),
        "--format", "cartridge", "--transport", "paired",
        "--update-slices", "2", "--fps", "30",
    ])

    cli.main()

    cartridge_args = calls[2][1]
    assert "--paired-cell-updates" in cartridge_args
    slice_index = cartridge_args.index("--update-slices")
    assert cartridge_args[slice_index + 1] == 2


def test_fill_space_runs_saved_sequence_fitter(monkeypatch, tmp_path):
    cli = load_cli()
    source = tmp_path / "input.gif"
    source.write_bytes(b"GIF89a")
    output = tmp_path / "output"
    calls = []
    fit_calls = 0

    def fake_run(script, *args):
        nonlocal fit_calls
        calls.append((script, args))
        if script == "src/encoder/encode_sequence.py":
            sequence = Path(args[1])
            sequence.mkdir(parents=True, exist_ok=True)
            for index in range(7):
                prefix = sequence / f"frame_{index:05d}"
                value = 0xFF if index % 2 else 0
                prefix.with_suffix(".pix").write_bytes(bytes([value]) * 0x1800)
                prefix.with_suffix(".atr").write_bytes(bytes([value]) * 0x1800)
        elif script == "src/encoder/fit_sequence.py":
            fit_calls += 1
            if fit_calls == 1:
                return
            sequence = Path(args[0])
            for prefix in sorted(sequence.glob("frame_*.pix"))[1:]:
                prefix.write_bytes(bytes(0x1800))
                prefix.with_suffix(".atr").write_bytes(bytes(0x1800))

    monkeypatch.setattr(cli, "run", fake_run)
    monkeypatch.setattr(cli, "probe_source_fps", lambda source: 12.5)
    monkeypatch.setattr(sys, "argv", [
        "tsvideocodec.py", str(source), str(output),
        "--fill-space", "--transport", "hybrid", "--fifo-packing",
        "--encoder", "native",
    ])

    cli.main()

    scripts = [call[0] for call in calls]
    assert scripts[0] == "src/encoder/encode_sequence.py"
    assert "src/encoder/fit_sequence.py" in scripts
    assert scripts[-2:] == ["src/encoder/pack_svd.py",
                            "src/cartridge/build_cartridge.py"]
    encoder_args = calls[0][1]
    assert encoder_args[encoder_args.index("--max-frames") + 1] == 12
    fitter_args = calls[1][1]
    assert "--clip-delta-bytes" in fitter_args
    assert fitter_args[fitter_args.index("--encoder") + 1] == "native"
    manifest = json.loads((output / "build.json").read_text(encoding="utf-8"))
    assert manifest["fill_space"] is True
    assert manifest["max_frames"] == 12
    assert (manifest["fps_num"], manifest["fps_den"]) == (25, 2)
    assert manifest["calculated_clip_delta_bytes"] > 0


def test_fill_space_accepts_bounce(monkeypatch, tmp_path):
    cli = load_cli()
    source = tmp_path / "input.gif"
    source.write_bytes(b"GIF89a")
    output = tmp_path / "output"
    calls = []

    def fake_run(script, *args):
        calls.append((script, args))
        if script == "src/encoder/encode_sequence.py":
            sequence = Path(args[1]); sequence.mkdir(parents=True, exist_ok=True)
            for index in range(3):
                prefix = sequence / f"frame_{index:05d}"
                prefix.with_suffix(".pix").write_bytes(bytes([index]) * 0x1800)
                prefix.with_suffix(".atr").write_bytes(bytes(0x1800))

    monkeypatch.setattr(cli, "run", fake_run)
    monkeypatch.setattr(cli, "probe_source_fps", lambda source: 12.0)
    monkeypatch.setattr(sys, "argv", [
        "tsvideocodec.py", str(source), str(output), "--fill-space", "--bounce",
        "--transport", "hybrid", "--fifo-packing",
    ])
    cli.main()
    cartridge_args = next(args for script, args in calls
                          if script == "src/cartridge/build_cartridge.py")
    assert "--bounce" in cartridge_args
    pause_index = cartridge_args.index("--loop-pause-frames")
    assert cartridge_args[pause_index + 1] == 0


def test_audio2ay_assets_and_bounce_timeline_events_are_forwarded(monkeypatch, tmp_path):
    cli = load_cli()
    source = tmp_path / "input.gif"
    source.write_bytes(b"GIF89a")
    sounds = []
    for name in ("boingf.dat", "boingw.dat"):
        path = tmp_path / name
        path.write_bytes(bytes((1, 1, 1, 0, 10, 0xF0)))
        sounds.append(path)
    calls = []

    def fake_run(script, *args):
        calls.append((script, args))
        if script == "src/encoder/encode_sequence.py":
            sequence = Path(args[1]); sequence.mkdir(parents=True, exist_ok=True)
            for index in range(20):
                prefix = sequence / f"frame_{index:05d}"
                prefix.with_suffix(".pix").write_bytes(bytes(0x1800))
                prefix.with_suffix(".atr").write_bytes(bytes(0x1800))

    monkeypatch.setattr(cli, "run", fake_run)
    monkeypatch.setattr(sys, "argv", [
        "tsvideocodec.py", str(source), str(tmp_path / "output"), "--bounce",
        "--max-frames", "20", "--audio2ay", str(sounds[0]),
        "--audio2ay", str(sounds[1]), "--audio2ay-play", "30:1",
    ])

    cli.main()

    cartridge_args = next(args for script, args in calls
                          if script == "src/cartridge/build_cartridge.py")
    assert cartridge_args.count("--audio2ay") == 2
    assert cartridge_args[cartridge_args.index("--audio2ay-play") + 1] == "30:1"
    manifest = json.loads((tmp_path / "output" / "build.json").read_text())
    assert manifest["audio2ay_events"] == {"30": 1}
    assert manifest["sound_toggle_key"] == "S"
