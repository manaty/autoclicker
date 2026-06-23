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


def _detect_claude(lines: List[OcrLine], monitor: Monitor) -> Optional[Detection]:
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

    yes_opt = options.get(1)
    if yes_opt is not None and YES_RE.match(yes_opt.text.strip()):
        yes_x = yes_opt.center_x
        yes_y = yes_opt.center_y
        yes_top = yes_opt.top
    else:
        # "1 Yes" wasn't read (highlight bar). Extrapolate its row from the
        # nearest lower-numbered sibling and No, assuming uniform row spacing.
        lower = sorted((n for n in options if n < no_num), reverse=True)
        if not lower:
            return None
        k = lower[0]
        ref = options[k]
        pitch = (no_line.top - ref.top) / (no_num - k)
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


def detect_prompt(lines: List[OcrLine], monitor: Monitor) -> Optional[Detection]:
    # When the "Allow this command" header is present, Claude wins outright.
    # Otherwise prefer Codex's "tell Codex" anchor (so its prompts keep their
    # source label) and only then fall back to the header-less Claude menu —
    # this is what catches edit / "Do you want to proceed?" prompts and the
    # common case where OCR drops the header line.
    if any(CLAUDE_HEADER_RE.search(ln.text) for ln in lines):
        claude = _detect_claude(lines, monitor)
        if claude is not None:
            return claude
    return _detect_codex(lines, monitor) or _detect_claude(lines, monitor)
