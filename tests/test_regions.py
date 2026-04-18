from autoclicker.capture import Monitor
from autoclicker.detect import detect_prompt
from autoclicker.ocr import OcrLine

try:
    from autoclicker.regions import Region
    _HAS_PYDANTIC = True
except ModuleNotFoundError:
    _HAS_PYDANTIC = False


def test_region_model_validates():
    if not _HAS_PYDANTIC:
        return
    r = Region(monitor_index=1, x=100, y=200, w=400, h=300)
    assert r.monitor_index == 1
    assert (r.x, r.y, r.w, r.h) == (100, 200, 400, 300)


def test_detect_on_virtual_region_monitor():
    """When a region is grabbed, capture.grab_region synthesises a Monitor whose
    left/top already point at the region's top-left in global coords. Any OCR
    bbox in the cropped image plus that offset should land on the real pixel.
    """
    # Region placed on a second monitor at (1920, 0); user drew a 600x400 box
    # offset by (300, 100) inside that monitor.
    virtual = Monitor(
        index=2,
        left=1920 + 300,
        top=0 + 100,
        width=600,
        height=400,
    )

    # OCR lines are relative to the cropped image (0-based).
    lines = [
        OcrLine(text="Allow this bash command?", left=20, top=10, right=400, bottom=34, confidence=0.99),
        OcrLine(text="ls -la", left=40, top=50, right=160, bottom=74, confidence=0.99),
        OcrLine(text="1 Yes", left=40, top=120, right=160, bottom=148, confidence=0.99),
        OcrLine(text="2 No", left=40, top=160, right=160, bottom=188, confidence=0.99),
    ]

    det = detect_prompt(lines, virtual)
    assert det is not None
    # Global click coords = region origin + local bbox center.
    assert det.yes_click_x == 1920 + 300 + 100  # (40+160)//2 = 100
    assert det.yes_click_y == 0 + 100 + 134     # (120+148)//2 = 134
