import json
import os
from typing import List, Optional

from pydantic import BaseModel, Field

from .paths import config_path
from .regions import Region
from .sessions import WindowSession


DEFAULT_MODEL = "gpt-5.4-nano-2026-03-17"
DEFAULT_MODEL_FALLBACK = "gpt-5.4-nano"


class Config(BaseModel):
    openai_api_key: Optional[str] = None

    # Default model for both classifiers when a per-classifier override
    # isn't set. Kept for backwards compatibility with older configs.
    model: str = DEFAULT_MODEL
    model_fallback: str = DEFAULT_MODEL_FALLBACK

    # Per-classifier overrides. Safety runs every poll cycle, so latency
    # matters → keep it on a fast model. Task-check runs at most once
    # per session.idle_threshold_s, so a slower reasoning model is fine.
    safety_model: Optional[str] = None
    safety_model_fallback: Optional[str] = None
    task_check_model: Optional[str] = None
    task_check_model_fallback: Optional[str] = None

    poll_interval_ms: int = Field(default=5000, ge=200, le=30_000)
    armed_on_start: bool = False
    log_level: str = "INFO"
    click_cooldown_s: float = 2.0
    dedup_window_s: float = 5.0
    user_activity_radius_px: int = 50
    openai_timeout_s: float = 4.0
    task_check_timeout_s: float = 8.0
    # If True (default), API errors don't block the autoclicker:
    #   - safety classifier → click Yes anyway (api-error-fail-open category).
    #   - task-done check   → behave as 'not_done' (send the Continue prompt).
    # Set to False to revert to fail-closed (safe but useless when API is flaky).
    fail_open_on_api_error: bool = True
    regions: List[Region] = Field(default_factory=list)
    window_sessions: List[WindowSession] = Field(default_factory=list)

    def resolved_api_key(self) -> Optional[str]:
        return self.openai_api_key or os.environ.get("OPENAI_API_KEY")

    def resolved_safety_models(self) -> tuple[str, str]:
        return (
            self.safety_model or self.model,
            self.safety_model_fallback or self.model_fallback,
        )

    def resolved_task_check_models(self) -> tuple[str, str]:
        return (
            self.task_check_model or self.model,
            self.task_check_model_fallback or self.model_fallback,
        )


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
