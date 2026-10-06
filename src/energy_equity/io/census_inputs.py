"""Find or download the Census files a run needs: PUMS microdata and TIGER layers.

A config may leave `data_sources.pums_housing_zip`, `pums_person_zip`, `tiger_puma_zip`
and `tiger_tract_zip` null. The `resolve_*` functions then download the file into the
cache (`project.cache_dir`, else `$EE_CACHE_DIR`, else the platform default) the first
time it is needed and reuse it afterwards. A configured path is always used as given,
and a configured path that does not exist is an error rather than a reason to download,
so a typo cannot quietly swap in different data.

Cache layout, matching the `ee cache clear` subsets:

    pums/<year>_<span>yr/csv_h<st>.zip and csv_p<st>.zip
    tiger/<year>/tl_<year>_<fips>_tract.zip and tl_<year>_<fips>_puma<10|20>.zip

URL patterns, checked on 2026-10-06 with header requests against www2.census.gov:

- PUMS: `programs-surveys/acs/data/pums/<year>/<1-Year|5-Year>/csv_<h|p><st>.zip` with
  the lower-case state abbreviation. The 2020 1-year file does not exist, because that
  year was published only as experimental data, and a year appears only after its
  release (2025 1-year was not yet out).
- TIGER tracts: `geo/tiger/TIGER<year>/TRACT/tl_<year>_<fips>_tract.zip` (2021, 2024 and
  2025 checked).
- TIGER PUMAs: the folder is `PUMA/` through TIGER2023 and `PUMA20/` from TIGER2024
  (2025 checked), and the file suffix is `puma10` through 2021 and `puma20` from 2022.
  Both folders are tried, `PUMA20/` first, so a later rename in either direction keeps
  working without a code change.

PUMA definitions, from the Census PUMS data dictionaries: 1-year files through 2021 and
5-year files ending in 2021 or earlier use 2010 PUMAs, and 1-year files from 2022 and
5-year files ending in 2023 or later use 2020 PUMAs. The 2018-2022 5-year file has no
`PUMA` column at all, because it splits the code into `PUMA10` and `PUMA20` by data year,
so the package cannot read it. The TIGER PUMA layer must use the same definitions as the
microdata, or PUMA codes would join to the wrong polygons, and `Config` validation
enforces that with `check_puma_vintages`.
"""

from __future__ import annotations

import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from loguru import logger

from ..paths import resolve_cache_dir
from .download import DownloadError, download_if_needed

if TYPE_CHECKING:
    from ..config import Config

PUMS_BASE_URL = "https://www2.census.gov/programs-surveys/acs/data/pums"
TIGER_BASE_URL = "https://www2.census.gov/geo/tiger"
TIGER_PUMA_FOLDERS = ("PUMA20", "PUMA")
FIRST_2020_PUMA_YEAR = 2022
SPLIT_PUMA_FIVE_YEAR_END = 2022

PumsKind = Literal["housing", "person"]
TigerLayer = Literal["puma", "tract"]


class CensusInputError(FileNotFoundError):
    """A Census input that is neither configured nor downloadable."""


def pums_puma_definition(pums_year: int, pums_span: int) -> int:
    """The decennial PUMA definition (2010 or 2020) a PUMS file's `PUMA` codes use.

    Raises ValueError for the 2018-2022 5-year file, which has no single `PUMA` column.
    """
    if pums_span == 5 and pums_year == SPLIT_PUMA_FIVE_YEAR_END:
        raise ValueError(
            "the 2018-2022 5-year PUMS file has no PUMA column; it splits the code into "
            "PUMA10 and PUMA20 by data year, which this package cannot read. Use the "
            "2019-2023 5-year file or later, or a 1-year file"
        )
    if pums_span == 5:
        return 2020 if pums_year > SPLIT_PUMA_FIVE_YEAR_END else 2010
    return 2020 if pums_year >= FIRST_2020_PUMA_YEAR else 2010


def tiger_puma_definition(tiger_year: int) -> int:
    """The decennial PUMA definition (2010 or 2020) of a TIGER release's PUMA layer."""
    return 2020 if tiger_year >= FIRST_2020_PUMA_YEAR else 2010


def check_puma_vintages(pums_year: int, pums_span: int, tiger_year: int) -> None:
    """Raise ValueError when the PUMS file and TIGER PUMA layer use different PUMAs."""
    microdata = pums_puma_definition(pums_year, pums_span)
    boundaries = tiger_puma_definition(tiger_year)
    if microdata != boundaries:
        raise ValueError(
            f"PUMS {pums_year} {pums_span}-year uses {microdata} PUMA codes but TIGER "
            f"{tiger_year} has {boundaries} PUMA boundaries, so PUMAs would join to the "
            f"wrong polygons. Set vintages.tiger_year to a year with {microdata} PUMAs "
            f"({'2022 or later' if microdata == 2020 else '2021 or earlier'})"
        )


def pums_filename(kind: PumsKind, state_abbr: str) -> str:
    """The Census file name of a state's PUMS housing or person ZIP."""
    letter = {"housing": "h", "person": "p"}[kind]
    return f"csv_{letter}{state_abbr.strip().lower()}.zip"


def pums_url(year: int, span: int, kind: PumsKind, state_abbr: str) -> str:
    """The Census download URL of a state's PUMS housing or person ZIP."""
    return f"{PUMS_BASE_URL}/{year}/{span}-Year/{pums_filename(kind, state_abbr)}"


def tiger_filename(layer: TigerLayer, tiger_year: int, state_fips: str) -> str:
    """The Census file name of a state's TIGER tract or PUMA ZIP for `tiger_year`."""
    if layer == "tract":
        return f"tl_{tiger_year}_{state_fips}_tract.zip"
    suffix = "puma20" if tiger_puma_definition(tiger_year) == 2020 else "puma10"
    return f"tl_{tiger_year}_{state_fips}_{suffix}.zip"


def tiger_urls(layer: TigerLayer, tiger_year: int, state_fips: str) -> list[str]:
    """Candidate download URLs of a TIGER tract or PUMA ZIP, in the order to try them."""
    filename = tiger_filename(layer, tiger_year, state_fips)
    folders = ("TRACT",) if layer == "tract" else TIGER_PUMA_FOLDERS
    return [f"{TIGER_BASE_URL}/TIGER{tiger_year}/{folder}/{filename}" for folder in folders]


def _valid_zip(path: Path) -> bool:
    return path.is_file() and zipfile.is_zipfile(path)


def download_first_available(
    urls: Sequence[str],
    dest: str | Path,
    *,
    force: bool = False,
    description: str = "file",
    not_found_hint: str | None = None,
) -> Path:
    """Download the first of `urls` the server has into `dest`, keeping a valid cached copy.

    A cached file that is not a readable ZIP (for example an HTML error page saved by an
    older run) is deleted and downloaded again, and so is a fresh download that is not a
    ZIP. Candidates that answer 404 or 410 are skipped, any other failure stops the
    search, and when every candidate is missing a `CensusInputError` lists the URLs tried
    with `not_found_hint` appended.
    """
    dest_path = Path(dest)
    if dest_path.exists() and not force:
        if _valid_zip(dest_path):
            return dest_path
        logger.warning(
            "cached {} at {} is not a valid ZIP; downloading again", description, dest_path
        )
        dest_path.unlink()

    tried: list[str] = []
    for url in urls:
        tried.append(url)
        logger.info("downloading {} from {}", description, url)
        try:
            download_if_needed(url, dest_path, force=True)
        except DownloadError as exc:
            if exc.not_found:
                logger.debug("{} not at {}", description, url)
                continue
            raise
        if not _valid_zip(dest_path):
            dest_path.unlink(missing_ok=True)
            raise CensusInputError(
                f"{description} downloaded from {url} is not a valid ZIP file; the server may "
                "have returned an error page. Try again later or download it by hand"
            )
        logger.info("downloaded {} from {}", description, url)
        return dest_path

    message = f"{description} is not available from Census. Tried: {', '.join(tried)}"
    if not_found_hint:
        message = f"{message}. {not_found_hint}"
    raise CensusInputError(message)


def _pums_not_found_hint(year: int, span: int) -> str:
    if year == 2020 and span == 1:
        return (
            "The 2020 1-year ACS was released only as experimental data, so there is no "
            "standard 2020 1-year PUMS file; use 2019, 2021 or a 5-year file"
        )
    return (
        f"Check that the {year} {span}-year PUMS has been released; Census publishes 1-year "
        "files each autumn and 5-year files each winter"
    )


def pums_cache_path(
    cache_dir: str | Path, *, year: int, span: int, kind: PumsKind, state_abbr: str
) -> Path:
    """Where a state's PUMS housing or person ZIP is cached."""
    return Path(cache_dir) / "pums" / f"{year}_{span}yr" / pums_filename(kind, state_abbr)


def tiger_cache_path(
    cache_dir: str | Path, *, layer: TigerLayer, tiger_year: int, state_fips: str
) -> Path:
    """Where a state's TIGER tract or PUMA ZIP is cached."""
    filename = tiger_filename(layer, tiger_year, state_fips)
    return Path(cache_dir) / "tiger" / str(tiger_year) / filename


def is_cached_zip(path: str | Path) -> bool:
    """True when `path` holds a readable ZIP, so no download is needed."""
    return _valid_zip(Path(path))


def ensure_pums_zip(
    cache_dir: str | Path,
    *,
    year: int,
    span: int,
    kind: PumsKind,
    state_abbr: str,
    force: bool = False,
) -> Path:
    """The cached PUMS housing or person ZIP for a state, downloading it when missing."""
    dest = pums_cache_path(cache_dir, year=year, span=span, kind=kind, state_abbr=state_abbr)
    return download_first_available(
        [pums_url(year, span, kind, state_abbr)],
        dest,
        force=force,
        description=f"PUMS {year} {span}-year {kind} file for {state_abbr.upper()}",
        not_found_hint=_pums_not_found_hint(year, span),
    )


def ensure_tiger_zip(
    cache_dir: str | Path,
    *,
    layer: TigerLayer,
    tiger_year: int,
    state_fips: str,
    force: bool = False,
) -> Path:
    """The cached TIGER tract or PUMA ZIP for a state, downloading it when missing."""
    dest = tiger_cache_path(cache_dir, layer=layer, tiger_year=tiger_year, state_fips=state_fips)
    return download_first_available(
        tiger_urls(layer, tiger_year, state_fips),
        dest,
        force=force,
        description=f"TIGER {tiger_year} {layer} layer for state {state_fips}",
        not_found_hint=f"Check that TIGER/Line {tiger_year} has been released",
    )


def _configured(path: Path | None, field: str) -> Path | None:
    if path is None:
        return None
    if not Path(path).exists():
        raise CensusInputError(
            f"data_sources.{field} is set to {path}, which does not exist. Fix the path, or "
            "set it to null to download the file automatically"
        )
    return Path(path)


def resolve_pums_zip(cfg: Config, kind: PumsKind, *, force: bool = False) -> Path:
    """The PUMS housing or person ZIP for a run: the configured file, or a cached download."""
    field = f"pums_{kind}_zip"
    configured = _configured(getattr(cfg.data_sources, field), field)
    if configured is not None:
        return configured
    return ensure_pums_zip(
        resolve_cache_dir(cfg.project.cache_dir),
        year=cfg.vintages.pums_year,
        span=cfg.vintages.pums_span,
        kind=kind,
        state_abbr=cfg.geography.state_abbr,
        force=force,
    )


def resolve_tiger_zip(cfg: Config, layer: TigerLayer, *, force: bool = False) -> Path:
    """The TIGER tract or PUMA ZIP for a run: the configured file, or a cached download."""
    field = f"tiger_{layer}_zip"
    configured = _configured(getattr(cfg.data_sources, field), field)
    if configured is not None:
        return configured
    return ensure_tiger_zip(
        resolve_cache_dir(cfg.project.cache_dir),
        layer=layer,
        tiger_year=cfg.vintages.tiger_year,
        state_fips=cfg.geography.state_fips,
        force=force,
    )
