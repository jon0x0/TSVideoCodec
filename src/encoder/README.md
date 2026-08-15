# Encoder workspace

Suggested implementation: Python initially for iteration, with FFmpeg/OpenCV/Pillow/numpy as locally available. Keep TS2068 reconstruction logic deterministic and independently testable.

The performance-critical Sierra Lite and shared hybrid rate-control path also
has a portable C11 implementation in `src/native_encoder/`. Build it with its
Makefile and select it using `--encoder native`; `--encoder python` remains the
default reference backend. See `src/native_encoder/README.md`.

Expected modules:
- palette/display model
- 8x1 ECM cell optimizer
- temporal optimizer
- rate controller
- SVD bitstream writer/reader
- exact preview renderer
- statistics/reporting
- TAP/DCK packer integration

## Current prototype

`svd_ecm.py` implements the paired display-file address model, legal ECM
attribute/pixel optimization, and exact reconstruction. Encode a still with:

    python src/encoder/encode_ecm.py input.png build/frame000

This writes `frame000.pix`, `frame000.atr`, and `frame000_preview.png`.

Encode a video probe or complete sequence with temporal change statistics:

    python src/encoder/encode_sequence.py input.mp4 build/sequence --fps 12 --max-frames 12

Optional decoder-budgeted reconstruction is enabled explicitly with
`--max-hybrid-bytes`; its default is zero (disabled):

    python src/encoder/encode_sequence.py input.mp4 build/cbr --fps 12 --max-hybrid-bytes 1800

This ranks 8x1 cell updates by source-error reduction, retains the most useful
updates within the compressed-byte ceiling, and uses the reconstructed result
as the next temporal predictor so encoder and decoder cannot drift.

Independent bitmap/chroma ceilings are another opt-in profile:

    python src/encoder/encode_sequence.py input.mp4 build/planes --fps 12 \
      --max-bitmap-bytes 1450 --max-attribute-bytes 350 --max-attribute-age 4

Both plane budgets must be specified together. `--max-attribute-age` raises
overdue color cells to the front of the attribute queue; zero disables age
priority. These flags do not alter the unrestricted or shared-budget modes.

Clip-level allocation is also opt-in. It carries unused bytes from quiet frames
forward and divides the remaining budget according to measured source motion:

    python src/encoder/encode_sequence.py input.mp4 build/global --fps 12 \
      --clip-delta-bytes 42000 --clip-bitmap-fraction 0.8 \
      --clip-min-frame-bytes 200 --clip-max-frame-bytes 3600 \
      --max-attribute-age 4

The hard frame cap is required to protect the measured 12 fps decoder deadline.

Start extraction at an exact source offset with `--start-seconds`, for example:

    python src/encoder/encode_sequence.py input.mp4 build/sequence --start-seconds 3

Add reconstructed-state-aware temporal selection with a per-plane update cost:

    python src/encoder/encode_sequence.py input.mp4 build/temporal --fps 12 --change-penalty 5000

Run the standard temporal penalty sweep and write a comparison CSV:

    python src/encoder/benchmark_temporal.py input.mp4 build/temporal_benchmark

Every sequence output includes `run.json` with the source SHA-256 and encoder
settings.

Diagnose palette false color with a reproducible chroma-weight sweep:

    python src/encoder/benchmark_chroma.py input.mp4 build/chroma_benchmark

Normal encoding uses chroma weight `1.0`, selected to suppress complementary
false color on low-saturation footage. `--chroma-weight` selects another fixed
experimental value; `--adaptive-chroma` enables the experimental cell-adaptive
model.

Pack generated planes into provisional SVD v0 and verify an exact reference
decoder round trip:

    python src/encoder/pack_svd.py build/temporal/p5000 build/demo.svd --fps-num 12
