"""GitHub-Releases-backed self-updater for the PyInstaller build.

On startup the App spawns :func:`check_async` in a background thread.
That hits the public GitHub API for ``/releases/latest`` (which excludes
pre-releases like the rolling ``latest`` tag), parses the version,
and decides whether an update is available. The user is then notified
and can trigger :func:`download_and_apply` from the control window.

Apply flow on Windows:
  1. Download the ``autoclicker-vX.Y.Z.exe`` asset to a temp file.
  2. Write a tiny ``.bat`` next to the temp file that, after a 2 s
     pause to let our process exit, moves the running exe out of the
     way and the new one into place, then re-launches us.
  3. Spawn the bat detached and ``sys.exit(0)`` — the bat takes over.

In dev (running from source, ``sys.frozen`` is False) the update path
is a no-op: there's no exe to swap. The check still works so you can
see what version is published.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen


GITHUB_REPO = "manaty/autoclicker"
RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
USER_AGENT = "autoclicker-updater"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def current_version() -> str:
    try:
        from . import _version
        return _version.__version__
    except Exception:
        return "0.0.0-dev"


def _parse(v: str) -> Tuple[int, ...]:
    """Parse 'v0.4.0' / '0.4.0' / '0.0.0-dev+abc' into a tuple for comparison.

    Pre-release / dev tails (``-dev``, ``-rc1``, etc.) collapse to a tuple
    of all zeros so a stamped tag always wins over a dev build.
    """
    s = (v or "").lstrip("vV")
    head = s.split("-", 1)[0].split("+", 1)[0]
    parts = head.split(".")
    out: list[int] = []
    for p in parts:
        try:
            out.append(int(p))
        except ValueError:
            return tuple(out) if out else (0,)
    return tuple(out)


def is_newer(remote: str, local: str) -> bool:
    return _parse(remote) > _parse(local)


@dataclass
class ReleaseInfo:
    tag: str            # e.g. "v0.5.0"
    name: str           # release title
    asset_name: str     # e.g. "autoclicker-v0.5.0.exe"
    asset_url: str      # browser_download_url
    asset_size: int


def _fetch_latest(timeout: float = 6.0) -> Optional[dict]:
    req = Request(RELEASES_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except (URLError, HTTPError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def _pick_exe_asset(release: dict) -> Optional[Tuple[str, str, int]]:
    for a in release.get("assets") or []:
        name = a.get("name") or ""
        if name.lower().startswith("autoclicker") and name.lower().endswith(".exe"):
            return name, a.get("browser_download_url") or "", int(a.get("size") or 0)
    return None


def check_for_update(timeout: float = 6.0) -> Optional[ReleaseInfo]:
    """Return a ``ReleaseInfo`` if a newer published release exists."""
    rel = _fetch_latest(timeout=timeout)
    if not rel:
        return None
    tag = rel.get("tag_name") or ""
    if not is_newer(tag, current_version()):
        return None
    asset = _pick_exe_asset(rel)
    if asset is None:
        return None
    name, url, size = asset
    if not url:
        return None
    return ReleaseInfo(
        tag=tag,
        name=rel.get("name") or tag,
        asset_name=name,
        asset_url=url,
        asset_size=size,
    )


def check_async(callback: Callable[[Optional[ReleaseInfo]], None]) -> threading.Thread:
    """Run the update check on a background thread.

    The callback is invoked once with either a ``ReleaseInfo`` or ``None``.
    Always called — including for "no update" — so the caller can update
    the UI ("up to date").
    """
    def run() -> None:
        try:
            info = check_for_update()
        except Exception:
            info = None
        try:
            callback(info)
        except Exception:
            pass

    t = threading.Thread(target=run, name="updater-check", daemon=True)
    t.start()
    return t


def _download(url: str, dest: Path, progress_cb: Optional[Callable[[int, int], None]] = None) -> None:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
        total = int(resp.headers.get("Content-Length") or 0)
        downloaded = 0
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            if progress_cb:
                try:
                    progress_cb(downloaded, total)
                except Exception:
                    pass


def download_and_apply(
    info: ReleaseInfo,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> str:
    """Download the new exe and arm the swap-and-restart bat.

    Returns a status message. Caller should immediately exit the app
    after a successful return so the bat can replace the running exe.
    """
    if not is_frozen() or sys.platform != "win32":
        return "skipped: only auto-applies on the frozen Windows build"

    cur_exe = Path(sys.executable).resolve()
    tmp_dir = Path(tempfile.gettempdir())
    new_exe = tmp_dir / f"autoclicker-{info.tag}.download.exe"
    if new_exe.exists():
        try:
            new_exe.unlink()
        except Exception:
            pass

    _download(info.asset_url, new_exe, progress_cb=progress_cb)

    if info.asset_size and new_exe.stat().st_size != info.asset_size:
        try:
            new_exe.unlink()
        except Exception:
            pass
        return f"download size mismatch (got {new_exe.stat().st_size}, expected {info.asset_size})"

    bat = tmp_dir / "autoclicker_update.bat"
    bat_body = (
        "@echo off\r\n"
        "timeout /t 2 /nobreak > NUL\r\n"
        f"move /Y \"{cur_exe}\" \"{cur_exe.with_suffix(cur_exe.suffix + '.old')}\"\r\n"
        f"move /Y \"{new_exe}\" \"{cur_exe}\"\r\n"
        f"start \"\" \"{cur_exe}\"\r\n"
        "del \"%~f0\"\r\n"
    )
    bat.write_text(bat_body, encoding="ascii", newline="")

    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    subprocess.Popen(
        ["cmd.exe", "/c", str(bat)],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    return f"update queued — restart in 2 s"
