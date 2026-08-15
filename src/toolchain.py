"""Portable discovery and invocation of external TSVideoCodec tools."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def tool(name: str, explicit: str | Path | None = None) -> str:
    candidate = str(explicit or os.environ.get(name.upper()) or name)
    resolved = shutil.which(candidate)
    if resolved:
        return resolved
    if Path(candidate).is_file():
        return str(Path(candidate).resolve())
    raise SystemExit(f"{name} was not found; install it on PATH or set {name.upper()}")


def assemble_pasmo(source: Path, output: Path, symbols: Path,
                   include: Path | list[Path],
                   executable: str | Path | None = None) -> None:
    includes = include if isinstance(include, list) else [include]
    command = [tool("pasmo", executable)]
    for directory in includes:
        command += ["-I", str(directory)]
    command += ["--bin", str(source), str(output), str(symbols)]
    subprocess.run(command, check=True)
