"""PyInstaller driver. Run on a Windows host with Python installed:

    python -m pip install -e .[dev]
    python build_windows.py

Output: dist\\autoclicker.exe (self-contained, no Python install required).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ENTRY = ROOT / "entry.py"
SRC = ROOT / "src"


def main() -> int:
    if sys.platform != "win32":
        print("build_windows.py must be run on Windows (PyInstaller produces native binaries).", file=sys.stderr)
        return 2

    dist = ROOT / "dist"
    build = ROOT / "build"
    for p in (dist, build):
        if p.exists():
            shutil.rmtree(p)

    # rapidocr_onnxruntime ships its ONNX models and a YAML config as package data.
    # --collect-all makes sure those resources land in the bundle.
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        "autoclicker",
        "--paths",
        str(SRC),
        "--collect-all",
        "autoclicker",
        "--collect-all",
        "rapidocr_onnxruntime",
        "--collect-data",
        "mss",
        str(ENTRY),
    ]
    print(" ".join(args))
    return subprocess.call(args)


if __name__ == "__main__":
    sys.exit(main())
