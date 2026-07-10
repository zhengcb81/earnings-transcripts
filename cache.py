"""JSON 文件缓存。"""

import json
from pathlib import Path


class JsonCache:
    """JSON 文件缓存。"""

    def __init__(self, path: Path):
        self.path = path
        self._data = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value

    def __contains__(self, key: str):
        return key in self._data

    def __getitem__(self, key: str):
        return self._data[key]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")

    def __len__(self):
        return len(self._data)
