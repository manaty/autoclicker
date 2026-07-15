import numpy as np

from autoclicker.capture import Monitor
from autoclicker.detect import detect_prompt, find_selected_row
from autoclicker.ocr import OcrLine


def _blue_bar_image(h=300, w=600, top=140, bottom=171):
    """A dark region image with a solid 'selection blue' bar."""
    img = np.full((h, w, 3), 30, dtype=np.uint8)
    img[top:bottom, :, 0] = 200  # B
    img[top:bottom, :, 1] = 100  # G
    img[top:bottom, :, 2] = 40   # R
    return img


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


def test_detects_when_highlighted_yes_missed_by_ocr():
    # The selected option (Yes, by default) is drawn with a highlight bar that
    # RapidOCR often fails to read — so the "1 Yes" line is absent while the
    # other options read fine. We must still click option 1, extrapolating its
    # row from the evenly-spaced siblings.
    lines = [
        _line("Allow this bash command?", top=60),
        _line("cd /repo && while kill -0 %1; do sleep 2; done", top=110),
        # no "1 Yes" line — highlighted row dropped by OCR
        _line("2 Yes, allow kill -0 %1 for all projects", top=180, left=100),
        _line("3 No", top=220, left=100, w=120),
    ]
    det = detect_prompt(lines, MON)
    assert det is not None
    assert det.source == "claude"
    # pitch = 220-180 = 40; row 1 top = 220 - 40*2 = 140; click center y = 152
    assert det.yes_click_y == 152
    # row 1 is above option 2, below the command line
    assert 134 < det.yes_click_y < 180
    assert "while kill" in det.command_text


def test_detects_when_ocr_drops_space_after_number():
    # OCR frequently merges the number and word ("1Yes", "3No"). These satisfy
    # YES_RE / CLAUDE_NO_RE, so detection must not require a space either.
    lines = [
        _line("Allow this bash command?", top=60),
        _line("pnpm add @checkout.com/checkout-web-components", top=110),
        _line("1Yes", top=180, left=100, w=120),
        _line("2Yes,allow pnpm add for all projects", top=220, left=100),
        _line("3No", top=260, left=100, w=120),
    ]
    det = detect_prompt(lines, MON)
    assert det is not None
    assert det.source == "claude"
    # clicks option 1 ("1Yes")
    assert det.yes_click_y == (180 + 12)


def test_detects_merged_space_with_highlighted_yes_missed():
    # Combined worst case: merged spacing AND the highlighted "1 Yes" row
    # dropped by OCR. Anchor on "3No", extrapolate row 1.
    lines = [
        _line("Allow this bash command?", top=60),
        _line("pnpm add something", top=110),
        _line("2Yes,allow pnpm add for all projects", top=180, left=100),
        _line("3No", top=220, left=100, w=120),
    ]
    det = detect_prompt(lines, MON)
    assert det is not None
    # pitch = 220-180 = 40; row 1 top = 220 - 80 = 140; center y = 152
    assert det.yes_click_y == 152


def test_find_selected_row_locates_blue_bar():
    res = find_selected_row(_blue_bar_image())
    assert res is not None
    cx, cy = res
    assert cy == 155  # center of rows 140..170
    assert 250 < cx < 350  # roughly centered horizontally


def test_find_selected_row_none_on_plain_image():
    img = np.full((300, 600, 3), 30, dtype=np.uint8)
    assert find_selected_row(img) is None


def test_detects_yes_via_blue_bar_when_ocr_misses_everything():
    # Worst case: the "1 Yes" row is entirely dropped AND option 2's number is
    # garbled. The blue highlight bar still pins the Yes click precisely.
    img = _blue_bar_image(top=140, bottom=171)
    lines = [
        _line("Allow this bash command?", top=60),
        _line("cd /repo && find app -name voucher_handler.rb", top=110),
        _line("Yes, allow ls app/models and find app for all projects", top=180),
        _line("3 No", top=220, left=100, w=120),
    ]
    det = detect_prompt(lines, MON, image=img)
    assert det is not None
    assert det.source == "claude"
    assert det.yes_click_y == 155  # bar center, not OCR/extrapolation


def test_blue_bar_ignored_when_below_no_row():
    # If the selection is on the No row (bar below No), don't treat it as Yes.
    img = _blue_bar_image(top=230, bottom=261)  # bar at/below No
    lines = [
        _line("Allow this bash command?", top=60),
        _line("cmd", top=110),
        _line("1 Yes", top=180, left=100, w=120),
        _line("3 No", top=220, left=100, w=120),
    ]
    det = detect_prompt(lines, MON, image=img)
    assert det is not None
    # Falls back to the OCR'd "1 Yes" (top 180), not the below-No bar.
    assert det.yes_click_y == (180 + 12)


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


# ----- Codex VS Code approval cards ---------------------------------------


def test_detects_codex_ui_allow_once_button():
    lines = [
        _line("Terminal", top=20, left=22, w=60, h=14),
        _line(
            "May I let Supabase CLI inspect the local Docker database to run the final database lint",
            top=50, left=22, w=520, h=14,
        ),
        _line("on the new migrations?", top=72, left=22, w=180, h=14),
        _line("supabase db lint --level warning", top=112, left=26, w=280, h=14),
        _line("Deny", top=148, left=414, w=38, h=16),
        _line("Allow once", top=148, left=470, w=72, h=16),
    ]

    det = detect_prompt(lines, MON)

    assert det is not None
    assert det.source == "codex-ui"
    assert det.yes.text == "Allow once"
    assert det.no.text == "Deny"
    assert det.yes_click_x == 506
    assert det.yes_click_y == 156
    assert "supabase db lint --level warning" in det.command_text


def test_codex_ui_monitor_offset_applied():
    mon = Monitor(index=2, left=1920, top=100, width=1920, height=1080)
    lines = [
        _line("Run tests?", top=50, left=20, w=100),
        _line("pytest", top=80, left=20, w=80),
        _line("Deny", top=130, left=300, w=50),
        _line("Allow once", top=130, left=370, w=100),
    ]

    det = detect_prompt(lines, mon)

    assert det is not None
    assert det.yes_click_x == 1920 + 420
    assert det.yes_click_y == 100 + 142


def test_detects_codex_ui_when_ocr_merges_allow_once():
    # RapidOCR reads the attached VS Code screenshot as "Allowonce".
    lines = [
        _line("May I run this command?", top=50, left=20, w=200),
        _line("pytest", top=90, left=20, w=80),
        _line("Deny", top=148, left=419, w=32, h=18),
        _line("Allowonce", top=150, left=474, w=61, h=12),
    ]

    det = detect_prompt(lines, MON)

    assert det is not None
    assert det.source == "codex-ui"
    assert det.yes.text == "Allowonce"
    assert (det.yes_click_x, det.yes_click_y) == (504, 156)


def test_ignores_allow_once_without_deny_button():
    lines = [
        _line("Documentation: choose Allow once to continue", top=50),
        _line("Allow once", top=100, left=300, w=100),
    ]
    assert detect_prompt(lines, MON) is None


def test_ignores_allow_once_when_deny_is_not_on_same_row():
    lines = [
        _line("Deny", top=20, left=100, w=60),
        _line("Some unrelated content", top=100),
        _line("Allow once", top=220, left=300, w=100),
    ]
    assert detect_prompt(lines, MON) is None
