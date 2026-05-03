"""Unit tests for autoclicker.updater — pure-logic bits only.

The HTTP + filesystem swap parts are exercised via the live build on
Windows; here we just sanity-check the version parser, the asset
picker, and the comparison logic.
"""
from autoclicker import updater


def test_parse_handles_v_prefix():
    assert updater._parse("v0.4.0") == (0, 4, 0)
    assert updater._parse("0.4.0") == (0, 4, 0)


def test_parse_handles_dev_suffix():
    assert updater._parse("0.0.0-dev") == (0, 0, 0)
    assert updater._parse("0.0.0-dev+abc1234") == (0, 0, 0)
    assert updater._parse("1.2.3-rc1") == (1, 2, 3)


def test_parse_handles_short_versions():
    assert updater._parse("v1") == (1,)
    assert updater._parse("v1.2") == (1, 2)


def test_parse_returns_zero_on_garbage():
    assert updater._parse("") == (0,)
    assert updater._parse("not-a-version") == (0,)


def test_is_newer_basic():
    assert updater.is_newer("v0.5.0", "v0.4.0") is True
    assert updater.is_newer("v0.4.0", "v0.4.0") is False
    assert updater.is_newer("v0.4.0", "v0.5.0") is False


def test_is_newer_dev_loses_to_tagged():
    assert updater.is_newer("v0.4.0", "0.0.0-dev") is True
    assert updater.is_newer("0.0.0-dev", "v0.4.0") is False


def test_is_newer_minor_bump():
    assert updater.is_newer("v0.4.1", "v0.4.0") is True
    assert updater.is_newer("v1.0.0", "v0.99.99") is True


def test_pick_exe_asset_finds_versioned():
    rel = {
        "assets": [
            {"name": "Source code (zip)", "browser_download_url": "http://x/zip", "size": 1},
            {"name": "autoclicker-v0.5.0.exe", "browser_download_url": "http://x/exe", "size": 100},
        ]
    }
    picked = updater._pick_exe_asset(rel)
    assert picked is not None
    name, url, size = picked
    assert name == "autoclicker-v0.5.0.exe"
    assert url == "http://x/exe"
    assert size == 100


def test_pick_exe_asset_finds_legacy_unversioned():
    rel = {"assets": [{"name": "autoclicker.exe", "browser_download_url": "http://x", "size": 50}]}
    picked = updater._pick_exe_asset(rel)
    assert picked is not None
    assert picked[0] == "autoclicker.exe"


def test_pick_exe_asset_none_when_no_match():
    rel = {"assets": [{"name": "something-else.zip", "browser_download_url": "http://x", "size": 1}]}
    assert updater._pick_exe_asset(rel) is None


def test_pick_exe_asset_handles_missing_assets_key():
    assert updater._pick_exe_asset({}) is None
    assert updater._pick_exe_asset({"assets": None}) is None


def test_current_version_default_is_dev():
    # In source checkouts the stamped version isn't applied, so we expect
    # the dev placeholder.
    v = updater.current_version()
    assert v.startswith("0.0.0") or "dev" in v
