from pydantic import BaseModel, Field


class Region(BaseModel):
    """A rectangle on one monitor, in monitor-local pixel coords."""

    monitor_index: int = Field(..., ge=1)
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)
    w: int = Field(..., ge=1)
    h: int = Field(..., ge=1)
