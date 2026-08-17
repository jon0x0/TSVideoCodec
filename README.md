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

## Algorithm lineage

The still-image side was informed by
[Retro Pixel Converter](https://github.com/factus10/retro-pixel-converter),
particularly its TS2068 ECM treatment, linear-light colour-pair fitting, and
Sierra Lite workflow. TSVideoCodec independently implements those ideas for a
scriptable video pipeline and adds temporal reconstructed-state optimization,
byte-budget selection, hybrid XOR delta compression, banked FIFO transport,
anti-tearing Z80 playback, seamless loops, and reversible bounce playback.
See [the implementation notes](docs/IMPLEMENTATION.md#what-is-searched) for the
search algorithms and the boundary between image conversion and video coding.

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

To maximize quality within the selected output capacity, use `--fill-space`:

```sh
python tsvideocodec.py input.gif build/example --format cartridge \
  --fill-space --transport hybrid --fifo-packing --encoder native
```

The same option works for TAP, cartridge, and `--format both`. It first creates
unrestricted candidates, measures the actual keyframe, loop delta and selected
transport, then searches for the highest common reconstruction budget that
fits. `--max-frames` remains independent; use `--max-frames 0` explicitly to
select every source frame. The fitting pass does not repeat source conversion.

Without `--fill-space`, the default is `--quality 100`: no hidden per-frame
rate ceiling is applied. `--quality 1..100` is the normal explicit quality
control; lower values retain fewer, lower-value cell changes. The expert
`--max-hybrid-bytes` option remains available but has no nonzero default.
Before assembly, the front end measures the actual selected transport. If it
does not fit, it reports two estimated alternatives: the largest frame count
at the requested quality and a quality value likely to retain all selected
frames. `--fill-space` performs the exact iterative search.

If fitting or packaging fails after ECM conversion has completed, add
`--reuse-sequence` to the same command. The front end resumes from
`OUTPUT/sequence` without extracting or converting the source frames again.

Hybrid cartridge builds can additionally use `--transport hybrid
--fifo-packing`. This treats all seven media banks as one logical 57,344-byte
stream, so frames may cross bank boundaries and no capacity is lost to
whole-frame bin packing. Reserved command-boundary markers switch banks without
adding a boundary check to every compressed byte. The original bank-local path
remains the default because it is still marginally faster.

For clips whose source has an intentional discontinuity, use
`--loop-transition keyframe`. The player replays the original keyframe at the
boundary instead of storing a last-to-first delta. The default is `delta`.

For motion that should reverse smoothly at its endpoint, add `--bounce` to a
cartridge or TAP build. The player traverses the same reversible delta records forward
and backward without storing reversed frames or duplicate payload. Endpoint
frames are not repeated: playback is `0..N-1..1`, then returns seamlessly to
frame zero using the first delta in reverse. Hybrid deltas are already XOR
based. With `--transport paired`, bounce mode automatically selects paired-XOR
records so bitmap and colour remain coupled without using the non-reversible
replacement records used by ordinary forward paired playback.

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
  --quality 85 --transport paired --encoder native
```

`--max-frames N` stops extraction after N samples, so unselected frames are
not ECM-encoded. To cover the entire source duration with only N encoded
frames, add `--frame-selection even`:

```sh
python tsvideocodec.py input.mp4 build/decimated --max-frames 24 \
  --frame-selection even --fps 12 --encoder native
```

Native automatic encoding forces a bitmap or colour cell still awaiting its
target after four frames by default. Tune this with `--max-cell-age N`; zero
disables it. Persistent-region detection is separate:
`--no-auto-static-plate` disables the static plate and its foreground masks,
while `--auto-plate-encoder`, `--background-motion-threshold`, and
`--background-penalty-multiplier` tune its representation and sensitivity.

Attribute-video profiles use the same working TAP and cartridge players:

```sh
python tsvideocodec.py input.gif build/attr --video-mode attr-32x24 --encoder native
python tsvideocodec.py input.gif build/attr192 --video-mode attr-32x192 --encoder native
```

Both use a fixed checker bitmap and encode colour only. `attr-32x24` assigns
one attribute to each 8x8 block; `attr-32x192` assigns one to every 8x1 cell.

For large paired-cell updates that visibly tear, cartridge output can stage
each logical frame over two 60 Hz raster bands:

```sh
python tsvideocodec.py input.gif build/sliced --format cartridge \
  --transport paired --update-slices 2 --fps 30 --encoder native
```

The default interlaced order applies alternate scanlines, waits for the next
display tick, and then applies the remaining lines. `--slice-order bands`
selects the original upper/lower diagnostic layout. This opt-in mode is limited to 30 fps or below;
`--update-slices 1` keeps the normal decoder. Bounce is not yet supported.

To animate a 4:3 window within a larger movie, give its upper-left position
and right edge as fractions of the source dimensions:

```sh
python tsvideocodec.py input.mp4 build/window --format cartridge \
  --source-window 0.3,0.3,0.6 --fps 12 --max-frames 24
```

Here, the window starts 30% across and 30% down, and ends 60% across. The
encoder derives the height needed for the TS2068's 256x192 (4:3) display.
If that rectangle would cross the right or bottom edge, it reduces the width
while retaining the requested origin and aspect ratio. The exact resolved
pixel rectangle is saved in `sequence/run.json`. If source pixel coordinates
are known, use `--source-window-pixels 576,324,1152` instead. Cropping occurs
before scaling and automatic clip analysis. Without either option, the full
source frame is used as before.

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
  --dither-mode sierra-lite --auto --quality 85
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
  --dither-mode sierra-lite --auto --quality 85
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
