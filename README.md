# autoclicker

Desktop watcher that auto-approves Claude Code's `Allow this bash command?` prompts — **only after** an OpenAI safety check confirms the command is non-destructive.

## How it works

1. Every 5 s (configurable), captures each monitored **region** (or full screens if none configured).
2. Runs OCR (RapidOCR — pure ONNX, no Tesseract needed).
3. Detects the Claude Code prompt by looking for the `Allow this bash command?` header together with `1 Yes` / `2-9 No` option lines. Frames without all markers are ignored.
4. Extracts the command text between the header and the options.
5. Sends it to OpenAI `gpt-5.4-nano-2026-03-17` with a Structured-Outputs schema asking for `{safe, category, reason}`.
6. If the verdict is `safe: true` **and** the app is armed, moves the mouse to the Yes button's pixel coords and clicks. Otherwise logs the decision.

The classifier fails **closed** on API errors or an uncertain verdict → no click. If `OPENAI_API_KEY` isn't set at all, the AI check is skipped and detections proceed as if safe (status bar shows `AI check: OFF`).

## UI

A small always-on-top control window (no tray icon). Shows the current state — **DRY-RUN / ARMED / PAUSED** — and five buttons:

- **Arm / Disarm** — toggle real clicking vs dry-run.
- **Pause / Resume** — stop/restart the scan loop entirely (no capture, no OCR, no API calls).
- **Set monitored regions** — fullscreen overlay to draw/update regions.
- **Clear regions** — fall back to full-screen scanning.
- **Logs / Config / Quit** — open the log folder, the config JSON, or shut down.

Each saved region is drawn as a permanent green click-through rectangle on screen, labeled `#1`, `#2`, … so you always see what the app is watching.

## Monitored regions

Full-screen OCR on a 2K/4K display is the slow path. Defining one or more ROIs over your Claude Code pane is **10-20× faster** and eliminates most false positives. Two ways to set them:

- **Control window → "Set monitored regions"**: fullscreen translucent overlay appears per monitor. Drag to add rectangles, right-click to remove the last one, Enter to save, Esc to cancel.
- **CLI**: `python -m autoclicker --pick` (or `autoclicker --pick` for the exe) runs just the picker and exits.
- Clear them with `autoclicker --clear-regions` (falls back to full-screen scan).

Regions are saved in `config.json` and persist across launches. Move/resize your terminal? Just rerun the picker.

### Multiple windows on one monitor — "Add window region"

When you've got several VSCode windows on a single screen (e.g. several Claude Code sessions side-by-side or stacked), use **Add window region**:

1. A list of currently-visible top-level windows opens.
2. Pick the one you want to monitor; the title-match field defaults to a stable substring (for VSCode: `"<workspace> - Visual Studio Code"`).
3. Confirm — the window is brought to the foreground and the rectangle picker appears on top of it. Draw one or more regions and press Enter.

At runtime, **when armed**, the autoclicker brings each tracked window to the foreground in turn before capturing its regions. The previously-foreground window is restored at the end of every poll cycle. In dry-run / paused mode the foreground is left alone, so you can keep working without flicker. Each region is stored with its `window_title_match` substring in `config.json` so the binding survives restarts.

## Window sessions — pinging an idle assistant

Beyond auto-approving Yes/No prompts, the autoclicker can also keep a stalled AI assistant moving. Per window, you give it a list of session goals; if the OCR text inside the window stops changing for longer than `idle_threshold_s` (default 60 s), the autoclicker:

1. asks OpenAI whether the assistant has finished the goals,
2. if **done** → pastes `as-tu tout terminé d'implémenter ?` into the chat input, presses Enter, and **marks the session completed** (its overlay rectangles turn orange — no further pings until you re-edit the goals),
3. if **not done** → pastes `Continue la tâche.` and presses Enter,
4. if the verdict is `unknown` → does nothing.

After every action, the per-session cooldown (`cooldown_s`, default 300 s) blocks the next check, so the autoclicker can't loop on a chatty assistant.

To set this up:

1. Click **Configure window session** in the control window.
2. Pick the AI's window from the list.
3. Type goals (one per line). Adjust the idle / cooldown thresholds if needed.
4. **Click on the chat input box** when the translucent overlay appears — that's the pixel the autoclicker will click before pasting messages.

Sessions live in `config.json` under `window_sessions`. Re-running the picker for the same window replaces the existing session.

Idleness checks only run **when armed**. In dry-run / paused mode the autoclicker still tracks change events (so you can watch the heartbeat in the log) but never types anything.

## First launch is dry-run

By design. The control window opens in grey `DRY-RUN` mode. Detections are logged, nothing is clicked. Once you've watched the log and trust the detection, click **Arm auto-click** — header turns green and `ARMED`.

## Install — end user (exe)

Download `autoclicker.exe` from the [GitHub Actions artifacts](../../actions) or the latest release, set `OPENAI_API_KEY` in your environment, then double-click. No Python needed.

## Install — from source

```pwsh
pip install -e .[dev]
python -m autoclicker           # dry-run, tray in grey
python -m autoclicker --armed   # arm on start (still cooldown-gated)
```

## Configuration

Config lives at `%APPDATA%\autoclicker\config.json` (Windows) or `~/.config/autoclicker/config.json` (Linux/macOS). Any field may be omitted; defaults are used.

```json
{
  "openai_api_key": null,
  "model": "gpt-5.4-nano-2026-03-17",
  "model_fallback": "gpt-5.4-nano",
  "poll_interval_ms": 5000,
  "armed_on_start": false,
  "click_cooldown_s": 2.0,
  "dedup_window_s": 5.0,
  "user_activity_radius_px": 50,
  "openai_timeout_s": 4.0,
  "log_level": "INFO",
  "regions": [
    { "monitor_index": 1, "x": 100, "y": 200, "w": 800, "h": 400,
      "window_title_match": "autoclicker - Visual Studio Code" }
  ],
  "window_sessions": [
    {
      "title_match": "autoclicker - Visual Studio Code",
      "goals": ["Add Codex detection", "Cleanup tests"],
      "prompt_input_x": 1234,
      "prompt_input_y": 980,
      "idle_threshold_s": 60,
      "cooldown_s": 300,
      "completed": false
    }
  ]
}
```

The API key falls back to the `OPENAI_API_KEY` environment variable if the config field is null.

## Logs

`%APPDATA%\autoclicker\logs\autoclicker.log` (rotating, 2 MB × 3). Obvious secrets (`sk-…`, `ghp_…`, AWS keys) are redacted on the way out.

## Safety guards

- Classifier fails closed (missing key / API error / ambiguity → no click).
- Per-command dedup for 5 s (avoids re-clicking the same prompt).
- Global click cooldown (2 s) to prevent runaway loops.
- Aborts if the user is actively moving the mouse within 1 s of the planned click.
- `pyautogui` failsafe: slam the mouse into any screen corner to immediately abort.

## Building the exe

On a Windows host:

```pwsh
pip install -e .[dev]
python build_windows.py
```

Produces `dist\autoclicker.exe`. Or let GitHub Actions build it — push to `main` or a `v*` tag and grab the artifact from the `build-windows` workflow.

## Development from WSL / Linux

The code is written on WSL but must **run** on Windows to see the Windows desktop. From WSL you can:

- Edit and lint.
- Run `pytest` for the classifier and detection unit tests.
- Push to trigger the Windows CI build.
