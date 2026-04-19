import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator, List

if TYPE_CHECKING:
    from .regions import Region


@dataclass
class Monitor:
    index: int
    left: int
    top: int
    width: int
    height: int


@dataclass
class Frame:
    monitor: Monitor
    image: "any"  # numpy array at runtime


class ScreenCapturer:
    """Captures one frame per monitor.

    ``mss`` uses a thread-local device context, so each calling thread gets
    its own ``mss.mss()`` instance via ``threading.local``. The monitor list
    is enumerated once on the construction thread and cached.
    """

    def __init__(self) -> None:
        self._tls = threading.local()
        self._monitors = self._build_monitor_list()

    def _sct(self):
        sct = getattr(self._tls, "sct", None)
        if sct is None:
            import mss

            sct = mss.mss()
            self._tls.sct = sct
        return sct

    def _build_monitor_list(self) -> List[Monitor]:
        mons: List[Monitor] = []
        for idx, m in enumerate(self._sct().monitors):
            if idx == 0:
                continue
            mons.append(
                Monitor(
                    index=idx,
                    left=m["left"],
                    top=m["top"],
                    width=m["width"],
                    height=m["height"],
                )
            )
        return mons

    @property
    def monitors(self) -> List[Monitor]:
        return self._monitors

    def grab_all(self) -> Iterator[Frame]:
        import numpy as np

        sct = self._sct()
        for mon in self._monitors:
            raw = sct.grab({
                "left": mon.left,
                "top": mon.top,
                "width": mon.width,
                "height": mon.height,
            })
            img = np.asarray(raw, dtype=np.uint8)[:, :, :3]
            yield Frame(monitor=mon, image=img)

    def grab_region(self, region: "Region") -> Frame:
        """Grab just a region on a given monitor.

        The returned Frame carries a *virtual* Monitor whose left/top are the
        region's origin in **global** screen coords — so OCR bbox coords plus
        ``monitor.left/top`` still resolve to correct click targets.
        """
        import numpy as np

        mon = next((m for m in self._monitors if m.index == region.monitor_index), None)
        if mon is None:
            raise ValueError(f"monitor {region.monitor_index} not found")

        raw = self._sct().grab({
            "left": mon.left + region.x,
            "top": mon.top + region.y,
            "width": region.w,
            "height": region.h,
        })
        img = np.asarray(raw, dtype=np.uint8)[:, :, :3]
        virtual = Monitor(
            index=mon.index,
            left=mon.left + region.x,
            top=mon.top + region.y,
            width=region.w,
            height=region.h,
        )
        return Frame(monitor=virtual, image=img)

    def close(self) -> None:
        sct = getattr(self._tls, "sct", None)
        if sct is not None:
            try:
                sct.close()
            except Exception:
                pass
            self._tls.sct = None
