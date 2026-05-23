"""LIHEAP State Median Income (SMI) lookup and per-household 60% SMI flag.

SMI thresholds vary by state and household size. The package ships a CSV of
LIHEAP-published 60% SMI thresholds at `src/energy_equity/data/smi/`; this module loads
that file and joins each household to the appropriate threshold.

Configurations select a row via `smi_source` of the form `"liheap_fy<YEAR>"`.
"""

from __future__ import annotations

import re
from importlib.resources import files

import numpy as np
import pandas as pd

PACKAGED_SMI_RESOURCE = files("energy_equity").joinpath("data/smi/liheap_smi_by_state_year.csv")


def load_packaged_smi_table() -> pd.DataFrame:
    """Read the LIHEAP SMI CSV shipped with the package as a DataFrame."""
    with PACKAGED_SMI_RESOURCE.open("r", encoding="utf-8") as fh:
        df = pd.read_csv(fh)
    df["state_fips"] = df["state_fips"].astype(str).str.zfill(2)
    df["state_abbr"] = df["state_abbr"].astype(str).str.upper()
    df["fy"] = pd.to_numeric(df["fy"], errors="coerce").astype("Int64")
    df["hh_size"] = pd.to_numeric(df["hh_size"], errors="coerce").astype("Int64")
    df["smi60_annual"] = pd.to_numeric(df["smi60_annual"], errors="coerce")
    return df


def smi_lookup_for(
    state_fips: str, smi_source: str, *, table: pd.DataFrame | None = None
) -> dict[int, float]:
    """Resolve `smi_source` (e.g. "liheap_fy2025") into a hh_size -> threshold lookup.

    Raises ValueError when the state/year combination is not covered. Callers can pass a
    custom `table` to override the packaged data (useful in tests).
    """
    match = re.fullmatch(r"liheap_fy(\d{4})", smi_source)
    if not match:
        raise ValueError(f"smi_source must be of the form 'liheap_fy<YEAR>', got {smi_source!r}")
    fy = int(match.group(1))
    df = table if table is not None else load_packaged_smi_table()
    rows = df[(df["state_fips"] == str(state_fips).zfill(2)) & (df["fy"] == fy)]
    if rows.empty:
        raise ValueError(
            f"No SMI thresholds packaged for state_fips={state_fips!r}, fy={fy}. "
            "Add a row to src/energy_equity/data/smi/liheap_smi_by_state_year.csv "
            "and document its source in provenance.csv."
        )
    return {int(r.hh_size): float(r.smi60_annual) for r in rows.itertuples()}


def attach_smi_statewide_threshold(
    df: pd.DataFrame,
    *,
    state_fips: str,
    smi_source: str,
    hh_size_col: str = "NP",
    income_col: str = "income_adjusted",
    out_size_col: str = "hh_size_for_smi",
    out_threshold_col: str = "SMI60",
    out_flag_col: str = "le_60_smi",
    out_weight_col: str = "smi_weight",
    table: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Attach a 60% SMI threshold, the `le_60_smi` flag, and a 0/1 `smi_weight` column."""
    smi_map = smi_lookup_for(state_fips, smi_source, table=table)
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
