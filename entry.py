"""PyInstaller entry point.

Importing ``autoclicker`` as a package first (instead of running
``autoclicker/__main__.py`` as a top-level script) ensures that the
relative imports inside the package resolve.

Also wraps startup in a top-level crash handler that writes any
uncaught exception to ``%APPDATA%/autoclicker/crash.log`` and shows a
Windows message box — so a ``--windowed`` build never fails silently.
"""
from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


def _crash_log_path() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    path = Path(base) / "autoclicker"
    path.mkdir(parents=True, exist_ok=True)
    return path / "crash.log"


def _report_crash(exc: BaseException) -> None:
    trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    log = _crash_log_path()
    try:
        with log.open("a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} ===\n")
            f.write(f"python={sys.version}\n")
            f.write(f"executable={sys.executable}\n")
            f.write(f"argv={sys.argv}\n")
            f.write(trace)
            f.write("\n")
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            import ctypes
            body = (
                "autoclicker crashed at startup.\n\n"
                f"Log: {log}\n\n"
                f"{trace[-1500:]}"
            )
            ctypes.windll.user32.MessageBoxW(0, body, "autoclicker — crash", 0x10)
        except Exception:
            pass


def _run() -> int:
    from autoclicker.__main__ import main
    return main()


if __name__ == "__main__":
    try:
        sys.exit(_run())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        _report_crash(exc)
        sys.exit(1)
