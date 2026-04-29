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


def _detect_claude(lines: List[OcrLine], monitor: Monitor) -> Optional[Detection]:
    header = next((ln for ln in lines if CLAUDE_HEADER_RE.search(ln.text)), None)
    if header is None:
        return None

    yes_line = _find_after(lines, YES_RE, after_y=header.bottom)
    if yes_line is None:
        return None

    no_line = _find_after(lines, CLAUDE_NO_RE, after_y=yes_line.top)
    if no_line is None:
        return None

    command_lines = [
        ln for ln in lines
        if ln.top > header.bottom and ln.bottom < yes_line.top
    ]
    command_text = "\n".join(ln.text.strip() for ln in command_lines if ln.text.strip())
    if not command_text:
        return None

    return Detection(
        monitor=monitor,
        command_text=command_text,
        yes_click_x=yes_line.center_x + monitor.left,
        yes_click_y=yes_line.center_y + monitor.top,
        header=header,
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
    return _detect_claude(lines, monitor) or _detect_codex(lines, monitor)
