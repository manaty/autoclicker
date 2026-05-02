import pytest

try:
    from autoclicker.sessions import WindowSession
    _HAS_PYDANTIC = True
except ModuleNotFoundError:
    _HAS_PYDANTIC = False


pytestmark = pytest.mark.skipif(not _HAS_PYDANTIC, reason="pydantic not installed")


def test_session_defaults():
    s = WindowSession(
        title_match="autoclicker - Visual Studio Code",
        prompt_input_x=100, prompt_input_y=200,
    )
    assert s.goals == []
    assert s.idle_threshold_s == 60.0
    assert s.cooldown_s == 300.0
    assert s.completed is False


def test_session_matches_substring_case_insensitive():
    s = WindowSession(
        title_match="autoclicker - Visual Studio Code",
        prompt_input_x=0, prompt_input_y=0,
    )
    assert s.matches("entry.py - autoclicker - Visual Studio Code")
    assert s.matches("ENTRY - AUTOCLICKER - VISUAL STUDIO CODE")
    assert not s.matches("issue-manager - Visual Studio Code")


def test_session_rejects_too_short_thresholds():
    with pytest.raises(Exception):
        WindowSession(
            title_match="x",
            prompt_input_x=0, prompt_input_y=0,
            idle_threshold_s=1.0,  # below the >=10 floor
        )


def test_session_completed_flag_persists():
    s = WindowSession(
        title_match="x", prompt_input_x=0, prompt_input_y=0,
        completed=True,
    )
    assert s.completed is True
    dumped = s.model_dump()
    assert dumped["completed"] is True
