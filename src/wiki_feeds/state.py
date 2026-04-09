from __future__ import annotations

import json
from pathlib import Path


class State:
    """Persists the set of seen URLs to avoid re-fetching."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._seen: set[str] = set()
        if path.exists():
            self._seen = set(json.loads(path.read_text()))

    def seen(self, url: str) -> bool:
        return url in self._seen

    def mark(self, url: str) -> None:
        self._seen.add(url)

    def save(self) -> None:
        self.path.write_text(json.dumps(sorted(self._seen), indent=2))
