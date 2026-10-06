"""A stand-in for `download_if_needed` and the Census base URLs, for offline tests."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from energy_equity.io.download import DownloadError

PUMS = "https://www2.census.gov/programs-surveys/acs/data/pums"
TIGER = "https://www2.census.gov/geo/tiger"


def zip_bytes(name: str = "data.csv", text: str = "a,b\n1,2\n") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(name, text)
    return buffer.getvalue()


class FakeDownloads:
    """Stands in for `download_if_needed`, answering per URL and recording each call."""

    def __init__(self, answers: dict[str, bytes | int]) -> None:
        self.answers = answers
        self.calls: list[str] = []

    def __call__(self, url: str, dest: str | Path, *, force: bool = False, **_: object) -> Path:
        self.calls.append(url)
        answer = self.answers.get(url, 404)
        if isinstance(answer, int):
            raise DownloadError(f"HTTP {answer}", url=url, status_code=answer)
        path = Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(answer)
        return path
