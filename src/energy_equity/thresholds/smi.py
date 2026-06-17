"""LIHEAP State Median Income (SMI) lookup and per-household 60% SMI flag.

SMI thresholds vary by state and household size. The package ships a CSV of
LIHEAP-published 60% SMI thresholds at `src/energy_equity/data/smi/`; this module loads
that file (or a `data_sources.smi_csv` override) and joins each household to the
appropriate threshold.

`smi_source` selects the fiscal year: either `"auto"` (pick the packaged FY closest to,
and not after, an anchor year — the AMI fiscal year) or an explicit `"liheap_fy<YEAR>"`.
"""

from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

PACKAGED_SMI_RESOURCE = files("energy_equity").joinpath("data/smi/liheap_smi_by_state_year.csv")


def load_packaged_smi_table(smi_csv: str | Path | None = None) -> pd.DataFrame:
    """Read the LIHEAP SMI CSV (the packaged file, or `smi_csv` override) as a DataFrame."""
    if smi_csv is not None:
        df = pd.read_csv(smi_csv)
    else:
        with PACKAGED_SMI_RESOURCE.open("r", encoding="utf-8") as fh:
            df = pd.read_csv(fh)
    df["state_fips"] = df["state_fips"].astype(str).str.zfill(2)
    df["state_abbr"] = df["state_abbr"].astype(str).str.upper()
    df["fy"] = pd.to_numeric(df["fy"], errors="coerce").astype("Int64")
    df["hh_size"] = pd.to_numeric(df["hh_size"], errors="coerce").astype("Int64")
    df["smi60_annual"] = pd.to_numeric(df["smi60_annual"], errors="coerce")
    return df


def resolve_smi_fiscal_year(
    state_fips: str, smi_source: str, *, anchor_year: int, table: pd.DataFrame
) -> int:
    """Resolve `smi_source` to a concrete fiscal year available for `state_fips` in `table`.

    `smi_source="auto"` picks the latest packaged FY that is <= `anchor_year` (the AMI/SMI
    benefit-year vintage); if none is <= anchor, it uses the earliest available FY and warns.
    An explicit `"liheap_fy####"` is returned as-is, with a warning if it diverges from
    `anchor_year` by more than one year (the silent-mismatch guard).
    """
    state_fips = str(state_fips).zfill(2)
    avail = sorted(
        int(y) for y in table.loc[table["state_fips"] == state_fips, "fy"].dropna().unique()
    )
    if not avail:
        raise ValueError(
            f"No SMI thresholds packaged for state_fips={state_fips!r}. "
            "Add rows to the SMI CSV (or set data_sources.smi_csv / thresholds.compute_smi=false)."
        )

    if smi_source == "auto":
        eligible = [y for y in avail if y <= anchor_year]
        if eligible:
            return max(eligible)
        chosen = min(avail)
        logger.warning(
            "SMI auto: no packaged fiscal year <= {} for state {}; using earliest available ({}).",
            anchor_year,
            state_fips,
            chosen,
        )
        return chosen

    match = re.fullmatch(r"liheap_fy(\d{4})", smi_source)
    if not match:
        raise ValueError(f"smi_source must be 'auto' or 'liheap_fy<YEAR>', got {smi_source!r}")
    fy = int(match.group(1))
    if abs(fy - anchor_year) > 1:
        logger.warning(
            "smi_source pins fiscal year {} but the analysis vintage (hud_ami_fy) is {}; "
            "SMI and income vintages diverge by {} years.",
            fy,
            anchor_year,
            abs(fy - anchor_year),
        )
    return fy


def smi_lookup_for(
    state_fips: str,
    smi_source: str,
    *,
    anchor_year: int,
    table: pd.DataFrame | None = None,
) -> dict[int, float]:
    """Resolve (state, fiscal year) into a hh_size -> 60% SMI threshold lookup.

    `smi_source` is "auto" or "liheap_fy####"; `anchor_year` drives auto-selection and the
    divergence warning. Raises ValueError when the resolved (state, fy) isn't covered.
    """
    df = table if table is not None else load_packaged_smi_table()
    fy = resolve_smi_fiscal_year(state_fips, smi_source, anchor_year=anchor_year, table=df)
    rows = df[(df["state_fips"] == str(state_fips).zfill(2)) & (df["fy"] == fy)]
    if rows.empty:
        raise ValueError(
            f"No SMI thresholds for state_fips={str(state_fips).zfill(2)!r}, fy={fy}. "
            "Add rows to the SMI CSV and provenance.csv, point data_sources.smi_csv at your "
            "own table, or set thresholds.compute_smi: false to run without SMI."
        )
    return {int(r.hh_size): float(r.smi60_annual) for r in rows.itertuples()}


def attach_smi_statewide_threshold(
    df: pd.DataFrame,
    *,
    state_fips: str,
    smi_source: str,
    anchor_year: int,
    hh_size_col: str = "NP",
    income_col: str = "income_adjusted",
    out_size_col: str = "hh_size_for_smi",
    out_threshold_col: str = "SMI60",
    out_flag_col: str = "le_60_smi",
    out_weight_col: str = "smi_weight",
    table: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Attach a 60% SMI threshold, the `le_60_smi` flag, and a 0/1 `smi_weight` column."""
    smi_map = smi_lookup_for(state_fips, smi_source, anchor_year=anchor_year, table=table)
    max_size = max(smi_map.keys())

    df[out_size_col] = df[hh_size_col].clip(lower=1, upper=max_size).astype("Int64")
    df[out_threshold_col] = df[out_size_col].astype(int).map(smi_map).astype(float)

    income = df[income_col].astype(float)
    df[out_flag_col] = np.where(
        df[out_threshold_col].notna(), income <= df[out_threshold_col], np.nan
    )
    df[out_weight_col] = np.where(
        df[out_flag_col] == True,  # noqa: E712
        1.0,
        np.where(df[out_flag_col] == False, 0.0, np.nan),  # noqa: E712
    )
    return df
