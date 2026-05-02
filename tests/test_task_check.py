"""Online tests for the task-done classifier (skipped without OPENAI_API_KEY)."""
import os

import pytest

from autoclicker.config import Config
from autoclicker.task_check import check_task_done


_HAS_KEY = bool(os.environ.get("OPENAI_API_KEY"))
online_only = pytest.mark.skipif(not _HAS_KEY, reason="OPENAI_API_KEY not set")


def test_no_key_returns_unknown(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = Config(openai_api_key=None)
    res = check_task_done(["do X"], "anything", cfg)
    assert res.verdict.status == "unknown"


@online_only
def test_done_signal_recognized():
    cfg = Config()
    visible = (
        "I've added the Codex prompt detector and the worker watchdog.\n"
        "All tests pass. Let me know if you want anything else."
    )
    res = check_task_done(["Add Codex detection", "Worker watchdog"], visible, cfg)
    assert res.verdict.status == "done", res.verdict.reason


@online_only
def test_in_progress_signal_recognized():
    cfg = Config()
    visible = (
        "Reading file capture.py...\n"
        "Editing capture.py: added per-thread mss instance.\n"
        "Running tests..."
    )
    res = check_task_done(["fix mss thread-safety"], visible, cfg)
    assert res.verdict.status in ("not_done", "unknown"), res.verdict.reason
