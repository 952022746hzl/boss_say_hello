import os
import shutil
import sqlite3
import sys
from pathlib import Path
from uuid import uuid4

from platformdirs import user_data_path


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    source_root = Path(__file__).resolve().parents[3]
    return source_root if (source_root / "pyproject.toml").exists() else Path.cwd().resolve()


def data_root() -> Path:
    program = application_dir()
    root = program / "workspace"
    legacy = user_data_path("BossSayHello", appauthor=False)
    if not root.exists() and legacy.is_dir():
        # Stage a copy before publishing it; never delete or modify the original.
        staging = program / ".cache" / f"workspace-migration-{uuid4().hex}"
        staging.mkdir(parents=True)
        for folder in ("logs", "resumes", "browser_profile"):
            if (legacy / folder).is_dir():
                shutil.copytree(
                    legacy / folder, staging / folder, ignore=shutil.ignore_patterns("Singleton*", "lockfile")
                )
        if (legacy / "settings.json").is_file():
            (staging / "config").mkdir()
            shutil.copy2(legacy / "settings.json", staging / "config" / "settings.json")
        if (legacy / "records.sqlite3").is_file():
            source = sqlite3.connect((legacy / "records.sqlite3").as_uri() + "?mode=ro", uri=True)
            destination = sqlite3.connect(staging / "records.sqlite3")
            try:
                source.backup(destination)
            finally:
                destination.close()
                source.close()
        os.replace(staging, root)
    root.mkdir(parents=True, exist_ok=True)
    return root
