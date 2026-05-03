from autoclicker.log_ticker import LogTicker


def test_global_messages_round_robin():
    t = LogTicker()
    t.add("first")
    t.add("second")
    text = t.text_for(key=None)
    assert "first" in text
    assert "second" in text
    assert "second" in text.split("first", 1)[1]  # second appears after first


def test_per_key_isolation():
    t = LogTicker()
    t.add("alpha event", key="WindowA")
    t.add("beta event", key="WindowB")
    a = t.text_for(key="WindowA", fallback_global=False)
    b = t.text_for(key="WindowB", fallback_global=False)
    assert "alpha event" in a and "beta event" not in a
    assert "beta event" in b and "alpha event" not in b


def test_fallback_to_global_when_no_per_key():
    t = LogTicker()
    t.add("global only")
    text = t.text_for(key="UnknownWindow", fallback_global=True)
    assert "global only" in text


def test_no_fallback_returns_empty():
    t = LogTicker()
    t.add("global only")
    assert t.text_for(key="UnknownWindow", fallback_global=False) == ""


def test_max_per_key_byte_eviction():
    """The per-key buffer is capped by *bytes* — old entries drop first."""
    t = LogTicker(max_per_key_bytes=200)  # ~3-4 short messages
    for i in range(20):
        t.add(f"long-message-{i:02d} with extra words", key="x")
    text = t.text_for(key="x", fallback_global=False)
    # Most recent should be kept.
    assert "long-message-19" in text
    # Earliest should be evicted under the byte cap.
    assert "long-message-00" not in text


def test_size_for_tracks_bytes():
    t = LogTicker()
    assert t.size_for(key="x") == 0
    t.add("hello", key="x")
    assert t.size_for(key="x") > 0


def test_lines_for_returns_timestamped_entries():
    t = LogTicker()
    t.add("first", key="x")
    t.add("second", key="x")
    entries = t.lines_for(key="x", fallback_global=False)
    assert len(entries) == 2
    assert entries[0][1] == "first"
    assert entries[1][1] == "second"
    assert isinstance(entries[0][0], float)


def test_clear_specific_key():
    t = LogTicker()
    t.add("a", key="x")
    t.add("b", key="y")
    t.clear(key="x")
    assert t.text_for(key="x", fallback_global=False) == ""
    # Global retains it (clear-by-key only drops the per-key buffer).
    assert "a" in t.text_for(key=None)
    assert "b" in t.text_for(key="y", fallback_global=False)


def test_clear_all():
    t = LogTicker()
    t.add("hello", key="x")
    t.clear()
    assert t.text_for(key=None) == ""
    assert t.text_for(key="x", fallback_global=False) == ""


def test_empty_messages_ignored():
    t = LogTicker()
    t.add("")
    t.add("   ")
    assert t.text_for(key=None) == ""
