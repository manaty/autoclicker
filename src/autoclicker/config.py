import json
import os
from typing import List, Optional

from pydantic import BaseModel, Field

from .paths import config_path
from .regions import Region


DEFAULT_MODEL = "gpt-5.4-nano-2026-03-17"
DEFAULT_MODEL_FALLBACK = "gpt-5.4-nano"


class Config(BaseModel):
    openai_api_key: Optional[str] = None
    model: str = DEFAULT_MODEL
    model_fallback: str = DEFAULT_MODEL_FALLBACK
    poll_interval_ms: int = Field(default=5000, ge=200, le=30_000)
    armed_on_start: bool = False
    log_level: str = "INFO"
    click_cooldown_s: float = 2.0
    dedup_window_s: float = 5.0
    user_activity_radius_px: int = 50
    openai_timeout_s: float = 4.0
    regions: List[Region] = Field(default_factory=list)

    def resolved_api_key(self) -> Optional[str]:
        return self.openai_api_key or os.environ.get("OPENAI_API_KEY")


def load_config() -> Config:
    path = config_path()
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return Config(**raw)
        except Exception:
            pass
    return Config()


def save_config(cfg: Config) -> None:
    path = config_path()
    path.write_text(cfg.model_dump_json(indent=2), encoding="utf-8")
