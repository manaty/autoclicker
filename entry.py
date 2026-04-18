"""PyInstaller entry point.

Importing ``autoclicker`` as a package first (instead of running
``autoclicker/__main__.py`` as a top-level script) ensures that the
relative imports inside the package resolve.
"""
import sys

from autoclicker.__main__ import main


if __name__ == "__main__":
    sys.exit(main())
