"""Version of this autoclicker build.

Replaced by the GitHub Actions workflow before PyInstaller bundles
the package: tag pushes write the tag name (e.g. ``v0.5.0``), other
builds write ``0.0.0-dev+<short-sha>``. Local source checkouts read
this file as-is — anything that doesn't parse as ``X.Y.Z`` is treated
as a dev build (auto-update is a no-op).
"""

__version__ = "0.0.0-dev"
