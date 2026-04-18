import time

from autoclicker.state import Cooldown, DedupCache


def test_dedup_blocks_same_command():
    cache = DedupCache(window_s=5.0)
    assert cache.seen_recently("ls", 1) is False
    assert cache.seen_recently("ls", 1) is True


def test_dedup_distinguishes_monitor():
    cache = DedupCache(window_s=5.0)
    cache.seen_recently("ls", 1)
    assert cache.seen_recently("ls", 2) is False


def test_dedup_expires():
    cache = DedupCache(window_s=0.05)
    cache.seen_recently("ls", 1)
    time.sleep(0.1)
    assert cache.seen_recently("ls", 1) is False


def test_cooldown():
    cd = Cooldown(interval_s=0.1)
    assert cd.ready() is True
    cd.trigger()
    assert cd.ready() is False
    time.sleep(0.15)
    assert cd.ready() is True
