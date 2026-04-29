"""End-to-end visual test over the sample images in tests/.

For every image, runs RapidOCR → detect_prompt and writes two artefacts to
``tests/results/``:

  - ``<name>.ocr.txt``   — every detected line, its bbox, and its confidence.
  - ``<name>.annotated.png`` — the original image with, if a prompt was found,
    a red circle on the computed click target (center of "1 Yes").

Additionally asserts that ``empty_image.png`` is *not* detected and each
``yes_image_*.png`` *is* detected.

Skipped entirely unless ``rapidocr_onnxruntime`` and ``Pillow`` are installed.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

pytest.importorskip("rapidocr_onnxruntime")
pytest.importorskip("PIL")
pytest.importorskip("numpy")

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from autoclicker.capture import Monitor
from autoclicker.detect import Detection, detect_prompt
from autoclicker.ocr import Ocr, OcrLine


TESTS_DIR = Path(__file__).parent
RESULTS_DIR = TESTS_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)


def _format_ocr(lines: List[OcrLine]) -> str:
    out = [f"{len(lines)} line(s) detected\n"]
    for ln in lines:
        out.append(
            f"  bbox=({ln.left:4d},{ln.top:4d},{ln.right:4d},{ln.bottom:4d}) "
            f"conf={ln.confidence:.3f}  {ln.text!r}"
        )
    return "\n".join(out) + "\n"


def _annotate(src: Path, dst: Path, det: Detection | None, monitor: Monitor, lines: List[OcrLine]) -> None:
    img = Image.open(src).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")

    for ln in lines:
        draw.rectangle(
            (ln.left, ln.top, ln.right, ln.bottom),
            outline=(0, 200, 80, 180),
            width=1,
        )

    if det is not None:
        local_x = det.yes_click_x - monitor.left
        local_y = det.yes_click_y - monitor.top
        r = 18
        draw.ellipse(
            (local_x - r, local_y - r, local_x + r, local_y + r),
            outline=(255, 30, 30, 255),
            width=4,
        )
        draw.ellipse(
            (local_x - 3, local_y - 3, local_x + 3, local_y + 3),
            fill=(255, 30, 30, 255),
        )
        draw.text(
            (local_x + r + 6, local_y - 8),
            "click",
            fill=(255, 30, 30, 255),
        )

    img.save(dst)


_OCR_SINGLETON: Ocr | None = None


def _get_ocr() -> Ocr:
    global _OCR_SINGLETON
    if _OCR_SINGLETON is None:
        _OCR_SINGLETON = Ocr()
    return _OCR_SINGLETON


def _process(name: str) -> tuple[List[OcrLine], Detection | None]:
    src = TESTS_DIR / name
    image = np.asarray(Image.open(src).convert("RGB"), dtype=np.uint8)

    h, w = image.shape[:2]
    monitor = Monitor(index=1, left=0, top=0, width=w, height=h)

    ocr = _get_ocr()
    lines = ocr.run(image)
    det = detect_prompt(lines, monitor)

    stem = Path(name).stem
    (RESULTS_DIR / f"{stem}.ocr.txt").write_text(_format_ocr(lines), encoding="utf-8")

    text_block = ""
    if det is not None:
        text_block = (
            "\nDETECTED:\n"
            f"  header: {det.header.text!r}\n"
            f"  yes:    {det.yes.text!r}  -> click @ ({det.yes_click_x},{det.yes_click_y})\n"
            f"  no:     {det.no.text!r}\n"
            f"  command (between header and yes):\n"
            + "\n".join(f"    > {line}" for line in det.command_text.splitlines())
            + "\n"
        )
    else:
        text_block = "\nNOT DETECTED\n"
    with (RESULTS_DIR / f"{stem}.ocr.txt").open("a", encoding="utf-8") as f:
        f.write(text_block)

    _annotate(src, RESULTS_DIR / f"{stem}.annotated.png", det, monitor, lines)
    return lines, det


def test_empty_image_not_detected():
    _lines, det = _process("empty_image.png")
    assert det is None, "empty_image.png should produce no detection"


@pytest.mark.parametrize(
    "name,expected_source",
    [
        ("yes_image_1.png", "claude"),
        ("yes_image_2.png", "claude"),
        ("yes_image_3.png", "claude"),
        ("yes_image_4.png", "codex"),
    ],
)
def test_yes_image_detected(name: str, expected_source: str):
    lines, det = _process(name)
    assert det is not None, f"{name} should produce a detection (lines={len(lines)})"
    assert det.yes_click_x > 0 and det.yes_click_y > 0
    assert det.command_text.strip() != ""
    assert det.source == expected_source, (
        f"{name} expected source={expected_source!r} but got {det.source!r}"
    )
