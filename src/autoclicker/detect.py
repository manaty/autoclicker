import re
from dataclasses import dataclass
from typing import List, Optional

from .capture import Monitor
from .ocr import OcrLine


HEADER_RE = re.compile(r"allow\s+this\s+(bash\s+)?command", re.IGNORECASE)
YES_RE = re.compile(r"^\s*1\s*[\.\)]?\s*yes\b", re.IGNORECASE)
NO_RE = re.compile(r"^\s*[2-9]\s*[\.\)]?\s*no\b", re.IGNORECASE)


@dataclass
class Detection:
    monitor: Monitor
    command_text: str
    yes_click_x: int  # global screen coord
    yes_click_y: int  # global screen coord
    header: OcrLine
    yes: OcrLine
    no: OcrLine


def _find_header(lines: List[OcrLine]) -> Optional[OcrLine]:
    for ln in lines:
        if HEADER_RE.search(ln.text):
            return ln
    return None


def _find_after(lines: List[OcrLine], pattern: re.Pattern, after_y: int) -> Optional[OcrLine]:
    best: Optional[OcrLine] = None
    for ln in lines:
        if ln.top <= after_y:
            continue
        if pattern.match(ln.text.strip()):
            if best is None or ln.top < best.top:
                best = ln
    return best


def detect_prompt(lines: List[OcrLine], monitor: Monitor) -> Optional[Detection]:
    header = _find_header(lines)
    if header is None:
        return None

    yes_line = _find_after(lines, YES_RE, after_y=header.bottom)
    if yes_line is None:
        return None

    no_line = _find_after(lines, NO_RE, after_y=yes_line.top)
    if no_line is None:
        return None

    command_lines = [
        ln for ln in lines
        if ln.top > header.bottom and ln.bottom < yes_line.top
    ]
    command_text = "\n".join(ln.text.strip() for ln in command_lines if ln.text.strip())
    if not command_text:
        return None

    yes_cx = yes_line.center_x + monitor.left
    yes_cy = yes_line.center_y + monitor.top

    return Detection(
        monitor=monitor,
        command_text=command_text,
        yes_click_x=yes_cx,
        yes_click_y=yes_cy,
        header=header,
        yes=yes_line,
        no=no_line,
    )
