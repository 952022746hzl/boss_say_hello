import hashlib
import json
import time
from pathlib import Path

from boss_say_hello.storage.json_file import write_json


class CompanyCache:
    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    @staticmethod
    def key(identity):
        return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def get(self, identity):
        row = self.data.get(self.key(identity))
        if row:
            sources = [
                {**source, "cached_at": source.get("cached_at", row["queried_at"])}
                for source in row["sources"]
            ]
            return sources or None
        return None

    def put(self, identity, sources):
        if not sources:
            return
        stamped = [{**source, "cached_at": source.get("cached_at", self.clock())} for source in sources]
        self.data[self.key(identity)] = {"identity": identity, "queried_at": self.clock(), "sources": stamped}
        write_json(self.path, self.data)
