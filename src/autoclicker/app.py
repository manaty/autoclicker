import threading
import time

from . import updater
from .capture import ScreenCapturer
from .click import Clicker
from .config import Config, load_config, save_config
from .control_window import ControlWindow
from .detect import detect_prompt
from .idle import IdleRegistry
from .log_ticker import LogTicker
from .logging_setup import setup_logging
from .ocr import Ocr
from .overlay import OverlayController
from .safety import classify
from .sessions import WindowSession
from .state import Cooldown, DedupCache
from .task_check import check_task_done


_TASK_CONTINUE_TEXT = "Continue la tâche."
_TASK_DONE_CONFIRM_TEXT = "as-tu tout terminé d'implémenter ?"


def _truncate(s: str, n: int = 120) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


# Recreate the capturer + OCR engine after this many consecutive tick failures.
_RUNTIME_RESET_AFTER = 3


class App:
    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg or load_config()
        self.log = setup_logging(self.cfg.log_level)

        # armed/paused kept as Events for backwards compat with status calls,
        # but the user-facing arm/disarm + global pause toggles were removed —
        # the autoclicker is always active; per-region pause is the new model.
        self.armed = threading.Event()
        self.armed.set()
        self.paused = threading.Event()
        self.stop_event = threading.Event()
        self._wakeup = threading.Event()
        self._pick_window_requested = threading.Event()

        self._capturer: ScreenCapturer | None = None
        self._ocr: Ocr | None = None
        self._clicker: Clicker | None = None
        self._dedup = DedupCache(window_s=self.cfg.dedup_window_s)
        self._cooldown = Cooldown(interval_s=self.cfg.click_cooldown_s)
        self._consec_failures = 0
        self._idle = IdleRegistry()
        self._overlay_dirty = threading.Event()
        self._ticker = LogTicker()
        # Per-region "scanned" heartbeats are throttled to avoid spam:
        # one entry every ~30s of polling unless something interesting fires.
        self._last_heartbeat_at: dict[str, float] = {}
        # Per-region pause state. Manual = user clicked the ⏸ icon. Logs-open
        # = the user has the logs panel up; we auto-pause monitoring while
        # they read so the ticker doesn't keep mutating under their eyes.
        self._paused_regions: set[int] = set()
        self._logs_open_regions: set[int] = set()
        self._pause_lock = threading.Lock()
        self._open_logs_panels: dict = {}  # cfg_idx -> RegionLogsPanel
        self._available_update: updater.ReleaseInfo | None = None

        self._window: ControlWindow | None = None
        self._overlay: OverlayController | None = None
        self._worker: threading.Thread | None = None

    # ---- lifecycle -------------------------------------------------------
    def run(self) -> None:
        self.log.info(
            "autoclicker starting (armed=%s, paused=%s, model=%s, regions=%d, ai_check=%s, interval=%dms)",
            self.armed.is_set(),
            self.paused.is_set(),
            self.cfg.model,
            len(self.cfg.regions),
            "on" if self.cfg.resolved_api_key() else "OFF",
            self.cfg.poll_interval_ms,
        )

        self._window = ControlWindow(
            on_quit=self.stop,
            on_pick_window_region=self._request_pick_window,
            on_install_update=self._install_update,
        )
        self._window.build()
        self._window.set_status(
            ai_check=bool(self.cfg.resolved_api_key()),
            region_count=len(self.cfg.regions),
            version=updater.current_version(),
        )

        # Ensure runtime (capture + OCR) lazily on the worker, but we need
        # monitors now for overlays.
        self._ensure_capturer()
        self._overlay = OverlayController(
            self._window.root,
            ticker=self._ticker,
            on_edit=self._on_region_edit,
            on_resize=self._on_region_resize,
            on_delete=self._on_region_delete,
            on_toggle_pause=self._on_region_toggle_pause,
            on_show_logs=self._on_region_show_logs,
        )
        self._refresh_overlay()

        self._start_worker()

        # Main-thread pick dispatcher — worker requests a pick, main thread runs it.
        self._schedule_pick_dispatch()
        # Worker watchdog — surface a dead worker in the UI and try to restart.
        self._schedule_worker_watchdog()

        # Background update check — non-blocking; result piped into status bar.
        self._schedule_update_check()

        try:
            self._window.run()
        finally:
            self.stop_event.set()
            self._wakeup.set()
            if self._overlay:
                self._overlay.clear()
            if self._capturer:
                self._capturer.close()
            self.log.info("autoclicker stopped")

    def stop(self) -> None:
        self.stop_event.set()
        self._wakeup.set()

    # ---- runtime setup ---------------------------------------------------
    def _ensure_capturer(self) -> None:
        if self._capturer is None:
            self._capturer = ScreenCapturer()

    def _ensure_ocr(self) -> None:
        if self._ocr is None:
            self._ocr = Ocr()

    def _ensure_clicker(self) -> None:
        if self._clicker is None:
            self._clicker = Clicker(activity_radius_px=self.cfg.user_activity_radius_px)

    def _reset_runtime(self) -> None:
        """Drop and rebuild capture + OCR after repeated tick failures.

        ``mss`` can return permanently empty frames after a display-config
        change (RDP reconnect, monitor sleep). RapidOCR's ONNX session has
        also been known to wedge after long uptime. Easier to recreate both
        than to diagnose live.
        """
        self.log.warning("resetting runtime after %d consecutive failures", self._consec_failures)
        try:
            if self._capturer is not None:
                self._capturer.close()
        except Exception:
            pass
        self._capturer = None
        self._ocr = None
        try:
            self._ensure_capturer()
            self._ensure_ocr()
        except Exception as exc:  # noqa: BLE001
            self.log.exception("runtime reset failed: %s", exc)

    def _refresh_overlay(self) -> None:
        if self._overlay is None or self._capturer is None:
            return
        with self._pause_lock:
            paused_now = set(self._paused_regions) | set(self._logs_open_regions)
        self._overlay.set(
            self.cfg.regions,
            self._capturer.monitors,
            sessions=self.cfg.window_sessions,
            paused_indices=paused_now,
        )
        if self._window:
            self._window.set_status(region_count=len(self.cfg.regions))

    def _is_region_paused(self, cfg_idx: int) -> bool:
        with self._pause_lock:
            return (
                cfg_idx in self._paused_regions
                or cfg_idx in self._logs_open_regions
            )

    # ---- button callbacks (called on Tk thread) -------------------------
    def _request_pick_window(self) -> None:
        self._pick_window_requested.set()

    # ---- per-region header actions (Tk thread) ---------------------------
    def _on_region_edit(self, cfg_idx: int) -> None:
        """Open the goals editor for the session bound to this region."""
        if cfg_idx < 0 or cfg_idx >= len(self.cfg.regions):
            return
        region = self.cfg.regions[cfg_idx]
        match = (region.window_title_match or "").strip()
        if not match:
            self.log.info("region #%d has no window_title_match — nothing to edit", cfg_idx + 1)
            return

        # Reuse the standard session-picker flow, scoped to this region's match.
        from .session_picker import configure_session
        if self._overlay:
            self._overlay.clear()
        try:
            session = configure_session(
                parent=self._window.root,
                available_matches=[match],
                existing_sessions=list(self.cfg.window_sessions),
            )
        except Exception:
            self.log.exception("region edit picker failed")
            self._refresh_overlay()
            return

        if session is None:
            self._refresh_overlay()
            return

        self.cfg.window_sessions = [
            s for s in self.cfg.window_sessions
            if s.title_match.lower() != session.title_match.lower()
        ] + [session]
        save_config(self.cfg)
        self._idle.forget(session.title_match)
        self.log.info("region #%d: session updated (%d goals)", cfg_idx + 1, len(session.goals))
        self._refresh_overlay()

    def _on_region_resize(self, cfg_idx: int) -> None:
        """Re-pick this region's rectangle on its monitor; preserve title_match."""
        from .region_picker import pick_regions
        if cfg_idx < 0 or cfg_idx >= len(self.cfg.regions):
            return
        old = self.cfg.regions[cfg_idx]

        if self._overlay:
            self._overlay.clear()
        try:
            picked = pick_regions(
                self._capturer.monitors,
                parent=self._window.root,
                only_monitor_index=old.monitor_index,
                window_title_match=old.window_title_match,
                hint_suffix=f"resize region #{cfg_idx + 1}"
                + (f" — {old.window_title_match}" if old.window_title_match else ""),
            )
        finally:
            self._refresh_overlay()

        if not picked:
            self.log.info("resize cancelled for region #%d", cfg_idx + 1)
            return
        # We replace the existing region with the *first* drawn rectangle;
        # if the user drew several, they're appended.
        new_first, *extras = picked
        self.cfg.regions[cfg_idx] = new_first
        self.cfg.regions = self.cfg.regions + list(extras)
        save_config(self.cfg)
        self.log.info(
            "region #%d resized: %dx%d at (%d,%d) on monitor %d",
            cfg_idx + 1, new_first.w, new_first.h, new_first.x, new_first.y,
            new_first.monitor_index,
        )
        self._refresh_overlay()

    def _on_region_toggle_pause(self, cfg_idx: int) -> None:
        if cfg_idx < 0 or cfg_idx >= len(self.cfg.regions):
            return
        with self._pause_lock:
            if cfg_idx in self._paused_regions:
                self._paused_regions.discard(cfg_idx)
                state = "resumed"
            else:
                self._paused_regions.add(cfg_idx)
                state = "paused"
        region = self.cfg.regions[cfg_idx]
        ticker_key = region.window_title_match or f"region_{cfg_idx}"
        self._ticker.add(f"region #{cfg_idx + 1} {state}", key=ticker_key)
        self.log.info("region #%d %s by user", cfg_idx + 1, state)
        self._refresh_overlay()

    def _on_region_show_logs(self, cfg_idx: int) -> None:
        from .region_logs_panel import RegionLogsPanel

        if cfg_idx < 0 or cfg_idx >= len(self.cfg.regions):
            return
        if cfg_idx in self._open_logs_panels:
            return  # already open

        region = self.cfg.regions[cfg_idx]
        if self._capturer is None:
            return
        mon = next((m for m in self._capturer.monitors if m.index == region.monitor_index), None)
        if mon is None:
            return

        ticker_key = region.window_title_match or f"region_{cfg_idx}"
        with self._pause_lock:
            self._logs_open_regions.add(cfg_idx)

        def _on_close():
            with self._pause_lock:
                self._logs_open_regions.discard(cfg_idx)
            self._open_logs_panels.pop(cfg_idx, None)
            self._refresh_overlay()

        panel = RegionLogsPanel(
            self._window.root,
            idx=cfg_idx + 1,
            region=region,
            monitor=mon,
            ticker=self._ticker,
            ticker_key=ticker_key,
            on_close=_on_close,
        )
        self._open_logs_panels[cfg_idx] = panel
        self._refresh_overlay()

    def _on_region_delete(self, cfg_idx: int) -> None:
        """Remove a region after confirmation."""
        from tkinter import messagebox
        if cfg_idx < 0 or cfg_idx >= len(self.cfg.regions):
            return
        region = self.cfg.regions[cfg_idx]
        label = f"region #{cfg_idx + 1}"
        if region.window_title_match:
            label += f" ({region.window_title_match})"
        if not messagebox.askyesno(
            "Delete region?",
            f"Remove {label}?",
            parent=self._window.root,
        ):
            return
        self.cfg.regions = [
            r for i, r in enumerate(self.cfg.regions) if i != cfg_idx
        ]
        save_config(self.cfg)
        self.log.info("deleted %s", label)
        self._refresh_overlay()

    def _schedule_pick_dispatch(self) -> None:
        if self._window is None or self._window.root is None:
            return
        if self._pick_window_requested.is_set():
            self._pick_window_requested.clear()
            self._run_window_picker()
        if self._overlay_dirty.is_set():
            self._overlay_dirty.clear()
            self._refresh_overlay()
        self._window.root.after(150, self._schedule_pick_dispatch)

    def _schedule_worker_watchdog(self) -> None:
        if self._window is None or self._window.root is None:
            return
        if self.stop_event.is_set():
            return
        if self._worker is not None and not self._worker.is_alive():
            self.log.error("worker thread died — restarting")
            if self._window:
                self._window.set_status(last_verdict="WORKER DIED — restarting")
            self._start_worker()
        self._window.root.after(1000, self._schedule_worker_watchdog)

    def _start_worker(self) -> None:
        self._worker = threading.Thread(target=self._worker_loop, name="detect", daemon=True)
        self._worker.start()

    # ---- update check ----------------------------------------------------
    def _schedule_update_check(self) -> None:
        """Kick off a background update check ~5 s after launch.

        Re-runs every hour as long as the app stays open — the user might
        leave it running for days while a new release lands.
        """
        if self._window is None or self._window.root is None:
            return

        def _on_result(info):
            try:
                if info is None:
                    return
                self._available_update = info
                cur = updater.current_version()
                msg = f"Update {info.tag} available (you're on {cur})"
                self.log.info(msg)
                if self._window:
                    self._window.set_status(
                        update_tag=info.tag,
                        update_message=msg,
                    )
                self._ticker.add(msg)
            except Exception:
                self.log.exception("update result handler failed")

        try:
            updater.check_async(_on_result)
        except Exception:
            self.log.exception("update check spawn failed")
        # Schedule the next one in 1 h.
        self._window.root.after(60 * 60 * 1000, self._schedule_update_check)

    def _install_update(self) -> None:
        info = self._available_update
        if info is None:
            return
        if self._window:
            self._window.set_status(update_message=f"Downloading {info.tag}…")
        self.log.info("downloading update %s from %s", info.tag, info.asset_url)

        def run() -> None:
            try:
                msg = updater.download_and_apply(info)
            except Exception as exc:  # noqa: BLE001
                msg = f"update failed: {exc}"
                self.log.exception("update apply failed: %s", exc)
            self.log.info("updater: %s", msg)
            if self._window:
                self._window.set_status(update_message=msg)
            if "queued" in msg:
                # Give the bat ~1 s to spawn, then quit so it can swap us.
                self.stop_event.set()
                self._wakeup.set()
                if self._window and self._window.root:
                    self._window.root.after(1500, self._window._quit)

        threading.Thread(target=run, name="updater-apply", daemon=True).start()

    def _run_window_picker(self) -> None:
        """Pick a window, foreground it, then draw region(s) on its monitor."""
        from .region_picker import pick_regions
        from .window import bring_to_front
        from .window_picker import pick_window

        assert self._capturer is not None
        self.log.info("launching window picker")
        if self._overlay:
            self._overlay.clear()

        picked = None
        try:
            picked = pick_window(parent=self._window.root)
        except Exception as exc:  # noqa: BLE001
            self.log.exception("window picker failed: %s", exc)
        if picked is None:
            self.log.info("window picker cancelled")
            self._refresh_overlay()
            return

        win, title_match = picked
        self.log.info(
            "picked window hwnd=%#x title=%r match=%r",
            win.hwnd, win.title, title_match,
        )

        # Bring the chosen window to front so the user draws on top of the
        # right content. find_monitor: which monitor contains the window center?
        try:
            bring_to_front(win.hwnd)
        except Exception:
            pass
        cx = (win.left + win.right) // 2
        cy = (win.top + win.bottom) // 2
        target_mon = next(
            (
                m for m in self._capturer.monitors
                if m.left <= cx < m.left + m.width and m.top <= cy < m.top + m.height
            ),
            None,
        )
        only_idx = target_mon.index if target_mon else None

        try:
            new_regions = pick_regions(
                self._capturer.monitors,
                parent=self._window.root,
                only_monitor_index=only_idx,
                window_title_match=title_match,
                hint_suffix=f"window: {title_match}",
            )
        finally:
            self._refresh_overlay()

        if not new_regions:
            self.log.info("window region picker cancelled")
            return
        self.cfg.regions = list(self.cfg.regions) + new_regions
        save_config(self.cfg)
        self.log.info(
            "added %d window region(s) for %r (total=%d)",
            len(new_regions), title_match, len(self.cfg.regions),
        )
        self._refresh_overlay()

    # ---- worker thread ---------------------------------------------------
    def _worker_loop(self) -> None:
        try:
            self._ensure_capturer()
            self._ensure_ocr()
            self._ensure_clicker()
        except Exception as exc:  # noqa: BLE001
            self.log.exception("worker init failed: %s", exc)
            return

        interval = self.cfg.poll_interval_ms / 1000.0
        heartbeat_every = max(1, int(60 / max(1.0, interval)))
        ticks = 0
        while not self.stop_event.is_set():
            ticks += 1
            start = time.monotonic()
            paused = self.paused.is_set()
            if not paused:
                if ticks % heartbeat_every == 0:
                    self.log.info(
                        "heartbeat: armed=%s paused=%s regions=%d failures=%d",
                        self.armed.is_set(), self.paused.is_set(),
                        len(self.cfg.regions), self._consec_failures,
                    )
                try:
                    self._tick()
                    self._consec_failures = 0
                except Exception as exc:  # noqa: BLE001
                    self._consec_failures += 1
                    self.log.exception(
                        "tick failed (%d in a row): %s", self._consec_failures, exc,
                    )
                    if self._consec_failures >= _RUNTIME_RESET_AFTER:
                        self._reset_runtime()
                        self._consec_failures = 0

            elapsed = time.monotonic() - start
            # Use a short wait when paused so resume is responsive (<=0.5s),
            # full poll interval otherwise. _wakeup is set on toggle events
            # and on stop, which lets us bail out instantly.
            sleep_for = 0.5 if paused else max(0.1, interval - elapsed)
            self._wakeup.clear()
            woke = self._wakeup.wait(sleep_for)
            if woke:
                # Drain any further sets so we don't burn CPU.
                self._wakeup.clear()

    def _tick(self) -> None:
        """One poll cycle.

        Groups regions by their ``window_title_match`` so each window's
        regions are captured back-to-back. Within a group:
          1. bring the window to the foreground;
          2. OCR each region and run Yes/No detection;
          3. update the idle tracker with the concatenated OCR text;
          4. if the window has a configured ``WindowSession`` that's
             gone idle past its threshold, ask OpenAI whether the AI
             has finished its goals and paste a follow-up message.
        """
        assert self._capturer and self._ocr
        if not self.cfg.regions:
            for frame in self._capturer.grab_all():
                lines = self._ocr.run(frame.image)
                self._process_detection(frame, lines, ticker_key="")
            return

        from .window import bring_to_front, find_window, get_foreground

        groups: "dict[str | None, list]" = {}
        for region in self.cfg.regions:
            key = (region.window_title_match or "").strip() or None
            groups.setdefault(key, []).append(region)

        switch_focus = any(k for k in groups)
        saved_fg_hwnd: int | None = None
        if switch_focus:
            fg = get_foreground()
            if fg is not None:
                saved_fg_hwnd = fg.hwnd

        # Map every region to its cfg index so unbound regions get a stable
        # per-region ticker key (#1, #2, …) instead of all sharing the global
        # stream. Window-bound regions still share their window's key — that's
        # the design intent (one window's events on one ticker).
        cfg_idx_by_id = {id(r): i for i, r in enumerate(self.cfg.regions)}

        try:
            for pattern, regions in groups.items():
                target = None
                if pattern and switch_focus:
                    target = find_window(pattern)
                    if target is None:
                        self.log.debug(
                            "window pattern %r not matched; skipping %d region(s)",
                            pattern, len(regions),
                        )
                        continue
                    bring_to_front(target.hwnd)

                window_text_parts: list[str] = []
                for region in regions:
                    cfg_idx = cfg_idx_by_id.get(id(region), 0)
                    if self._is_region_paused(cfg_idx):
                        continue
                    try:
                        frame = self._capturer.grab_region(region)
                    except ValueError:
                        continue
                    lines = self._ocr.run(frame.image)
                    if lines:
                        window_text_parts.append("\n".join(ln.text for ln in lines))
                    ticker_key = pattern or f"region_{cfg_idx}"
                    self._process_detection(frame, lines, ticker_key=ticker_key)
                    self._maybe_emit_heartbeat(ticker_key, lines)

                if pattern:
                    visible_text = "\n\n".join(window_text_parts)
                    self._maybe_run_task_check(pattern, visible_text)
        finally:
            if saved_fg_hwnd is not None:
                try:
                    bring_to_front(saved_fg_hwnd, settle_s=0.0)
                except Exception:
                    pass

    def _log_missed_detection(self, lines, ticker_key: str) -> None:
        """When OCR has text but no Yes/No prompt was extracted, point at why.

        Throttled per region: at most one diagnostic every 60s, and only
        when the OCR seems to be looking at *something* prompt-shaped
        (mentions of "allow", "yes", "no", or a Codex anchor).
        """
        from .detect import (
            CLAUDE_HEADER_RE, YES_RE, CLAUDE_NO_RE, CODEX_ANCHOR_RE,
        )
        now = time.monotonic()
        diag_key = ticker_key + "::diag"
        last = self._last_heartbeat_at.get(diag_key, 0.0)
        if now - last < 60.0:
            return

        joined = "\n".join(ln.text for ln in lines if ln.text)
        if not joined:
            return
        has_header = bool(CLAUDE_HEADER_RE.search(joined))
        has_yes = any(YES_RE.match(ln.text.strip()) for ln in lines)
        has_no = any(CLAUDE_NO_RE.match(ln.text.strip()) for ln in lines)
        has_codex = bool(CODEX_ANCHOR_RE.search(joined))

        # Only log if at least one prompt-shaped marker is present —
        # otherwise we'd flood every chat the autoclicker watches.
        if not (has_header or has_codex or (has_yes and has_no)):
            return

        self._last_heartbeat_at[diag_key] = now
        bits = []
        bits.append(f"header={'✓' if has_header else '✗'}")
        bits.append(f"yes={'✓' if has_yes else '✗'}")
        bits.append(f"no={'✓' if has_no else '✗'}")
        if has_codex:
            bits.append("codex-anchor=✓")
        msg = f"prompt-shaped but not detected ({', '.join(bits)})"
        self.log.info("%s for ticker_key=%r", msg, ticker_key)
        self._ticker.add(msg, key=ticker_key)

    def _maybe_emit_heartbeat(self, ticker_key: str, lines) -> None:
        """Drop a 'scanned' breadcrumb on the region's marquee at most every 30s.

        Without this, regions that never see a Yes/No prompt show
        'no events yet' forever — which is correct but unhelpful when
        you just want to confirm OCR is reading the window.
        """
        now = time.monotonic()
        last = self._last_heartbeat_at.get(ticker_key, 0.0)
        if now - last < 30.0:
            return
        self._last_heartbeat_at[ticker_key] = now
        n = len(lines) if lines else 0
        self._ticker.add(f"scanned: {n} OCR line(s)", key=ticker_key)

    def _process_detection(self, frame, lines, ticker_key: str = "") -> None:
        if not lines:
            return
        det = detect_prompt(lines, frame.monitor)
        if det is None:
            self._log_missed_detection(lines, ticker_key)
            return

        if self._dedup.seen_recently(det.command_text, frame.monitor.index):
            self.log.debug(
                "skipped: dedup hit on monitor %d (cmd=%r)",
                frame.monitor.index, _truncate(det.command_text, 80),
            )
            return

        cmd_short = _truncate(det.command_text)
        self.log.info(
            "detected [%s] on monitor %d: cmd=%r yes@(%d,%d)",
            det.source, frame.monitor.index, cmd_short,
            det.yes_click_x, det.yes_click_y,
        )
        self._ticker.add(f"detected [{det.source}]: {_truncate(det.command_text, 60)}", key=ticker_key)
        if self._window:
            self._window.set_status(last_detection=f"[{det.source}] {cmd_short}")

        result = classify(det.command_text, self.cfg)
        verdict = result.verdict
        self.log.info(
            "classifier: safe=%s category=%s reason=%s%s",
            verdict.safe, verdict.category, verdict.reason,
            f" error={result.error}" if result.error else "",
        )
        if self._window:
            v_label = f"{'OK' if verdict.safe else 'BLOCK'} · {verdict.category}"
            self._window.set_status(last_verdict=v_label)

        if not verdict.safe:
            self.log.warning("BLOCKED: %s", verdict.reason)
            self._ticker.add(f"BLOCKED · {verdict.category}", key=ticker_key)
            return

        if not self._cooldown.ready():
            self.log.info("skipped click: cooldown active")
            return

        assert self._clicker
        cr = self._clicker.click(det.yes_click_x, det.yes_click_y)
        if cr.clicked:
            self._cooldown.trigger()
            self.log.info("CLICKED: %s", cr.reason)
            self._ticker.add(f"clicked Yes · {verdict.category}", key=ticker_key)
        else:
            self.log.info("skipped click: %s", cr.reason)
            self._ticker.add(f"skipped click: {cr.reason}", key=ticker_key)

    def _maybe_run_task_check(self, title_match: str, visible_text: str) -> None:
        """Update idle tracker for one window and act if the AI has gone quiet."""
        session = next(
            (s for s in list(self.cfg.window_sessions)
             if s.title_match.lower() == title_match.lower() and not s.completed),
            None,
        )
        if session is None:
            return

        st = self._idle.get(session.title_match)
        changed = st.observe(visible_text)
        if changed:
            self.log.debug("window %r: text changed", session.title_match)
            return

        idle_for = st.idle_for()
        if idle_for < session.idle_threshold_s:
            return
        if not st.can_act(session.cooldown_s):
            return

        self.log.info(
            "window %r: idle %.0fs — running task-done check (goals=%d)",
            session.title_match, idle_for, len(session.goals),
        )
        result = check_task_done(session.goals, visible_text, self.cfg)
        verdict = result.verdict
        self.log.info(
            "task-check: status=%s reason=%s%s",
            verdict.status, verdict.reason,
            f" error={result.error}" if result.error else "",
        )

        # API failure on both attempts → fail open if configured: send the
        # "continue" prompt so the AI doesn't sit blocked behind a flaky API.
        api_error_fail_open = (
            result.error is not None
            and getattr(self.cfg, "fail_open_on_api_error", False)
        )

        ticker_key = session.title_match
        if verdict.status == "done":
            self._send_to_window(_TASK_DONE_CONFIRM_TEXT, session)
            session.completed = True
            try:
                save_config(self.cfg)
            except Exception:
                self.log.exception("failed to persist completed=True for %r", session.title_match)
            self._overlay_dirty.set()
            self.log.info("window %r marked completed", session.title_match)
            self._ticker.add("DONE — asked confirmation, session parked", key=ticker_key)
        elif verdict.status == "not_done" or api_error_fail_open:
            if api_error_fail_open:
                self.log.warning("task-check API failed — sending continue (fail-open)")
            self._send_to_window(_TASK_CONTINUE_TEXT, session)
            self._ticker.add(
                "API failed — sent continue (fail-open)" if api_error_fail_open
                else "not_done — sent continue prompt",
                key=ticker_key,
            )
        else:
            self.log.info("task-check: unknown — no action")
            self._ticker.add("idle but task status unknown — no action", key=ticker_key)

        st.mark_acted()

    def _send_to_window(self, text: str, session: WindowSession) -> None:
        """Click on the session's chat input and paste ``text`` + Enter."""
        from .keyboard import type_and_submit

        assert self._clicker
        click_res = self._clicker.click(session.prompt_input_x, session.prompt_input_y)
        if not click_res.clicked:
            self.log.info("skipped task-action click: %s", click_res.reason)
            return
        # Brief settle so the chat input has focus before the paste.
        time.sleep(0.10)
        type_res = type_and_submit(text)
        if type_res.sent:
            self.log.info("sent to %r: %r (%s)", session.title_match, text, type_res.reason)
        else:
            self.log.warning("failed to send to %r: %s", session.title_match, type_res.reason)
