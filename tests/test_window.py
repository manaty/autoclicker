"""Tests for the Win32 helpers in autoclicker.window.

The Win32 calls themselves are skipped on non-Windows. The pure-Python
bits — title heuristic, dataclass shape — are tested everywhere.
"""
import sys

import pytest

from autoclicker.window import WindowInfo, list_windows, stable_title_match


def test_stable_title_vscode_workspace():
    title = "entry.py - autoclicker - Visual Studio Code"
    assert stable_title_match(title) == "autoclicker - Visual Studio Code"


def test_stable_title_vscode_with_modified_marker():
    title = "● fix.md - autoclicker - Visual Studio Code"
    assert stable_title_match(title) == "autoclicker - Visual Studio Code"


def test_stable_title_vscode_no_workspace_separator():
    # Some VSCode states don't include the workspace name (e.g., welcome tab).
    title = "Welcome - Visual Studio Code"
    # No " - " before " - Visual Studio Code" → keep full title.
    assert stable_title_match(title) == title


def test_stable_title_non_vscode_passthrough():
    title = "Untitled - Notepad"
    assert stable_title_match(title) == title


def test_stable_title_multi_segment_workspace():
    # Workspace itself contains hyphens — last segment before VSC suffix wins.
    title = "main.go - my-cool-repo - Visual Studio Code"
    assert stable_title_match(title) == "my-cool-repo - Visual Studio Code"


def test_window_info_dimensions():
    w = WindowInfo(hwnd=1, title="x", left=100, top=50, right=900, bottom=650)
    assert w.width == 800
    assert w.height == 600


@pytest.mark.skipif(sys.platform != "win32", reason="Win32-only")
def test_list_windows_returns_something():
    wins = list_windows()
    assert isinstance(wins, list)
    # Even a freshly-booted Windows session has a desktop window.
    assert all(isinstance(w, WindowInfo) for w in wins)


def test_list_windows_empty_on_non_windows():
    if sys.platform == "win32":
        pytest.skip("only meaningful off-Windows")
    assert list_windows() == []
