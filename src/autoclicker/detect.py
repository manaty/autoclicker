import re
from dataclasses import dataclass
from typing import List, Optional

from .capture import Monitor
from .ocr import OcrLine


CLAUDE_HEADER_RE = re.compile(r"allow\s+this\s+(bash\s+)?command", re.IGNORECASE)
YES_RE = re.compile(r"^\s*1\s*[\.\)]?\s*yes\b", re.IGNORECASE)
CLAUDE_NO_RE = re.compile(r"^\s*[2-9]\s*[\.\)]?\s*no\b", re.IGNORECASE)
# A numbered menu option: "1. Yes", "2 Yes, allow…", "3No" (OCR often drops
# the space). A digit then optional dot/paren then a letter — the letter
# requirement keeps command fragments like "2>&1" from matching. Spacing is
# optional to stay consistent with YES_RE / CLAUDE_NO_RE, which OCR-merged
# lines still satisfy. The leading number tells us the option's position.
OPTION_RE = re.compile(r"^\s*([1-9])\s*[\.\)]?\s*[A-Za-z]")

# Codex CLI: option 3 always says "...tell Codex what to do differently".
# That string is the most reliable anchor — it stays in English even when
# the question above it is in another language.
CODEX_ANCHOR_RE = re.compile(r"tell\s+codex", re.IGNORECASE)
CODEX_OPT2_RE = re.compile(
    r"^\s*2\s*[\.\)]?\s*yes\b.*(don.?t\s+ask|ne\s+plus\s+demander|skip\s+confirmations)",
    re.IGNORECASE,
)


@dataclass
class Detection:
    monitor: Monitor
    command_text: str
    yes_click_x: int  # global screen coord
    yes_click_y: int  # global screen coord
    header: OcrLine
    yes: OcrLine
    no: OcrLine
    source: str = "claude"
    yes_source: str = "ocr"  # how the Yes target was located: bar|ocr|extrapolated


def _find_after(lines: List[OcrLine], pattern: re.Pattern, after_y: int) -> Optional[OcrLine]:
    best: Optional[OcrLine] = None
    for ln in lines:
        if ln.top <= after_y:
            continue
        if pattern.match(ln.text.strip()):
            if best is None or ln.top < best.top:
                best = ln
    return best


# How far above the "No" option (in No-line-heights) we still treat numbered
# lines as part of the same menu cluster. Generous enough to span a wrapped
# multi-line option, tight enough to exclude the command block above.
_OPTION_GAP_LINES = 8
# When no "Allow this command" header is found, the question/command text is
# taken from the lines above option 1, up to this many line-heights up.
_NO_HEADER_LOOKBACK_LINES = 25


def _option_number(text: str) -> Optional[int]:
    m = OPTION_RE.match(text.strip())
    return int(m.group(1)) if m else None


def find_selected_row(image, min_width_frac: float = 0.40, min_height_px: int = 6):
    """Locate the highlighted (selected) option row by its solid blue bar.

    Claude Code / Codex draw the currently-selected option — "Yes" by default
    when a confirm prompt first appears — as a full-width saturated-blue bar.
    That bar is far more reliable to find than OCR of the (light-on-blue) text
    drawn on top of it, which RapidOCR routinely drops.

    Returns ``(center_x, center_y)`` in image-local pixels for the *topmost*
    such bar (option 1 / the default selection sits at the top), or ``None``.
    """
    try:
        import numpy as np
    except Exception:  # pragma: no cover - numpy always present at runtime
        return None
    if image is None or getattr(image, "ndim", 0) != 3 or image.shape[2] < 3:
        return None

    # mss frames are BGR(A); channel 0=blue, 1=green, 2=red.
    b = image[:, :, 0].astype("int16")
    g = image[:, :, 1].astype("int16")
    r = image[:, :, 2].astype("int16")
    # A "selection blue" pixel: blue clearly dominant over red and green.
    blue = (b > 90) & (b - r > 35) & (b - g > 12)
    h, w = blue.shape
    if h == 0 or w == 0:
        return None

    row_frac = blue.sum(axis=1) / float(w)
    hot = row_frac >= min_width_frac
    if not hot.any():
        return None

    # Topmost contiguous run of "hot" rows = the bar of the top-most selected
    # option (Yes, by default).
    rows = np.where(hot)[0]
    start = int(rows[0])
    end = start
    for y in rows[1:]:
        if int(y) == end + 1:
            end = int(y)
        else:
            break
    if (end - start + 1) < min_height_px:
        return None

    band = blue[start : end + 1, :]
    cols = np.where(band.any(axis=0))[0]
    if len(cols) == 0:
        return None
    cx = (int(cols[0]) + int(cols[-1])) // 2
    cy = (start + end) // 2
    return cx, cy


def _detect_claude(
    lines: List[OcrLine], monitor: Monitor, image=None
) -> Optional[Detection]:
    """Detect a Claude Code confirmation prompt and locate its "Yes" option.

    Anchors on the "[2-9] No" option — every confirm menu has one, and it
    reads cleanly even when the header text is missing/garbled. "Yes" is
    always option 1, directly above. Crucially, the *selected* option (Yes,
    by default) is drawn with a full-width highlight bar that RapidOCR often
    fails to read, so we can't rely on the "1 Yes" line being present. When
    it is missing we extrapolate option 1's row from the evenly-spaced
    sibling options, which OCR does read.
    """
    no_lines = [ln for ln in lines if CLAUDE_NO_RE.match(ln.text.strip())]
    if not no_lines:
        return None
    # Lowest "No" on screen = the live prompt (older ones scrolled up).
    no_line = max(no_lines, key=lambda l: l.top)
    no_num = _option_number(no_line.text)
    if no_num is None or no_num < 2:
        return None
    line_h = max(1, no_line.bottom - no_line.top)

    # Collect the numbered options clustered just above (and including) No.
    cluster_top = no_line.top - line_h * _OPTION_GAP_LINES
    options: dict[int, OcrLine] = {}
    for ln in lines:
        if ln.bottom <= cluster_top or ln.top > no_line.bottom:
            continue
        num = _option_number(ln.text)
        if num is None or num > no_num:
            continue
        # Prefer the lowest line for a given number (the live menu).
        if num not in options or ln.top > options[num].top:
            options[num] = ln
    options[no_num] = no_line

    # Primary locator: the blue highlight bar of the selected option. It's the
    # default selection (Yes) and is immune to the OCR failures that plague the
    # light-on-blue option text. Only trust a bar that sits above the No row
    # (i.e. a "Yes"-side option is selected, not No).
    bar = find_selected_row(image) if image is not None else None
    yes_opt = options.get(1)
    if bar is not None and bar[1] < no_line.top:
        yes_x, yes_y = bar
        yes_top = yes_y
        yes_source = "bar"
    elif yes_opt is not None and YES_RE.match(yes_opt.text.strip()):
        yes_x = yes_opt.center_x
        yes_y = yes_opt.center_y
        yes_top = yes_opt.top
        yes_source = "ocr"
    else:
        yes_source = "extrapolated"
        # "1 Yes" wasn't read — its row is drawn with a highlight bar that
        # RapidOCR drops. Extrapolate its position assuming uniform row
        # spacing, using a reference row above No and its option distance.
        ref: Optional[OcrLine] = None
        ref_dist = 0  # how many option rows ref sits above No
        lower = sorted((n for n in options if n < no_num), reverse=True)
        if lower:
            # Best case: another option's number was read.
            ref = options[lower[0]]
            ref_dist = no_num - lower[0]
        else:
            # The siblings' numbers were dropped too (common with long option
            # text). The nearest OCR line above No is option (no_num - 1) —
            # use its text row even though its number is unreadable.
            above = [
                ln for ln in lines
                if ln.bottom <= no_line.top and ln.top > cluster_top and ln.text.strip()
            ]
            if above:
                cand = max(above, key=lambda l: l.top)  # nearest above No
                # Sanity: one option row is ~1–3 text-line-heights tall; reject
                # a reference that's really the command block far above.
                if 0 < (no_line.top - cand.top) <= line_h * 3:
                    ref = cand
                    ref_dist = 1
        if ref is None or ref_dist <= 0:
            return None
        pitch = (no_line.top - ref.top) / ref_dist
        if pitch <= 0:
            return None
        yes_top = int(round(no_line.top - pitch * (no_num - 1)))
        yes_y = int(yes_top + line_h / 2)
        # Land within row 1, a little right of the number gutter; the whole
        # option row is clickable so exact x doesn't matter much.
        yes_x = int(ref.left + line_h)

    headers = [
        ln for ln in lines
        if CLAUDE_HEADER_RE.search(ln.text) and ln.bottom <= yes_top
    ]
    header = max(headers, key=lambda l: l.bottom) if headers else None
    top_bound = header.bottom if header is not None else yes_top - line_h * _NO_HEADER_LOOKBACK_LINES

    command_lines = [
        ln for ln in lines
        if ln.top > top_bound and ln.bottom <= yes_top
        and ln.text.strip() and _option_number(ln.text) is None
    ]
    command_text = "\n".join(ln.text.strip() for ln in command_lines)
    if not command_text:
        # Command block unreadable too — still click, but keep the text
        # distinct per prompt so the dedup cache doesn't collapse prompts.
        command_text = no_line.text.strip()

    return Detection(
        monitor=monitor,
        command_text=command_text,
        yes_click_x=yes_x + monitor.left,
        yes_click_y=yes_y + monitor.top,
        header=header or (command_lines[0] if command_lines else no_line),
        yes=yes_opt or no_line,
        no=no_line,
        source="claude",
        yes_source=yes_source,
    )


def _detect_codex(lines: List[OcrLine], monitor: Monitor) -> Optional[Detection]:
    anchor = next((ln for ln in lines if CODEX_ANCHOR_RE.search(ln.text)), None)
    if anchor is None:
        return None

    # The Yes line (option 1) sits above the anchor — pick the closest match.
    yes_line: Optional[OcrLine] = None
    for ln in lines:
        if ln.bottom > anchor.top:
            continue
        if YES_RE.match(ln.text.strip()):
            if yes_line is None or ln.top > yes_line.top:
                yes_line = ln
    if yes_line is None:
        return None

    # Optional sanity check: "2. Yes, and don't ask again ..." between Yes and anchor.
    # If we can find it, we'll use it as the upper bound for "above" content.
    opt2 = next(
        (
            ln for ln in lines
            if ln.top > yes_line.top and ln.bottom < anchor.top + 1
            and CODEX_OPT2_RE.match(ln.text.strip())
        ),
        None,
    )
    _ = opt2  # not strictly required, kept for future use

    above = [ln for ln in lines if ln.bottom <= yes_line.top and ln.text.strip()]
    if not above:
        return None
    # Drop lines that are far above (more than 25× line-height) — likely unrelated UI.
    line_h = max(1, yes_line.bottom - yes_line.top)
    cutoff = yes_line.top - line_h * 25
    above = [ln for ln in above if ln.bottom >= cutoff]
    if not above:
        return None
    command_text = "\n".join(ln.text.strip() for ln in above)

    return Detection(
        monitor=monitor,
        command_text=command_text,
        yes_click_x=yes_line.center_x + monitor.left,
        yes_click_y=yes_line.center_y + monitor.top,
        header=above[0],
        yes=yes_line,
        no=anchor,
        source="codex",
    )


def detect_prompt(lines: List[OcrLine], monitor: Monitor, image=None) -> Optional[Detection]:
    # When the "Allow this command" header is present, Claude wins outright.
    # Otherwise prefer Codex's "tell Codex" anchor (so its prompts keep their
    # source label) and only then fall back to the header-less Claude menu —
    # this is what catches edit / "Do you want to proceed?" prompts and the
    # common case where OCR drops the header line. ``image`` (the region frame)
    # lets Claude detection locate the Yes option by its blue highlight bar.
    if any(CLAUDE_HEADER_RE.search(ln.text) for ln in lines):
        claude = _detect_claude(lines, monitor, image)
        if claude is not None:
            return claude
    return _detect_codex(lines, monitor) or _detect_claude(lines, monitor, image)
