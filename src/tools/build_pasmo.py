#!/usr/bin/env python3
"""Build Pasmo 0.5.5 from a caller-supplied source tree."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

SOURCES = [
    "pasmo.cxx", "asm.cxx", "asmerror.cxx", "asmfile.cxx", "cpc.cxx",
    "macro.cxx", "nullstream.cxx", "pasmotypes.cxx", "spectrum.cxx",
    "tap.cxx", "token.cxx", "tzx.cxx",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True,
                        help="path to an unpacked Pasmo 0.5.5 source tree")
    parser.add_argument("--output", type=Path, default=Path("build/tools/pasmo.exe"))
    args = parser.parse_args()
    compiler = shutil.which("c++") or shutil.which("g++")
    if not compiler:
        raise SystemExit("a C++ compiler (c++ or g++) was not found on PATH")
    missing = [name for name in SOURCES if not (args.source / name).exists()]
    if missing:
        raise SystemExit(f"Pasmo source is missing: {', '.join(missing)}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    source_paths = [str(args.source / name) for name in SOURCES]
    command = [compiler, "-std=gnu++11", "-O2", "-I", str(args.source),
               "-o", str(args.output)] + source_paths
    subprocess.run(command, check=True)
    print(f"built {args.output.resolve()}")


if __name__ == "__main__":
    main()
