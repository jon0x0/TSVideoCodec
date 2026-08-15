# Automatic encoder analysis

`src/encoder/encode_sequence.py --auto` performs deterministic whole-clip analysis
before encoding. It is a codec feature: no interactive or external visual
decisions are required after the command starts.

The current automatic pass:

1. Applies the configured normal brightness, contrast, saturation, and gamma
   transform to every extracted source frame.
2. Builds a robust temporal-median reference.
3. Measures spatial variance and temporal distance for every 8x1 ECM cell.
4. Finds generic flat/stable cells, including cells temporarily occluded by a
   moving object.
5. Consolidates large adjacent regions only when their source colors are
   statistically similar.
6. Fits every reference cell against legal ECM paper/ink pairs and ordered
   patterns with a chroma-preserving objective.
7. Uses the fixed plate when a frame matches the reference and Sierra Lite for
   foreground/detail cells. When an object leaves, the reference plate is
   restored instead of retaining a trail.
8. Detects and reports scene-cut candidates.

All selected thresholds, tone inputs, region sizes, scene cuts, and decisions
are written to `auto_analysis.json` inside the generated sequence directory.
The normal `run.json` records that auto mode was enabled. Explicit encoder
options remain available for experiments and overrides.

`--auto-colour-policy faithful` is the default and weights chroma strongly
enough to preserve pale source hues. `--auto-colour-policy quiet` permits more
neutral flat-region choices when minimizing visible pattern noise is preferred.
The US spelling `--auto-color-policy` is accepted as an alias.

Example:

```text
python src/encoder/encode_sequence.py video/input.gif build/input/sequence \
  --encoder native --dither-mode sierra-lite --auto --fps 12
```

Automatic tone search, decoder-cost allocation, and automatic insertion of
additional cartridge keyframes are planned extensions. The analysis file is
versioned so those decisions can be added without making existing builds
irreproducible.
