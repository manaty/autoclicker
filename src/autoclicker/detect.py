import re
from dataclasses import dataclass
from typing import List, Optional

from .capture import Monitor
from .ocr import OcrLine


CLAUDE_HEADER_RE = re.compile(r"allow\s+this\s+(bash\s+)?command", re.IGNORECASE)
YES_RE = re.compile(r"^\s*1\s*[\.\)]?\s*yes\b", re.IGNORECASE)
CLAUDE_NO_RE = re.compile(r"^\s*[2-9]\s*[\.\)]?\s*no\b", re.IGNORECASE)

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


# Max vertical gap between the "Yes" and "No" options, in Yes-line-heights,
# for them to count as one menu. Keeps a stray "1. Yes" from pairing with an
# unrelated "[2-9]. No" elsewhere in the pane.
_OPTION_GAP_LINES = 6
# When no "Allow this command" header is found, the question/command text is
# taken from the lines above the "Yes" option, up to this many line-heights up.
_NO_HEADER_LOOKBACK_LINES = 25


def _find_option_pair(lines: List[OcrLine]):
    """Find the active "1. Yes" / "[2-9]. No" option menu.

    Returns (yes_line, no_line) or None. When several Yes options are on
    screen (scrolled-back prompts in the chat pane), prefer the *lowest* one
    that has a matching No just below it — that's the live prompt.
    """
    candidates = [ln for ln in lines if YES_RE.match(ln.text.strip())]
    for yes_line in sorted(candidates, key=lambda l: l.top, reverse=True):
        line_h = max(1, yes_line.bottom - yes_line.top)
        no_line: Optional[OcrLine] = None
        for ln in lines:
            if ln.top <= yes_line.top:
                continue
            if ln.top - yes_line.bottom > line_h * _OPTION_GAP_LINES:
                continue
            if CLAUDE_NO_RE.match(ln.text.strip()):
                if no_line is None or ln.top < no_line.top:
                    no_line = ln
        if no_line is not None:
            return yes_line, no_line
    return None


def _detect_claude(lines: List[OcrLine], monitor: Monitor) -> Optional[Detection]:
    """Detect a Claude Code confirmation prompt.

    Anchors on the "1. Yes" / "[2-9]. No" option menu rather than the header
    text: Claude shows many prompt headers ("Allow this bash command?", "Do
    you want to make this edit?", "Do you want to proceed?", …) and OCR often
    misreads the long header line while reading the short option lines
    cleanly. The option pair is the one reliable signal. The "Allow this
    command" header, when present, is only used to bound the command text.
    """
    pair = _find_option_pair(lines)
    if pair is None:
        return None
    yes_line, no_line = pair

    line_h = max(1, yes_line.bottom - yes_line.top)
    headers = [
        ln for ln in lines
        if CLAUDE_HEADER_RE.search(ln.text) and ln.bottom < yes_line.top
    ]
    header = max(headers, key=lambda l: l.bottom) if headers else None
    if header is not None:
        top_bound = header.bottom
    else:
        top_bound = yes_line.top - line_h * _NO_HEADER_LOOKBACK_LINES

    command_lines = [
        ln for ln in lines
        if ln.top > top_bound and ln.bottom < yes_line.top and ln.text.strip()
    ]
    command_text = "\n".join(ln.text.strip() for ln in command_lines)
    if not command_text:
        return None

    return Detection(
        monitor=monitor,
        command_text=command_text,
        yes_click_x=yes_line.center_x + monitor.left,
        yes_click_y=yes_line.center_y + monitor.top,
        header=header or command_lines[0],
        yes=yes_line,
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
