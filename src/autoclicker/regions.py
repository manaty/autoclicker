from typing import Optional

from pydantic import BaseModel, Field


class Region(BaseModel):
    """A rectangle on one monitor, in monitor-local pixel coords.

    If ``window_title_match`` is set, the autoclicker will bring the first
    visible window whose title contains that substring (case-insensitive)
    to the foreground before capturing this region — so multiple VSCode
    windows on the same monitor can each be monitored separately.
    """

    monitor_index: int = Field(..., ge=1)
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    w: int = Field(..., ge=1)
    h: int = Field(..., ge=1)
    window_title_match: Optional[str] = None
