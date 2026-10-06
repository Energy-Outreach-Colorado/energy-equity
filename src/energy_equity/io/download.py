"""HTTP download helpers with retry, friendly User-Agent, and idempotent cache writes.

A failed download raises `DownloadError`, a `RuntimeError` subclass carrying the HTTP
status when the server answered. Client errors (4xx, such as a 404 for a file Census has
not published) are not retried, since asking again cannot change the answer, so a list
of fallback URLs moves on to the next candidate at once.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path

import requests

DEFAULT_USER_AGENT = "energy-equity/0.1 (+https://github.com/ebaumer/energy-equity)"
DEFAULT_TIMEOUT_SEC = 60


class DownloadError(RuntimeError):
    """A download that failed, with the URL and the HTTP status when there was one."""

    def __init__(self, message: str, *, url: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.url = url
        self.status_code = status_code

    @property
    def not_found(self) -> bool:
        """True when the server answered 404 or 410, meaning the file is not published."""
        return self.status_code in (404, 410)


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
    partially written file is never left behind. Raises `DownloadError` on failure,
    straight away for a 4xx answer and after `retries` attempts otherwise.
    """
    dest_path = Path(dest)
    if dest_path.exists() and not force:
        return dest_path

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": user_agent}

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        tmp_path: Path | None = None
        try:
            with requests.get(url, headers=headers, stream=True, timeout=timeout) as resp:
                if 400 <= resp.status_code < 500:
                    raise DownloadError(
                        f"Failed to download {url}: HTTP {resp.status_code}",
                        url=url,
                        status_code=resp.status_code,
                    )
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
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            last_exc = exc
            if attempt == retries:
                break
            time.sleep(backoff_sec * attempt)

    status = getattr(getattr(last_exc, "response", None), "status_code", None)
    raise DownloadError(
        f"Failed to download {url} after {retries} attempts: {last_exc}",
        url=url,
        status_code=status,
    ) from last_exc


def urban_areas_url(tiger_year: int) -> str:
    """The Census URL of the national TIGER 2020 urban-area ZIP for `tiger_year`."""
    return f"https://www2.census.gov/geo/tiger/TIGER{tiger_year}/UAC20/tl_{tiger_year}_us_uac20.zip"


def urban_areas_cache_path(cache_dir: str | Path, tiger_year: int) -> Path:
    """Where the urban-area ZIP for `tiger_year` is cached."""
    return Path(cache_dir) / f"tl_{tiger_year}_us_uac20.zip"


def ensure_urban_areas_zip(cache_dir: str | Path, tiger_year: int, *, force: bool = False) -> Path:
    """Ensure the TIGER urban-area shapefile ZIP for `tiger_year` is present in `cache_dir`."""
    return download_if_needed(
        urban_areas_url(tiger_year), urban_areas_cache_path(cache_dir, tiger_year), force=force
    )
