from autoclicker.idle import IdleRegistry, WindowIdleState


def test_observe_first_call_sets_baseline():
    st = WindowIdleState(title_match="x")
    assert st.observe("hello", now=100.0) is True
    assert st.last_change_at == 100.0


def test_observe_same_text_does_not_update():
    st = WindowIdleState(title_match="x")
    st.observe("hello", now=100.0)
    assert st.observe("hello", now=200.0) is False
    assert st.last_change_at == 100.0


def test_observe_different_text_updates():
    st = WindowIdleState(title_match="x")
    st.observe("hello", now=100.0)
    assert st.observe("hello world", now=130.0) is True
    assert st.last_change_at == 130.0


def test_idle_for_returns_zero_until_observed():
    st = WindowIdleState(title_match="x")
    assert st.idle_for(now=999.0) == 0.0


def test_idle_for_grows_when_static():
    st = WindowIdleState(title_match="x")
    st.observe("hello", now=100.0)
    assert st.idle_for(now=160.0) == 60.0
    st.observe("hello", now=160.0)  # same text — still idle
    assert st.idle_for(now=160.0) == 60.0


def test_can_act_first_call():
    st = WindowIdleState(title_match="x")
    assert st.can_act(cooldown_s=300.0, now=10.0) is True


def test_can_act_respects_cooldown():
    st = WindowIdleState(title_match="x")
    st.mark_acted(now=100.0)
    assert st.can_act(cooldown_s=300.0, now=200.0) is False
    assert st.can_act(cooldown_s=300.0, now=400.0) is True


def test_mark_acted_resets_idle_clock():
    st = WindowIdleState(title_match="x")
    st.observe("hello", now=100.0)
    st.mark_acted(now=200.0)
    # After acting, the change clock is reset so we don't re-trigger immediately.
    assert st.idle_for(now=210.0) == 10.0


def test_idle_registry_creates_per_match():
    reg = IdleRegistry()
    a = reg.get("autoclicker - VSC")
    b = reg.get("issue-manager - VSC")
    assert a is not b
    assert reg.get("autoclicker - VSC") is a


def test_idle_registry_forget():
    reg = IdleRegistry()
    a = reg.get("x")
    reg.forget("x")
    assert reg.get("x") is not a


def test_last_verdict_starts_none_and_is_writable():
    """Used to require two consecutive 'done' verdicts before parking."""
    st = WindowIdleState(title_match="x")
    assert st.last_verdict is None
    st.last_verdict = "done"
    assert st.last_verdict == "done"
    st.last_verdict = "not_done"
    assert st.last_verdict == "not_done"
