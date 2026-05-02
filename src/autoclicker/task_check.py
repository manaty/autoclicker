"""Ask OpenAI whether the AI assistant has finished its task.

Called when a monitored window has been visually idle for more than its
session's ``idle_threshold_s``. We feed the latest OCR text plus the
user-defined goals; the model returns ``done`` / ``not_done`` /
``unknown`` (we fail closed to ``unknown``).
"""
from dataclasses import dataclass
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from .config import Config


SYSTEM_PROMPT = """You analyse the visible state of an AI coding assistant
(Claude Code or Codex CLI) running inside a chat pane. Decide whether the
assistant has finished its current task.

You receive:
  - GOALS: a list of objectives the user gave to the assistant.
  - VISIBLE: recent OCR text from the assistant's pane.

Reply with exactly one of:
  - done: the assistant has completed (or near-completed) the goals,
    is now idle / awaiting user input, no remaining clear next step.
  - not_done: the assistant is mid-task, blocked on an error or tool
    output, has clearly more to do, or a confirmation prompt is up.
  - unknown: not enough signal — text is too garbled, off-topic, or
    you cannot tell the assistant's state.

Prefer 'unknown' over a confident wrong answer. The user can always
override manually. The reason field is one short sentence."""


class TaskVerdict(BaseModel):
    status: Literal["done", "not_done", "unknown"] = Field(
        description="Whether the AI assistant has finished its task."
    )
    reason: str = Field(description="One short sentence explaining the verdict.")


@dataclass
class TaskCheckResult:
    verdict: TaskVerdict
    error: Optional[str] = None


def _format_goals(goals: List[str]) -> str:
    if not goals:
        return "(no explicit goals provided)"
    return "\n".join(f"- {g.strip()}" for g in goals if g.strip())


def check_task_done(
    goals: List[str],
    visible_text: str,
    cfg: Config,
) -> TaskCheckResult:
    api_key = cfg.resolved_api_key()
    if not api_key:
        return TaskCheckResult(
            verdict=TaskVerdict(
                status="unknown",
                reason="No OPENAI_API_KEY; task check skipped.",
            ),
        )

    from openai import OpenAI

    client = OpenAI(api_key=api_key, timeout=cfg.task_check_timeout_s)

    user_msg = (
        f"GOALS:\n{_format_goals(goals)}\n\n"
        f"VISIBLE:\n```\n{visible_text.strip()[:6000]}\n```"
    )

    last_error: Optional[str] = None
    primary, fallback = cfg.resolved_task_check_models()
    for model in (primary, fallback):
        try:
            resp = client.chat.completions.parse(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                response_format=TaskVerdict,
            )
            verdict = resp.choices[0].message.parsed
            if verdict is None:
                continue
            return TaskCheckResult(verdict=verdict)
        except Exception as exc:  # noqa: BLE001
            last_error = f"{model}: {exc}"
            continue

    return TaskCheckResult(
        verdict=TaskVerdict(
            status="unknown",
            reason="Classifier unreachable; treating as unknown.",
        ),
        error=last_error,
    )
