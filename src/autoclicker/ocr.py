from dataclasses import dataclass
from typing import List, Optional


@dataclass
class OcrLine:
    text: str
    left: int
    top: int
    right: int
    bottom: int
    confidence: float

    @property
    def center_x(self) -> int:
        return (self.left + self.right) // 2

    @property
    def center_y(self) -> int:
        return (self.top + self.bottom) // 2


class Ocr:
    """Thin wrapper over RapidOCR returning axis-aligned bboxes."""

    def __init__(self) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self._engine = RapidOCR()

    def run(self, image) -> List[OcrLine]:
        result, _ = self._engine(image)
        lines: List[OcrLine] = []
        if not result:
            return lines
        for entry in result:
            box, text, score = entry
            xs = [int(p[0]) for p in box]
            ys = [int(p[1]) for p in box]
            lines.append(
                OcrLine(
                    text=text,
                    left=min(xs),
                    top=min(ys),
                    right=max(xs),
                    bottom=max(ys),
                    confidence=float(score),
                )
            )
        lines.sort(key=lambda l: (l.top, l.left))
        return lines


def find_line(lines: List[OcrLine], needle: str, case_sensitive: bool = False) -> Optional[OcrLine]:
    if case_sensitive:
        for ln in lines:
            if needle in ln.text:
                return ln
    else:
        target = needle.lower()
        for ln in lines:
            if target in ln.text.lower():
                return ln
    return None
