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

## Complete GIF/video to cartridge example

The normal interface is a single command. For a looping 64 KB cartridge:

```sh
python tsvideocodec.py input.gif build/example --format cartridge
```

For a whole-clip rather than per-frame rate limit, set
`--max-hybrid-bytes 0 --clip-delta-bytes N`. The encoder distributes `N` bytes
across all non-key frames according to source motion while retaining a minimum
allocation for every frame. `--clip-max-frame-bytes` optionally limits the
largest individual update.

Hybrid cartridge builds can additionally use `--transport hybrid
--fifo-packing`. This treats all seven media banks as one logical 57,344-byte
stream, so frames may cross bank boundaries and no capacity is lost to
whole-frame bin packing. Reserved command-boundary markers switch banks without
adding a boundary check to every compressed byte. The original bank-local path
remains the default because it is still marginally faster.

For clips whose source has an intentional discontinuity, use
`--loop-transition keyframe`. The player replays the original keyframe at the
boundary instead of storing a last-to-first delta. The default is `delta`.

The initial TAP or cartridge frame uses `--keyframe-codec auto` by default. It selects
PackBits when that meaningfully reduces the 12 KB bitmap-plus-attribute frame;
use `raw` or `packbits` to force either representation. During startup the
player clears the live attributes, decompresses the bitmap directly into ECM
screen memory, then decompresses the attributes for a controlled colour reveal.

```powershell
python tsvideocodec.py video\Kahnankas.mp4 build\kahn --format cartridge --keyframe-codec packbits
```

This performs frame extraction, automatic ECM encoding, SVD stream packing,
and cartridge assembly. The output is
`build/example/cartridge/svd_video_64k.dck`.

For a TAP instead:

```sh
python tsvideocodec.py input.gif build/example --format tap
```

The output is `build/example/tap/svd_video.tap`. Use `--format both` to produce
both outputs from the same encoded sequence.

Common options include:

```sh
python tsvideocodec.py input.mp4 build/example --format both \
  --start-seconds 3 --fps 12 --max-frames 12 --geometry crop \
  --max-hybrid-bytes 1400 --transport paired --encoder native
```

Run `python tsvideocodec.py --help` for all options. Pasmo 0.5.5 must be on
`PATH`, named by `PASMO`, or supplied with `--pasmo`.

### Equivalent individual stages

The lower-level commands remain available for experimentation and reproducible
inspection of each stage:

The following example converts `input.gif` into a seamless 64 KB cartridge.
The same command accepts formats supported by FFmpeg, including MP4, MOV, MKV,
and animated WebP. Pasmo 0.5.5 must be on `PATH`; alternatively pass its full
path with `--pasmo` or set the `PASMO` environment variable.

First generate reconstructed TS2068 frames. The byte ceiling makes a 12-frame
example reasonably likely to fit even when the source contains substantial
motion:

```sh
python src/encoder/encode_sequence.py input.gif build/example/sequence \
  --fps 12 --max-frames 12 --geometry fit \
  --dither-mode sierra-lite --auto --max-hybrid-bytes 1400
```

To use the optional native C encoder, build it first and add `--encoder native`
to the command above. The Python encoder is the portable reference default.

Pack the reconstructed frames into an SVD stream:

```sh
python src/encoder/pack_svd.py \
  build/example/sequence build/example/video.svd \
  --fps-num 12 --fps-den 1 --delta-format hybrid
```

Build a looping cartridge using raster-ordered paired bitmap/colour updates:

```sh
python src/cartridge/build_cartridge.py \
  build/example/sequence build/example/video.svd build/example/cartridge \
  --seamless-loop --loop-pause-frames 0 --paired-cell-updates
```

The runnable files are:

- `build/example/cartridge/svd_video_64k.dck` for Fuse and DCK-aware tools
- `build/example/cartridge/svd_video_64k.bin`, the exact 65,536-byte image

The packer reports the used payload and rejects an image that exceeds the seven
available media banks. If a source does not fit, reduce `--max-frames`, lower
`--max-hybrid-bytes`, reduce the sampling rate, or choose a more compact update
transport. `--paired-cell-updates` is intended to reduce visible tearing;
omitting it uses the smaller and faster hybrid-plane cartridge decoder.

For an MP4 beginning three seconds into the source, the encoding step could be:

```sh
python src/encoder/encode_sequence.py input.mp4 build/example/sequence \
  --start-seconds 3 --fps 12 --max-frames 12 --geometry crop \
  --dither-mode sierra-lite --auto --max-hybrid-bytes 1400
```

## Complete GIF/video to TAP example

Encode the source as above, then build a self-loading TAP directly from the
generated sequence:

```sh
python src/player/build_video_tap.py \
  build/example/sequence build/example/tap \
  --fps-num 12 --fps-den 1
```

This produces `build/example/tap/svd_video.tap`. Load it normally; playback
starts automatically. Pressing a key restores the original BASIC workspace,
returns to normal display mode, and returns to BASIC.

The current TAP player has a safe contiguous image budget of 26,624 bytes,
including its keyframe and player code. PackBits commonly reduces the 12,288-byte
raw keyframe by roughly half, leaving correspondingly more room for delta frames.
TAP capacity is still smaller than cartridge capacity. If the builder reports an overflow, reduce
the number of frames or use a tighter rate-control setting. The TAP builder
currently uses its raster replacement transport, while cartridge output offers
hybrid, row-hybrid, and paired-cell transports.

Every sequence build records source identity, frame selection, tone settings,
rate control, and automatic-analysis decisions under its output directory.

Run tests with `python -m pytest -q`.

See [Implementation](docs/IMPLEMENTATION.md), [Automatic mode](docs/AUTO_MODE.md),
and the [draft bitstream specification](docs/CODEC_SPEC_DRAFT.md).
