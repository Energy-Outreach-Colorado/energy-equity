"""HUD county-level AMI -> PUMA AMI bridge and per-household AMI eligibility flags.

The bridge has two steps:

1. Read a county-by-household-size HUD 80% AMI limits CSV and tract-level household counts
   from ACS B11001. Combine via the official Census 2020 Tract -> 2020 PUMA relationship
   file to compute a household-weighted PUMA share for each county within each PUMA.
2. Blend the county-specific limits up to the PUMA level using those shares to produce a
   single `AMI80` threshold per `(PUMA, hh_size)`. Households are then flagged
   `le_80_ami` by comparing their adjusted income to the PUMA-level threshold for their
   household size.

`attach_ami_blended_threshold` is the binary path (one threshold per PUMA, flag = 0/1).
`attach_ami_expected_probability` is the probabilistic path (sums county-share weights
where the household would qualify, giving a value in [0, 1]). Both modes write
`ami_weight`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pandas as pd

from ..io.download import download_if_needed
from ..io.pums import zfill_str
from ..paths import ensure_dir

CENSUS_TRACT_TO_PUMA_URLS: tuple[str, ...] = (
    "https://www2.census.gov/geo/docs/reference/puma2020/2020_Census_Tract_to_2020_PUMA.txt",
    "https://www2.census.gov/geo/docs/maps-data/data/rel2020/2020_Census_Tract_to_2020_PUMA.txt",
)


def _normalize_county_name(name: str) -> str:
    text = str(name).strip().lower()
    text = re.sub(r"\s+county$", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def build_county_name_to_fips3(counties: pd.DataFrame) -> dict[str, str]:
    """Map normalized county name -> 3-digit FIPS from a `CensusClient.fetch_counties` frame.

    The Census `name` column looks like "Pueblo County, Colorado"; we take the part before
    the first comma and normalize it so it matches the `County` values in a HUD AMI CSV
    (e.g. "Pueblo"). Pass the result as `county_name_to_fips3` to
    `load_county_ami_limits_long_from_csv`.
    """
    return {
        _normalize_county_name(str(name).split(",")[0]): fips3
        for name, fips3 in zip(counties["name"], counties["county_fips"], strict=False)
    }


def load_county_ami_limits_long_from_csv(
    path: str | Path,
    *,
    state_fips: str,
    county_name_to_fips3: Mapping[str, str],
    scalar: float = 1.0,
) -> pd.DataFrame:
    """Parse a wide HUD AMI limits CSV and reshape to a long (county_fips5, hh_size, ami80) table.

    The expected input format has a `County` column plus numeric columns named "1", "2",
    ... up to the maximum household size (HUD publishes through 20). Values are 80% AMI
    dollar thresholds. `scalar` is a multiplicative override (1.0 keeps published values).
    `county_name_to_fips3` is a state-specific lookup; build it from
    `census.api.fetch_counties(state_fips, year)` so the parser stays state-agnostic.
    """
    df = pd.read_csv(path)
    df.columns = [str(c).strip() for c in df.columns]
    if "County" not in df.columns:
        raise ValueError(f"Expected a 'County' column in {path}. Found: {df.columns.tolist()}")

    df["county_norm"] = df["County"].map(_normalize_county_name)
    df["county_fips3"] = df["county_norm"].map(county_name_to_fips3)

    missing = df.loc[df["county_fips3"].isna(), "County"].unique().tolist()
    if missing:
        raise ValueError(
            "Some county names in the AMI file did not match the FIPS map. Fix spelling "
            f"in the CSV or extend the lookup. Missing: {missing}"
        )
    df["county_fips5"] = zfill_str(state_fips, 2) + df["county_fips3"]

    size_cols = [c for c in df.columns if re.fullmatch(r"\d+", c)]
    if not size_cols:
        raise ValueError(
            "Could not find any integer household-size columns (e.g. '1','2',...) in the AMI CSV."
        )

    long = df.melt(
        id_vars=["county_fips5"],
        value_vars=size_cols,
        var_name="hh_size",
        value_name="ami80",
    )
    long["hh_size"] = pd.to_numeric(long["hh_size"], errors="coerce").astype("Int64")
    long["ami80"] = pd.to_numeric(long["ami80"], errors="coerce") * float(scalar)
    long = long.dropna(subset=["hh_size", "ami80"]).copy()
    long["hh_size"] = long["hh_size"].astype(int)
    long = long.groupby(["county_fips5", "hh_size"], as_index=False)["ami80"].mean()
    return long


def load_tract_to_puma_relationship(cache_dir: str | Path, state_fips: str) -> pd.DataFrame:
    """Download (if needed) and parse the 2020 Tract -> 2020 PUMA relationship file."""
    cache_dir = ensure_dir(cache_dir)
    dest = Path(cache_dir) / "2020_Census_Tract_to_2020_PUMA.txt"

    if not dest.exists():
        last_exc: Exception | None = None
        for url in CENSUS_TRACT_TO_PUMA_URLS:
            try:
                download_if_needed(url, dest)
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
        if last_exc is not None:
            raise last_exc

    rel = pd.read_csv(dest, dtype=str)
    needed = {"STATEFP", "COUNTYFP", "TRACTCE", "PUMA5CE"}
    missing = needed - set(rel.columns)
    if missing:
        raise ValueError(
            f"Unexpected schema in tract->PUMA file. Missing columns: {missing}; got {list(rel.columns)}"
        )

    rel["STATEFP"] = rel["STATEFP"].str.zfill(2)
    rel["COUNTYFP"] = rel["COUNTYFP"].str.zfill(3)
    rel["TRACTCE"] = rel["TRACTCE"].str.zfill(6)
    rel["PUMA5CE"] = rel["PUMA5CE"].str.zfill(5)
    rel = rel.loc[rel["STATEFP"] == state_fips].copy()
    rel["tract_geoid"] = rel["STATEFP"] + rel["COUNTYFP"] + rel["TRACTCE"]
    rel["county_fips5"] = rel["STATEFP"] + rel["COUNTYFP"]
    rel = rel.rename(columns={"PUMA5CE": "PUMA"})
    return rel[["tract_geoid", "county_fips5", "PUMA"]].reset_index(drop=True)


def compute_puma_county_household_weights(
    tract_puma: pd.DataFrame, tract_households: pd.DataFrame
) -> pd.DataFrame:
    """Build (PUMA, county_fips5, weight_hh) where weight_hh is the share of a PUMA's households in that county.

    Inputs:
      tract_puma: (tract_geoid, county_fips5, PUMA) -- tract-to-PUMA relationship.
      tract_households: (tract_geoid|geoid, households) -- ACS B11001 tract household counts.
    """
    th = tract_households.copy()
    if "tract_geoid" not in th.columns:
        if "geoid" in th.columns:
            th = th.rename(columns={"geoid": "tract_geoid"})
        else:
            raise ValueError("tract_households must include a 'tract_geoid' or 'geoid' column.")
    th["households"] = pd.to_numeric(th["households"], errors="coerce").fillna(0.0)

    tp = tract_puma.merge(th[["tract_geoid", "households"]], on="tract_geoid", how="left")
    tp["households"] = tp["households"].fillna(0.0)
    pc = (
        tp.groupby(["PUMA", "county_fips5"], as_index=False)["households"]
        .sum()
        .rename(columns={"households": "hh_in_county_within_puma"})
    )
    pc["hh_in_puma_total"] = pc.groupby("PUMA")["hh_in_county_within_puma"].transform("sum")
    pc["weight_hh"] = np.where(
        pc["hh_in_puma_total"] > 0, pc["hh_in_county_within_puma"] / pc["hh_in_puma_total"], np.nan
    )
    return pc


def build_ami80_by_puma(
    puma_county_weights: pd.DataFrame, county_ami_long: pd.DataFrame
) -> pd.DataFrame:
    """Blend county AMI limits to PUMA-level using household-share weights.

    Returns columns: PUMA, hh_size, AMI80.
    """
    merged = puma_county_weights.merge(county_ami_long, on="county_fips5", how="left")

    def _weighted_mean(g: pd.DataFrame) -> float:
        vals = g["ami80"].astype(float)
        wts = g["weight_hh"].astype(float)
        mask = vals.notna() & wts.notna()
        if not mask.any() or float(wts.loc[mask].sum()) == 0:
            return float("nan")
        return float(np.average(vals.loc[mask], weights=wts.loc[mask]))

    by_puma = (
        merged.groupby(["PUMA", "hh_size"], as_index=False, group_keys=False)[
            ["ami80", "weight_hh"]
        ]
        .apply(lambda g: pd.Series({"AMI80": _weighted_mean(g)}))
        .reset_index()
    )
    by_puma = by_puma[["PUMA", "hh_size", "AMI80"]]
    by_puma["AMI80"] = by_puma["AMI80"].round(0).astype("Int64")
    return by_puma


def attach_ami_blended_threshold(
    df: pd.DataFrame,
    ami80_by_puma: pd.DataFrame,
    *,
    puma_col: str = "PUMA",
    hh_size_col: str = "NP",
    income_col: str = "income_adjusted",
    out_size_col: str = "hh_size_for_ami",
    out_ami_col: str = "AMI80",
    out_flag_col: str = "le_80_ami",
    out_weight_col: str = "ami_weight",
) -> pd.DataFrame:
    """Attach a per-household 80% AMI threshold + binary `le_80_ami` flag using the PUMA-blended table.

    Households with NP greater than the max published household size are clipped to that
    max (matching the notebook's behavior of clipping rather than NaN-ing).
    """
    ami = ami80_by_puma.copy()
    ami[puma_col] = ami[puma_col].astype(str).str.zfill(5)
    ami["hh_size"] = pd.to_numeric(ami["hh_size"], errors="coerce").astype("Int64")
    ami[out_ami_col] = pd.to_numeric(ami[out_ami_col], errors="coerce")
    max_size = int(ami["hh_size"].max())

    df[out_size_col] = df[hh_size_col].clip(lower=1, upper=max_size).astype("Int64")
    df = df.merge(
        ami.rename(columns={"hh_size": out_size_col})[[puma_col, out_size_col, out_ami_col]],
        on=[puma_col, out_size_col],
        how="left",
    )

    income = df[income_col].astype(float)
    df[out_flag_col] = np.where(df[out_ami_col].notna(), income <= df[out_ami_col], np.nan)
    df[out_weight_col] = np.where(
        df[out_flag_col] == True,  # noqa: E712
        1.0,
        np.where(df[out_flag_col] == False, 0.0, np.nan),  # noqa: E712
    )
    return df


def attach_ami_expected_probability(
    df: pd.DataFrame,
    puma_county_weights: pd.DataFrame,
    county_ami_long: pd.DataFrame,
    *,
    serial_col: str = "SERIALNO",
    puma_col: str = "PUMA",
    hh_size_col: str = "NP",
    income_col: str = "income_adjusted",
    out_weight_col: str = "ami_weight",
    out_prob_col: str = "ami_prob",
    out_flag_col: str = "le_80_ami",
    out_size_col: str = "hh_size_for_ami",
) -> pd.DataFrame:
    """Attach a fractional `ami_weight` in [0, 1] by summing county-share weights where the household qualifies.

    Useful when a PUMA spans multiple counties with different AMI thresholds; the binary
    blended approach can misclassify households near the threshold, while this version
    reports the expected probability of qualification given the within-PUMA county mix.
    """
    pc = puma_county_weights.copy()
    pc[puma_col] = pc[puma_col].astype(str).str.zfill(5)
    pc["weight_hh"] = pd.to_numeric(pc["weight_hh"], errors="coerce")

    cl = county_ami_long.rename(columns={"hh_size": out_size_col, "ami80": "AMI80_COUNTY"})
    pcx = pc.merge(cl, on="county_fips5", how="left")
    max_size = int(pcx[out_size_col].max())

    df[out_size_col] = df[hh_size_col].clip(lower=1, upper=max_size).astype("Int64")
    tmp = df[[serial_col, puma_col, out_size_col, income_col]].merge(
        pcx[[puma_col, out_size_col, "weight_hh", "AMI80_COUNTY"]],
        on=[puma_col, out_size_col],
        how="left",
    )
    tmp["eligible_in_county"] = np.where(
        tmp["AMI80_COUNTY"].notna(),
        tmp[income_col].astype(float) <= tmp["AMI80_COUNTY"].astype(float),
        np.nan,
    )
    tmp["prob_piece"] = np.where(
        tmp["eligible_in_county"].notna(),
        tmp["weight_hh"].astype(float) * tmp["eligible_in_county"].astype(float),
        np.nan,
    )
    prob = (
        tmp.groupby(serial_col, as_index=False)["prob_piece"]
        .sum()
        .rename(columns={"prob_piece": out_prob_col})
    )
    df = df.merge(prob, on=serial_col, how="left")
    df[out_weight_col] = df[out_prob_col]
    df[out_flag_col] = np.where(df[out_prob_col].notna(), df[out_prob_col] >= 0.5, np.nan)
    return df
