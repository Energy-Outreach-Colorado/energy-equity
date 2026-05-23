"""Tests for io.pums helpers."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pandas as pd
import pytest

from energy_equity.io.pums import (
    coerce_numeric,
    find_single_csv_in_zip,
    read_pums_csv_from_zip,
    replicate_cols,
    zfill_str,
)


def _make_pums_zip(tmp_path: Path, csv_name: str = "psam_h08.csv") -> Path:
    csv_text = "SERIALNO,WGTP,HINCP\n2024GQ0000001,42,55000\n2024HU0000002,17,82500\n"
    zip_path = tmp_path / "csv_hco.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(csv_name, csv_text)
        zf.writestr("README.txt", "PUMS docs go here")
    return zip_path


def test_find_single_csv_in_zip(tmp_path: Path) -> None:
    zip_path = _make_pums_zip(tmp_path)
    assert find_single_csv_in_zip(zip_path) == "psam_h08.csv"


def test_find_single_csv_in_zip_raises_when_multiple(tmp_path: Path) -> None:
    zip_path = tmp_path / "two.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a.csv", "a")
        zf.writestr("b.csv", "b")
    with pytest.raises(ValueError):
        find_single_csv_in_zip(zip_path)


def test_find_single_csv_in_zip_raises_when_none(tmp_path: Path) -> None:
    zip_path = tmp_path / "none.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("README.txt", "no csvs")
    with pytest.raises(FileNotFoundError):
        find_single_csv_in_zip(zip_path)


def test_read_pums_csv_from_zip(tmp_path: Path) -> None:
    zip_path = _make_pums_zip(tmp_path)
    df = read_pums_csv_from_zip(zip_path)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert {"SERIALNO", "WGTP", "HINCP"} <= set(df.columns)


def test_read_pums_csv_from_zip_usecols(tmp_path: Path) -> None:
    zip_path = _make_pums_zip(tmp_path)
    df = read_pums_csv_from_zip(zip_path, usecols=["WGTP", "HINCP"])
    assert list(df.columns) == ["WGTP", "HINCP"]


def test_coerce_numeric_replaces_unparseable_with_nan() -> None:
    df = pd.DataFrame({"a": ["1", "2", "x"], "b": ["3", "y", "5"]})
    coerce_numeric(df, ["a", "b"])
    assert pd.isna(df["a"].iloc[2])
    assert pd.isna(df["b"].iloc[1])
    assert df["a"].iloc[0] == 1


def test_coerce_numeric_skips_missing_columns() -> None:
    df = pd.DataFrame({"a": [1]})
    coerce_numeric(df, ["a", "does_not_exist"])  # must not raise
    assert df["a"].iloc[0] == 1


def test_zfill_str_handles_floats_and_nans() -> None:
    assert zfill_str(8.0, 2) == "08"
    assert zfill_str(101, 3) == "101"
    assert zfill_str("8", 2) == "08"
    assert zfill_str(float("nan"), 3) == ""
    assert zfill_str(None, 3) == ""


def test_replicate_cols() -> None:
    assert replicate_cols("WGTP", 3) == ["WGTP1", "WGTP2", "WGTP3"]
    assert len(replicate_cols("PWGTP", 80)) == 80
