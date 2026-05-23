"""On-disk caching for Census API responses.

Each Census API call is cached as JSON under
`{cache_dir}/census/{kind}_{state_fips}_{year}_{span}.json`. The cache is read-through:
callers check `load` first, hit the API on a miss, then `store` the response. This makes
repeated pipeline runs cheap and lets the test suite stub the network entirely by
pre-populating the cache.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..paths import ensure_dir


class CensusCache:
    """A thin file-backed cache keyed by Census table + geography + vintage."""

    def __init__(self, cache_dir: str | Path) -> None:
        self.cache_dir = ensure_dir(Path(cache_dir) / "census")

    def _path(self, kind: str, state_fips: str, year: int, span: int) -> Path:
        return self.cache_dir / f"{kind}_{state_fips}_{year}_{span}yr.json"

    def load(self, kind: str, state_fips: str, year: int, span: int) -> Any | None:
        path = self._path(kind, state_fips, year, span)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def store(self, kind: str, state_fips: str, year: int, span: int, payload: Any) -> Path:
        path = self._path(kind, state_fips, year, span)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def has(self, kind: str, state_fips: str, year: int, span: int) -> bool:
        return self._path(kind, state_fips, year, span).exists()
