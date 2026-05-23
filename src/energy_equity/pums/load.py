"""Load ACS PUMS housing and person microdata ZIPs into pandas DataFrames.

PUMS column conventions:
- WGTP / PWGTP: point-estimate household and person weights.
- WGTP1..WGTP80 / PWGTP1..PWGTP80: 80 replicate weights for SDR variance.
- ADJINC: income adjustment factor (divide by 1_000_000 to get a multiplier).
- ADJHSG: housing adjustment factor (divide by 1_000_000 to get a multiplier).
- ELEP, GASP, FULP: monthly electric, monthly gas, annual other-fuel cost (with ACS
  special codes 0..3 indicating N/A, included-in-rent, no-charge, no-fuel-used).
- HHL: broad household language category (1=English only, 2=Spanish, 3=Other Indo-European,
  4=Asian & Pacific Island, 5=Other).
- LANP: detailed language code for the householder (~1000 entries).
- TEN: tenure (1=owner with mortgage, 2=owner free & clear, 3=renter, 4=no cash rent).
- RELSHIPP: relationship to householder (20 = reference person / householder).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..io.pums import coerce_numeric, read_pums_csv_from_zip, replicate_cols, zfill_str
from ..weights.sdr import PUMS_REPLICATE_COUNT

HOUSING_BASE_COLS: tuple[str, ...] = (
    "SERIALNO",
    "PUMA",
    "WGTP",
    "NP",
    "HINCP",
    "ADJINC",
    "ADJHSG",
    "ELEP",
    "GASP",
    "FULP",
    "HHL",
    "TEN",
    "GRNTP",
    "GRPIP",
    "SMOCP",
)

PERSON_BASE_COLS: tuple[str, ...] = (
    "SERIALNO",
    "RELSHIPP",
    "AGEP",
    "SEX",
    "RAC1P",
    "HISP",
    "LANP",
)

HOUSEHOLDER_RELSHIPP = 20


def load_pums_households(
    housing_zip: str | Path,
    *,
    include_replicate_weights: bool = True,
    replicate_count: int = PUMS_REPLICATE_COUNT,
    extra_cols: tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """Load the household-level PUMS CSV into a clean DataFrame.

    Drops rows with zero/missing weight or zero household size and zero-pads PUMA to 5
    digits. Replicate weights (WGTP1..WGTPn) are included by default for MOE computation.
    Caller may pass `extra_cols` to request additional PUMS variables (for example, for
    finer demographic slicing).
    """
    cols = list(HOUSING_BASE_COLS)
    if include_replicate_weights:
        cols += replicate_cols("WGTP", replicate_count)
    if extra_cols:
        cols += [c for c in extra_cols if c not in cols]

    df = read_pums_csv_from_zip(housing_zip, usecols=cols)
    assert isinstance(df, pd.DataFrame)  # not chunked
    df["SERIALNO"] = df["SERIALNO"].astype("string")
    df["PUMA"] = df["PUMA"].astype(str).map(lambda s: zfill_str(s, 5))

    numeric_cols = [c for c in cols if c not in {"SERIALNO", "PUMA"}]
    coerce_numeric(df, numeric_cols)

    df = df[(df["WGTP"] > 0) & (df["NP"] > 0)].copy()
    return df


def load_head_demographics(person_zip: str | Path) -> pd.DataFrame:
    """Load householder (reference person, RELSHIPP=20) demographics keyed by SERIALNO."""
    df = read_pums_csv_from_zip(person_zip, usecols=list(PERSON_BASE_COLS))
    assert isinstance(df, pd.DataFrame)
    df["SERIALNO"] = df["SERIALNO"].astype("string")
    coerce_numeric(df, [c for c in PERSON_BASE_COLS if c != "SERIALNO"])
    head = df.loc[df["RELSHIPP"] == HOUSEHOLDER_RELSHIPP, list(PERSON_BASE_COLS)].copy()
    head = head.drop(columns=["RELSHIPP"]).drop_duplicates(subset=["SERIALNO"], keep="first")
    return head


def load_puma_name_lookup(
    puma_shape_zip: str | Path | None,
    *,
    name_col_candidates: tuple[str, ...] = ("NAMELSAD20", "NAMELSAD10", "NAME20", "NAMELSAD"),
    code_col_candidates: tuple[str, ...] = ("PUMACE20", "PUMACE10", "PUMA20", "PUMA"),
) -> pd.DataFrame | None:
    """Read TIGER PUMA shapefile and return a (PUMA, puma_name) lookup table.

    Returns None when no shapefile is provided or when geopandas is unavailable. TIGER
    column names drift slightly between vintages so both name and code columns are
    looked up via a candidate list.
    """
    if puma_shape_zip is None:
        return None
    if not Path(puma_shape_zip).exists():
        return None

    try:
        import geopandas as gpd
    except ImportError:
        return None

    gdf = gpd.read_file(str(puma_shape_zip))
    code_col = next((c for c in code_col_candidates if c in gdf.columns), None)
    name_col = next((c for c in name_col_candidates if c in gdf.columns), None)
    if code_col is None or name_col is None:
        return None

    lk = gdf[[code_col, name_col]].drop_duplicates().copy()
    lk["PUMA"] = lk[code_col].astype(str).str.zfill(5)
    lk["puma_name"] = lk[name_col].astype(str)
    return lk[["PUMA", "puma_name"]].reset_index(drop=True)
