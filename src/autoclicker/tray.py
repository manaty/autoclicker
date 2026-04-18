import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

from .paths import config_path, log_dir


def _build_icon(color: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((6, 6, 58, 58), fill=color, outline=(30, 30, 30, 255), width=2)
    return img


ARMED_ICON = _build_icon("#37b24d")
DRYRUN_ICON = _build_icon("#d0d0d0")


class TrayController:
    """pystray icon; toggles armed flag on a threading.Event."""

    def __init__(
        self,
        armed: threading.Event,
        on_quit: Callable[[], None],
        on_pick_regions: Callable[[], None] | None = None,
    ) -> None:
        import pystray

        self._pystray = pystray
        self._armed = armed
        self._on_quit = on_quit
        self._on_pick_regions = on_pick_regions
        self._icon: "pystray.Icon | None" = None

    def _title(self) -> str:
        return "autoclicker: ARMED" if self._armed.is_set() else "autoclicker: DRY-RUN"

    def _image(self) -> Image.Image:
        return ARMED_ICON if self._armed.is_set() else DRYRUN_ICON

    def _toggle(self, icon, item) -> None:
        if self._armed.is_set():
            self._armed.clear()
        else:
            self._armed.set()
        icon.icon = self._image()
        icon.title = self._title()

    def _open(self, path: Path) -> None:
        try:
            if sys.platform == "win32":
                import os
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            pass

    def _open_logs(self, icon, item) -> None:
        self._open(log_dir())

    def _open_config(self, icon, item) -> None:
        path = config_path()
        if not path.exists():
            path.write_text("{}\n", encoding="utf-8")
        self._open(path)

    def _quit(self, icon, item) -> None:
        icon.stop()
        self._on_quit()

    def _pick_regions(self, icon, item) -> None:
        if self._on_pick_regions is not None:
            self._on_pick_regions()

    def run(self) -> None:
        menu = self._pystray.Menu(
            self._pystray.MenuItem(
                lambda item: "Disarm (dry-run)" if self._armed.is_set() else "Arm auto-click",
                self._toggle,
                default=True,
            ),
            self._pystray.MenuItem("Set monitored regions", self._pick_regions),
            self._pystray.MenuItem("Open log folder", self._open_logs),
            self._pystray.MenuItem("Edit config", self._open_config),
            self._pystray.Menu.SEPARATOR,
            self._pystray.MenuItem("Quit", self._quit),
        )
        self._icon = self._pystray.Icon(
            "autoclicker",
            self._image(),
            self._title(),
            menu,
        )
        self._icon.run()
