"""HTTP download helpers with retry, friendly User-Agent, and idempotent cache writes."""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

import requests

DEFAULT_USER_AGENT = "energy-equity/0.1 (+https://github.com/ebaumer/energy-equity)"
DEFAULT_TIMEOUT_SEC = 60


def download_if_needed(
    url: str,
    dest: str | Path,
    *,
    force: bool = False,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    retries: int = 3,
    backoff_sec: float = 1.5,
) -> Path:
    """Download `url` to `dest` if the file is missing (or `force=True`).

    Writes to a temp file in the same directory and renames atomically on success so a
    partially written file is never left behind.
    """
    dest_path = Path(dest)
    if dest_path.exists() and not force:
        return dest_path

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": user_agent}

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, headers=headers, stream=True, timeout=timeout) as resp:
                resp.raise_for_status()
                with tempfile.NamedTemporaryFile(
                    delete=False, dir=dest_path.parent, prefix=dest_path.name + ".part."
                ) as tmp:
                    tmp_path = Path(tmp.name)
                    for chunk in resp.iter_content(chunk_size=1 << 16):
                        if chunk:
                            tmp.write(chunk)
            shutil.move(str(tmp_path), str(dest_path))
            return dest_path
        except (requests.RequestException, OSError) as exc:
            last_exc = exc
            if attempt == retries:
                break
            time.sleep(backoff_sec * attempt)

    raise RuntimeError(
        f"Failed to download {url} after {retries} attempts: {last_exc}"
    ) from last_exc


def ensure_urban_areas_zip(cache_dir: str | Path, tiger_year: int) -> Path:
    """Ensure the TIGER urban-area shapefile ZIP for `tiger_year` is present in `cache_dir`."""
    dest = Path(cache_dir) / f"tl_{tiger_year}_us_uac20.zip"
    url = f"https://www2.census.gov/geo/tiger/TIGER{tiger_year}/UAC20/tl_{tiger_year}_us_uac20.zip"
    return download_if_needed(url, dest)
