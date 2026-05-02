import threading
import time

from .capture import ScreenCapturer
from .click import Clicker
from .config import Config, load_config, save_config
from .control_window import ControlWindow
from .detect import detect_prompt
from .idle import IdleRegistry
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

        self.armed = threading.Event()
        if self.cfg.armed_on_start:
            self.armed.set()
        self.paused = threading.Event()
        self.stop_event = threading.Event()
        self._wakeup = threading.Event()
        self._pick_requested = threading.Event()
        self._pick_window_requested = threading.Event()

        self._capturer: ScreenCapturer | None = None
        self._ocr: Ocr | None = None
        self._clicker: Clicker | None = None
        self._dedup = DedupCache(window_s=self.cfg.dedup_window_s)
        self._cooldown = Cooldown(interval_s=self.cfg.click_cooldown_s)
        self._consec_failures = 0
        self._idle = IdleRegistry()
        self._pick_session_requested = threading.Event()
        self._overlay_dirty = threading.Event()

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
            armed=self.armed,
            paused=self.paused,
            on_quit=self.stop,
            on_pick_regions=self._request_pick,
            on_clear_regions=self._clear_regions,
            on_arm_toggle=self._on_arm_toggle,
            on_pause_toggle=self._on_pause_toggle,
            on_pick_window_region=self._request_pick_window,
            on_configure_session=self._request_pick_session,
        )
        self._window.build()
        self._window.set_status(
            armed=self.armed.is_set(),
            paused=self.paused.is_set(),
            ai_check=bool(self.cfg.resolved_api_key()),
            region_count=len(self.cfg.regions),
        )

        # Ensure runtime (capture + OCR) lazily on the worker, but we need
        # monitors now for overlays.
        self._ensure_capturer()
        self._overlay = OverlayController(self._window.root)
        self._refresh_overlay()

        self._start_worker()

        # Main-thread pick dispatcher — worker requests a pick, main thread runs it.
        self._schedule_pick_dispatch()
        # Worker watchdog — surface a dead worker in the UI and try to restart.
        self._schedule_worker_watchdog()

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
        self._overlay.set(
            self.cfg.regions,
            self._capturer.monitors,
            sessions=self.cfg.window_sessions,
        )
        if self._window:
            self._window.set_status(region_count=len(self.cfg.regions))

    # ---- button callbacks (called on Tk thread) -------------------------
    def _request_pick(self) -> None:
        self._pick_requested.set()

    def _request_pick_window(self) -> None:
        self._pick_window_requested.set()

    def _request_pick_session(self) -> None:
        self._pick_session_requested.set()

    def _on_arm_toggle(self) -> None:
        """Called from the control window whenever armed state flips.

        Clears dedup so a prompt that's already on screen (and was logged
        in the previous mode) is re-processed immediately in the new mode.
        """
        self._dedup = DedupCache(window_s=self.cfg.dedup_window_s)
        self._wakeup.set()
        self.log.info("armed=%s (dedup cleared)", self.armed.is_set())

    def _on_pause_toggle(self) -> None:
        # Same reasoning as arm toggle: a prompt seen just before pause is in
        # dedup; on resume the user expects it to be re-processed immediately.
        # Idle trackers are also reset so a long pause doesn't immediately
        # trip the task-done check on the very next poll cycle.
        self._dedup = DedupCache(window_s=self.cfg.dedup_window_s)
        self._idle = IdleRegistry()
        self._wakeup.set()
        self.log.info("paused=%s (dedup + idle cleared)", self.paused.is_set())

    def _clear_regions(self) -> None:
        self.cfg.regions = []
        save_config(self.cfg)
        self.log.info("regions cleared")
        self._refresh_overlay()

    def _schedule_pick_dispatch(self) -> None:
        if self._window is None or self._window.root is None:
            return
        if self._pick_requested.is_set():
            self._pick_requested.clear()
            self._run_picker()
        if self._pick_window_requested.is_set():
            self._pick_window_requested.clear()
            self._run_window_picker()
        if self._pick_session_requested.is_set():
            self._pick_session_requested.clear()
            self._run_session_picker()
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

    def _run_picker(self) -> None:
        from .region_picker import pick_regions

        assert self._capturer is not None
        self.log.info("launching region picker")
        if self._overlay:
            self._overlay.clear()
        try:
            regions = pick_regions(self._capturer.monitors, parent=self._window.root)
        finally:
            self._refresh_overlay()
        if not regions:
            self.log.info("picker cancelled — keeping %d existing region(s)", len(self.cfg.regions))
            return
        self.cfg.regions = regions
        save_config(self.cfg)
        self.log.info("saved %d region(s)", len(regions))
        self._refresh_overlay()

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

    def _run_session_picker(self) -> None:
        """Pick a window, list goals, click on chat input — save WindowSession."""
        from .session_picker import configure_session

        assert self._capturer is not None
        self.log.info("launching window-session picker")
        if self._overlay:
            self._overlay.clear()

        # If sessions exist, default to editing the first matching one for
        # the picked window; otherwise create new. configure_session does
        # the matching itself when 'existing' is supplied.
        try:
            session = configure_session(parent=self._window.root)
        except Exception as exc:  # noqa: BLE001
            self.log.exception("session picker failed: %s", exc)
            self._refresh_overlay()
            return
        finally:
            pass

        if session is None:
            self.log.info("session picker cancelled")
            self._refresh_overlay()
            return

        # Replace any existing session for the same title_match.
        self.cfg.window_sessions = [
            s for s in self.cfg.window_sessions
            if s.title_match.lower() != session.title_match.lower()
        ] + [session]
        save_config(self.cfg)
        # Reset idle state for this match — fresh session.
        self._idle.forget(session.title_match)
        self.log.info(
            "saved session for %r: %d goal(s), idle_threshold=%.0fs",
            session.title_match, len(session.goals), session.idle_threshold_s,
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
          1. (when armed) bring the window to the foreground;
          2. OCR each region and run Yes/No detection (existing behaviour);
          3. update the idle tracker with the concatenated OCR text;
          4. if the window has a configured ``WindowSession`` that's
             gone idle past its threshold, ask OpenAI whether the AI
             has finished its goals and paste a follow-up message.
        """
        assert self._capturer and self._ocr
        if not self.cfg.regions:
            for frame in self._capturer.grab_all():
                lines = self._ocr.run(frame.image)
                self._process_detection(frame, lines)
            return

        from .window import bring_to_front, find_window, get_foreground

        groups: "dict[str | None, list]" = {}
        for region in self.cfg.regions:
            key = (region.window_title_match or "").strip() or None
            groups.setdefault(key, []).append(region)

        switch_focus = self.armed.is_set() and any(k for k in groups)
        saved_fg_hwnd: int | None = None
        if switch_focus:
            fg = get_foreground()
            if fg is not None:
                saved_fg_hwnd = fg.hwnd

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
                    try:
                        frame = self._capturer.grab_region(region)
                    except ValueError:
                        continue
                    lines = self._ocr.run(frame.image)
                    if lines:
                        window_text_parts.append("\n".join(ln.text for ln in lines))
                    self._process_detection(frame, lines)

                if pattern:
                    visible_text = "\n\n".join(window_text_parts)
                    self._maybe_run_task_check(pattern, visible_text)
        finally:
            if saved_fg_hwnd is not None:
                try:
                    bring_to_front(saved_fg_hwnd, settle_s=0.0)
                except Exception:
                    pass

    def _process_detection(self, frame, lines) -> None:
        if not lines:
            return
        det = detect_prompt(lines, frame.monitor)
        if det is None:
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
            return

        if not self.armed.is_set():
            self.log.info("WOULD CLICK (%d,%d) — dry-run", det.yes_click_x, det.yes_click_y)
            return

        if not self._cooldown.ready():
            self.log.info("skipped click: cooldown active")
            return

        assert self._clicker
        cr = self._clicker.click(det.yes_click_x, det.yes_click_y)
        if cr.clicked:
            self._cooldown.trigger()
            self.log.info("CLICKED: %s", cr.reason)
        else:
            self.log.info("skipped click: %s", cr.reason)

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
        if not self.armed.is_set():
            self.log.info(
                "window %r: idle %.0fs but dry-run — skipping task check",
                session.title_match, idle_for,
            )
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

        if verdict.status == "done":
            self._send_to_window(_TASK_DONE_CONFIRM_TEXT, session)
            session.completed = True
            try:
                save_config(self.cfg)
            except Exception:
                self.log.exception("failed to persist completed=True for %r", session.title_match)
            self._overlay_dirty.set()
            self.log.info("window %r marked completed", session.title_match)
        elif verdict.status == "not_done":
            self._send_to_window(_TASK_CONTINUE_TEXT, session)
        else:
            self.log.info("task-check: unknown — no action")

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
