import json
import os
from dataclasses import asdict, replace
from pathlib import Path

from boss_say_hello.domain import AppConfig, config_from_dict


class ConfigStore:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "settings.json"

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig()
        config = config_from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        return replace(config, auto_send=False, send_attachment=False)

    def save(self, config: AppConfig) -> AppConfig:
        config.validate()
        self.root.mkdir(parents=True, exist_ok=True)
        old_revision = self.load().revision if self.path.exists() else 0
        saved = replace(config, revision=max(old_revision, config.revision) + 1)
        temporary = self.path.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(asdict(saved), stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return saved
