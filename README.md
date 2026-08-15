# TSVideoCodec

TSVideoCodec is an experimental video encoder and Z80 player for the Timex
Sinclair 2068. Its primary profile, SVD-ECM, targets the SCLD Extended Colour
Mode: a 256x192 one-bit bitmap supplies spatial detail while 32x192 colour
attributes provide two colours for every 8x1 cell.

The encoder converts images, GIFs, and video into legal TS2068 ECM frames,
performs temporal and chroma-aware optimization, rate-controls reconstructed
deltas, and packages streams for TAP or 64 KB DCK cartridges. `--auto` analyzes
the complete clip, identifies stable background regions, preserves moving
foreground detail, and restores exposed backgrounds without manual masks.

## Repository layout

- `src/encoder/` — reference Python encoder and stream tools
- `src/native_encoder/` — portable C11 Sierra Lite encoder
- `src/player/` — Z80 RAM/TAP player and measurement tools
- `src/cartridge/` — 64 KB DCK packer and banked player
- `tests/` — deterministic codec and round-trip tests
- `docs/` — format, automatic-analysis, measurements, and implementation notes
- `demos/` — the only checked-in binary demos, plus their curated build scripts

Generated frames, streams, reports, source videos, native executables, and
assembler objects are deliberately excluded from Git.

## Requirements

- Python 3.10 or newer
- FFmpeg and `ffprobe` on `PATH`
- NumPy and Pillow
- A C11 compiler for the optional native encoder
- Pasmo 0.5.5 on `PATH` for TAP/DCK/player builds
- Fuse for automated emulator validation (optional)

Install Python dependencies:

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# Unix: source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Build the native encoder on Linux or macOS:

```sh
make -C src/native_encoder
```

On Windows, use GNU Make with GCC/Clang, or compile `svdenc.c` as documented in
[`src/native_encoder/README.md`](src/native_encoder/README.md).

## Quick start

Reference Python encoder:

```sh
python src/encoder/encode_sequence.py input.gif build/example/sequence \
  --fps 12 --max-frames 24 --geometry fit --dither-mode sierra-lite --auto
```

Native encoder:

```sh
python src/encoder/encode_sequence.py input.gif build/example/sequence \
  --encoder native --fps 12 --max-frames 24 --geometry fit \
  --dither-mode sierra-lite --auto
```

Pack the reconstructed frames into an SVD stream:

```sh
python src/encoder/pack_svd.py build/example/sequence build/example/video.svd \
  --fps-num 12 --delta-format hybrid
```

Every sequence build records source identity, frame selection, tone settings,
rate control, and automatic-analysis decisions under its output directory.

Run tests with `python -m pytest -q`.

See [Implementation](docs/IMPLEMENTATION.md), [Automatic mode](docs/AUTO_MODE.md),
and the [draft bitstream specification](docs/CODEC_SPEC_DRAFT.md).
