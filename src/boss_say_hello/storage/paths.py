from pathlib import Path

from platformdirs import user_data_path


def data_root() -> Path:
    root = user_data_path("BossSayHello", appauthor=False)
    root.mkdir(parents=True, exist_ok=True)
    return root
