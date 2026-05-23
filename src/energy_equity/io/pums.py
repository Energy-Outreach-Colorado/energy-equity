"""PUMS ZIP/CSV readers and small data-shape helpers used across the package."""

from __future__ import annotations

import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path

import pandas as pd


def find_single_csv_in_zip(zip_path: str | Path) -> str:
    """Return the name of the single .csv member inside `zip_path`.

    PUMS distributions typically contain one CSV plus a README. This helper raises a clear
    error when zero or multiple CSVs are present so the caller does not silently pick the
    wrong file.
    """
    with zipfile.ZipFile(zip_path) as zf:
        csvs = [n for n in zf.namelist() if n.lower().endswith(".csv")]
    if not csvs:
        raise FileNotFoundError(f"No .csv member found inside {zip_path}")
    if len(csvs) > 1:
        raise ValueError(f"Multiple .csv members in {zip_path}; specify one: {csvs}")
    return csvs[0]


def read_pums_csv_from_zip(
    zip_path: str | Path,
    *,
    usecols: Iterable[str] | None = None,
    dtype: Mapping[str, str] | None = None,
    chunksize: int | None = None,
) -> pd.DataFrame | pd.io.parsers.TextFileReader:
    """Stream a single CSV out of a PUMS ZIP archive.

    Passing `chunksize` returns a chunked reader (suitable for streaming large states).
    Otherwise returns a fully materialized DataFrame.
    """
    member = find_single_csv_in_zip(zip_path)
    with zipfile.ZipFile(zip_path) as zf, zf.open(member) as fh:
        return pd.read_csv(
            fh,
            usecols=list(usecols) if usecols is not None else None,
            dtype=dict(dtype) if dtype else None,
            chunksize=chunksize,
            low_memory=False,
        )


def coerce_numeric(df: pd.DataFrame, cols: Iterable[str]) -> pd.DataFrame:
    """Coerce the given columns to numeric in place, turning unparseable values into NaN.

    Returns the same DataFrame for fluent chaining.
    """
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def zfill_str(value: object, width: int) -> str:
    """Zero-pad an arbitrary value to a fixed-width string.

    Tolerates floats coming from `pd.read_csv` (e.g. county FIPS stored as 8.0 by default).
    Returns an empty string for missing/NaN values.
    """
    if value is None:
        return ""
    try:
        if isinstance(value, float) and (value != value):  # NaN
            return ""
    except TypeError:
        pass
    text = str(int(value)) if isinstance(value, float) else str(value)
    return text.zfill(width)


def replicate_cols(prefix: str, n: int) -> list[str]:
    """PUMS replicate-weight column names, e.g. WGTP1..WGTP80 or PWGTP1..PWGTP80."""
    return [f"{prefix}{i}" for i in range(1, n + 1)]
