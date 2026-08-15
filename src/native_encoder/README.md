# Portable native SVD encoder

`svdenc.c` is a dependency-free ISO C11 implementation of the expensive SVD
Sierra Lite cell search, error diffusion, and shared-budget hybrid rate
controller. Python remains responsible for FFmpeg extraction, manifests,
previews, stream packing, and TAP/DCK construction.

## Build

Linux or macOS with GCC:

```sh
make -C native_encoder CC=gcc
```

macOS may also use its default Clang C compiler:

```sh
make -C native_encoder
```

Windows with MinGW-w64 GCC and GNU Make on `PATH`:

```powershell
mingw32-make -C native_encoder CC=gcc
```

MSYS2, Cygwin, and WSL can use ordinary `make`. The result is
`src/native_encoder/build/svdenc` or `svdenc.exe`. No third-party C libraries are
required; only the standard C library and `libm` are used.

## Use through the maintained Python pipeline

```sh
python src/encoder/encode_sequence.py input.mp4 build/sequence \
  --fps 20 --dither-mode sierra-lite --encoder native \
  --max-hybrid-bytes 700
```

Use `--native-encoder PATH` for a non-default executable location. The
existing `--encoder python` backend remains available as an independent
reference implementation.

Benchmark both backends reproducibly:

```sh
python demos/scripts/benchmark_native_encoder.py --frames 10
```

Compare a still frame against the Python reference:

```sh
python tests/compare_native_encoder.py image.png
```

The C and Python implementations can choose different but closely equivalent
ECM representations because ink/paper reversal plus bitmap inversion is a
display-equivalent solution and floating-point tie ordering varies by
compiler. Tests therefore report both raw-plane and reconstructed-image
differences. Each backend is deterministic when built with the documented
flags.
