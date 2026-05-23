"""Path helpers: cache directory resolution, existence checks, directory ensure."""

from __future__ import annotations

import glob
import os
from collections.abc import Iterable
from pathlib import Path

from platformdirs import user_cache_dir

CACHE_ENV_VAR = "EE_CACHE_DIR"
_APP_NAME = "energy-equity"


def default_cache_dir() -> Path:
    """Platform-appropriate default cache directory.

    Linux: ~/.cache/energy-equity
    macOS: ~/Library/Caches/energy-equity
    Windows: %LOCALAPPDATA%\\energy-equity\\Cache
    """
    return Path(user_cache_dir(_APP_NAME))


def resolve_cache_dir(config_override: Path | None = None) -> Path:
    """Resolve the on-disk cache directory.

    Precedence: config > $EE_CACHE_DIR > platformdirs default. The chosen directory is
    created if it does not yet exist.
    """
    if config_override is not None:
        chosen = Path(config_override).expanduser()
    elif env := os.environ.get(CACHE_ENV_VAR):
        chosen = Path(env).expanduser()
    else:
        chosen = default_cache_dir()
    chosen.mkdir(parents=True, exist_ok=True)
    return chosen


def ensure_dir(path: str | Path) -> Path:
    """Create a directory (and parents) if it does not exist; return as a Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def unique_paths(paths: Iterable[str | Path | None]) -> list[Path]:
    """De-duplicate path candidates while preserving order; drop None."""
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        if p is None:
            continue
        normalized = str(Path(p))
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(Path(p))
    return out


def resolve_existing_path(
    candidates: Iterable[str | Path | None],
    glob_patterns: Iterable[str] | None = None,
    *,
    description: str = "path",
    required: bool = True,
) -> Path | None:
    """Return the first existing path from `candidates`, falling back to `glob_patterns`.

    Useful for tolerating multiple plausible layouts of input data (the original notebooks
    searched a half-dozen candidate locations for each input file).

    Raises FileNotFoundError when `required=True` and nothing matches; returns None
    otherwise. Prints which path was resolved for run-log traceability.
    """
    for p in unique_paths(candidates):
        if p.exists():
            print(f"[paths] {description}: {p}")
            return p

    if glob_patterns:
        matches: list[Path] = []
        for pattern in glob_patterns:
            matches.extend(Path(m) for m in glob.glob(pattern, recursive=True))
        for p in unique_paths(matches):
            if p.exists():
                print(f"[paths] {description} (via glob): {p}")
                return p

    msg = (
        f"Could not resolve {description}. Tried: "
        f"{[str(Path(p)) for p in candidates if p is not None]}"
    )
    if required:
        raise FileNotFoundError(msg)
    print(f"[paths] (optional) {msg}")
    return None
