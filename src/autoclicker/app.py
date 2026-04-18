import threading
import time

from .capture import ScreenCapturer
from .click import Clicker
from .config import Config, load_config, save_config
from .detect import detect_prompt
from .logging_setup import setup_logging
from .ocr import Ocr
from .safety import classify
from .state import Cooldown, DedupCache
from .tray import TrayController


def _truncate(s: str, n: int = 500) -> str:
    return s if len(s) <= n else s[: n - 1] + "\u2026"


class App:
    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg or load_config()
        self.log = setup_logging(self.cfg.log_level)
        self.armed = threading.Event()
        if self.cfg.armed_on_start:
            self.armed.set()
        self.stop_event = threading.Event()
        self.pick_requested = threading.Event()
        self._capturer: ScreenCapturer | None = None
        self._ocr: Ocr | None = None
        self._clicker: Clicker | None = None
        self._dedup = DedupCache(window_s=self.cfg.dedup_window_s)
        self._cooldown = Cooldown(interval_s=self.cfg.click_cooldown_s)

    def _ensure_runtime(self) -> None:
        if self._capturer is None:
            self._capturer = ScreenCapturer()
        if self._ocr is None:
            self._ocr = Ocr()
        if self._clicker is None:
            self._clicker = Clicker(activity_radius_px=self.cfg.user_activity_radius_px)

    def run(self) -> None:
        self.log.info(
            "autoclicker starting (armed=%s, model=%s, regions=%d, ai_check=%s)",
            self.armed.is_set(),
            self.cfg.model,
            len(self.cfg.regions),
            "on" if self.cfg.resolved_api_key() else "OFF (no OPENAI_API_KEY)",
        )
        tray = TrayController(
            self.armed,
            on_quit=self.stop,
            on_pick_regions=self.pick_requested.set,
        )
        tray_thread = threading.Thread(target=tray.run, name="tray", daemon=True)
        tray_thread.start()
        try:
            self._loop()
        finally:
            if self._capturer:
                self._capturer.close()
            self.log.info("autoclicker stopped")

    def stop(self) -> None:
        self.stop_event.set()

    def _loop(self) -> None:
        interval = self.cfg.poll_interval_ms / 1000.0
        while not self.stop_event.is_set():
            start = time.monotonic()
            try:
                self._ensure_runtime()
                if self.pick_requested.is_set():
                    self.pick_requested.clear()
                    self._run_picker()
                    continue
                self._tick()
            except Exception as exc:  # noqa: BLE001
                self.log.exception("tick failed: %s", exc)
            elapsed = time.monotonic() - start
            sleep_for = max(0.05, interval - elapsed)
            self.stop_event.wait(sleep_for)

    def _run_picker(self) -> None:
        from .region_picker import pick_regions

        assert self._capturer
        self.log.info("launching region picker on %d monitor(s)", len(self._capturer.monitors))
        regions = pick_regions(self._capturer.monitors)
        if not regions:
            self.log.info("region picker cancelled — keeping existing %d region(s)", len(self.cfg.regions))
            return
        self.cfg.regions = regions
        save_config(self.cfg)
        self.log.info("saved %d region(s)", len(regions))

    def _iter_frames(self):
        assert self._capturer
        if self.cfg.regions:
            for region in self.cfg.regions:
                try:
                    yield self._capturer.grab_region(region)
                except ValueError:
                    continue
        else:
            yield from self._capturer.grab_all()

    def _tick(self) -> None:
        assert self._capturer and self._ocr
        for frame in self._iter_frames():
            lines = self._ocr.run(frame.image)
            if not lines:
                continue
            det = detect_prompt(lines, frame.monitor)
            if det is None:
                continue

            if self._dedup.seen_recently(det.command_text, frame.monitor.index):
                continue

            self.log.info(
                "detected prompt on monitor %d: command=%r yes@(%d,%d)",
                frame.monitor.index,
                _truncate(det.command_text),
                det.yes_click_x,
                det.yes_click_y,
            )

            result = classify(det.command_text, self.cfg)
            verdict = result.verdict
            self.log.info(
                "classifier: safe=%s category=%s reason=%s%s",
                verdict.safe,
                verdict.category,
                verdict.reason,
                f" error={result.error}" if result.error else "",
            )

            if not verdict.safe:
                self.log.warning("BLOCKED: %s", verdict.reason)
                continue

            if not self.armed.is_set():
                self.log.info("WOULD CLICK (%d,%d) — dry-run", det.yes_click_x, det.yes_click_y)
                continue

            if not self._cooldown.ready():
                self.log.info("skipped click: cooldown active")
                continue

            assert self._clicker
            cr = self._clicker.click(det.yes_click_x, det.yes_click_y)
            if cr.clicked:
                self._cooldown.trigger()
                self.log.info("CLICKED: %s", cr.reason)
            else:
                self.log.info("skipped click: %s", cr.reason)
