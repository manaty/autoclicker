from autoclicker.capture import Monitor
from autoclicker.detect import detect_prompt
from autoclicker.ocr import OcrLine


def _line(text, top, left=100, w=400, h=24):
    return OcrLine(
        text=text,
        left=left,
        top=top,
        right=left + w,
        bottom=top + h,
        confidence=0.99,
    )


MON = Monitor(index=1, left=0, top=0, width=1920, height=1080)


def test_detects_basic_prompt():
    lines = [
        _line("Allow this bash command?", top=100),
        _line("ls -la", top=150),
        _line("List files", top=180),
        _line("1 Yes", top=220, w=120),
        _line("2 No", top=260, w=120),
    ]
    det = detect_prompt(lines, MON)
    assert det is not None
    assert "ls -la" in det.command_text
    assert det.yes_click_x == (100 + 60)
    assert det.yes_click_y == (220 + 12)


def test_detects_without_header():
    # Claude shows many prompt headers ("Do you want to proceed?", edit
    # confirmations, …) and OCR often drops the long header line entirely.
    # The "1 Yes" / "2 No" option menu is the reliable anchor, so a prompt
    # without the "Allow this command" header must still be detected.
    lines = [
        _line("Do you want to proceed?", top=50),
        _line("1 Yes", top=220, left=100, w=120),
        _line("2 No", top=260, w=120),
    ]
    det = detect_prompt(lines, MON)
    assert det is not None
    assert det.source == "claude"
    assert det.yes_click_x == (100 + 60)
    assert det.yes_click_y == (220 + 12)


def test_ignores_lone_yes_without_no():
    # A "1 Yes" with no matching "[2-9] No" nearby is not a confirm menu.
    lines = [
        _line("Some other window", top=50),
        _line("1 Yes", top=220, w=120),
    ]
    assert detect_prompt(lines, MON) is None


def test_ignores_yes_no_too_far_apart():
    # Yes and No separated by more than the option-gap window → not one menu.
    lines = [
        _line("Some prose with 1 Yes mention", top=50),
        _line("1 Yes", top=100, w=120),
        _line("2 No", top=900, w=120),
    ]
    assert detect_prompt(lines, MON) is None


def test_picks_lowest_active_prompt():
    # A scrolled-back answered prompt plus the live one below it — click the
    # lower (most recent) menu.
    lines = [
        _line("Allow this bash command?", top=60),
        _line("old-command", top=100),
        _line("1 Yes", top=140, left=100, w=120),
        _line("2 No", top=170, w=120),
        _line("new-command", top=420),
        _line("1 Yes", top=460, left=100, w=120),
        _line("2 No", top=500, w=120),
    ]
    det = detect_prompt(lines, MON)
    assert det is not None
    assert det.yes_click_y == (460 + 12)
    assert "new-command" in det.command_text


def test_ignores_without_yes_no():
    lines = [
        _line("Allow this bash command?", top=100),
        _line("ls -la", top=150),
    ]
    assert detect_prompt(lines, MON) is None


def test_monitor_offset_applied():
    mon = Monitor(index=2, left=1920, top=0, width=1920, height=1080)
    lines = [
        _line("Allow this bash command?", top=100),
        _line("rm file.txt", top=150),
        _line("1 Yes", top=220, left=100, w=120),
        _line("2 No", top=260, w=120),
    ]
    det = detect_prompt(lines, mon)
    assert det is not None
    assert det.yes_click_x == 1920 + 160
    assert det.yes_click_y == 232


def test_multiline_command_captured():
    lines = [
        _line("Allow this bash command?", top=100),
        _line("until gh run list --limit 1; do", top=150),
        _line("  sleep 20", top=180),
        _line("done", top=210),
        _line("1 Yes", top=260, w=120),
        _line("2 No", top=300, w=120),
    ]
    det = detect_prompt(lines, MON)
    assert det is not None
    assert "until gh run list" in det.command_text
    assert "sleep 20" in det.command_text
    assert "done" in det.command_text


# ----- Codex CLI prompts ----------------------------------------------------


def test_detects_codex_prompt():
    lines = [
        _line("Autorises-tu l'attente du workflow ?", top=80),
        _line("gh run watch 25093192467 --exit-status", top=140),
        _line("1. Yes", top=220, left=100, w=120),
        _line("2. Yes, and don't ask again for commands that start with", top=260, w=400),
        _line("3. No, and tell Codex what to do differently", top=300, w=400),
    ]
    det = detect_prompt(lines, MON)
    assert det is not None
    assert det.source == "codex"
    assert "gh run watch" in det.command_text
    # click target = center of "1. Yes"
    assert det.yes_click_x == (100 + 60)
    assert det.yes_click_y == (220 + 12)


def test_codex_shape_without_anchor_falls_back_to_claude():
    # Same shape as a Codex prompt but no "tell Codex" anchor: we no longer
    # require the anchor — a clear "1. Yes" / "[2-9]. No" menu is a real
    # confirm prompt and gets clicked via the header-less Claude fallback
    # (just not labelled "codex" since the anchor is what proves Codex).
    lines = [
        _line("Random window header", top=80),
        _line("some-command --flag", top=140),
        _line("1. Yes", top=220, left=100, w=120),
        _line("2. Yes, do it again", top=260, w=200),
        _line("3. No thanks", top=300, w=120),
    ]
    det = detect_prompt(lines, MON)
    assert det is not None
    assert det.source == "claude"
    assert det.yes_click_y == (220 + 12)


def test_codex_monitor_offset_applied():
    mon = Monitor(index=2, left=1920, top=0, width=1920, height=1080)
    lines = [
        _line("rm important.txt", top=140),
        _line("1. Yes", top=220, left=100, w=120),
        _line("3. No, and tell Codex what to do differently", top=300, w=400),
    ]
    det = detect_prompt(lines, mon)
    assert det is not None
    assert det.source == "codex"
    assert det.yes_click_x == 1920 + 160
    assert det.yes_click_y == 232


def test_claude_takes_priority_over_codex():
    # Both header types present (unlikely in practice). Claude wins because
    # it has the stricter signature; Codex is the fallback path.
    lines = [
        _line("Allow this bash command?", top=80),
        _line("ls -la", top=120),
        _line("1 Yes", top=180, w=120),
        _line("2 No", top=220, w=120),
        _line("3. No, and tell Codex what to do differently", top=320, w=400),
    ]
    det = detect_prompt(lines, MON)
    assert det is not None
    assert det.source == "claude"
