"""Keep persistent data beside the program, independent of the caller's CWD."""

import sys
from pathlib import Path


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        # _MEIPASS is the bundled dependencies directory, not writable user data.
        return Path(sys.executable).resolve().parent
    source_root = Path(__file__).resolve().parents[3]
    if (source_root / "pyproject.toml").is_file():
        return source_root
    return Path(sys.argv[0]).resolve().parent


def data_root() -> Path:
    root = application_dir() / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    return root
