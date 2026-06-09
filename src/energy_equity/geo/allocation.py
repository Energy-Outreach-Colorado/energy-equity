"""Tract-household-weighted PUMA share allocation into a service territory.

Pipeline:
  1. Spatial-join each tract centroid (representative point) to a PUMA polygon.
  2. Intersect each tract polygon with the service-area polygon in an equal-area CRS
     to compute the area fraction of each tract inside the service area.
  3. Weight that fraction by ACS B11001 tract household counts to estimate the number
     of households each tract contributes to the service area.
  4. Aggregate to PUMA: the share of a PUMA's households that fall in the service area.
  5. Allocate PUMA-level point and replicate estimates by multiplying by the PUMA share.

# Methodology notes (relevant to all callers)

- **Area-weighting limitation.** Step 2 assumes uniform household density within each
  tract. For irregular service shapes (e.g. utility pipe buffers), the populated portion
  of a partially overlapping tract may not equal the area-overlap fraction. Block-group
  level allocation would be tighter; for utility footprints with wide overlap it's a
  small effect.
- **Within-PUMA homogeneity.** Step 5 implicitly assumes the in-service portion of each
  PUMA has the same distribution of energy burden / AMI / SMI as the full PUMA. This is
  the standard small-area-estimation compromise — call it out in any methodology
  appendix that goes with published numbers.
- **Replicate propagation is preferred over RSS** for service-territory MOEs because
  PUMAs within a state are not independent (they share the same Census frame and
  replicate-weight design).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from loguru import logger
from shapely.ops import unary_union

from ..io.geo import CRS_EQUAL_AREA, get_first_existing_col, read_geofile
from ..weights.sdr import PUMS_REPLICATE_COUNT, Z_90, sdr_moe


def assign_puma_to_tracts(tracts: gpd.GeoDataFrame, pumas: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Assign each tract a PUMA code via representative-point spatial join.

    Uses representative_point (guaranteed inside the polygon) rather than the centroid,
    which can fall outside concave polygons. Tracts that don't match a PUMA are kept
    with `PUMA = <NA>` and excluded from PUMA-level aggregation downstream.
    """
    tract_geoid_col = get_first_existing_col(
        tracts, ("GEOID", "GEOID20"), description="tract GEOID"
    )
    puma_code_col = get_first_existing_col(
        pumas, ("PUMACE20", "PUMA5CE", "PUMACE10"), description="PUMA code"
    )

    if tracts.crs != pumas.crs:
        pumas = pumas.to_crs(tracts.crs)

    pts = tracts[[tract_geoid_col, "geometry"]].copy()
    pts["geometry"] = pts.geometry.representative_point()
    pumas_renamed = pumas[[puma_code_col, "geometry"]].rename(columns={puma_code_col: "PUMA"})

    joined = gpd.sjoin(pts, pumas_renamed, how="left", predicate="within")

    out = tracts.copy()
    out["tract_geoid"] = out[tract_geoid_col].astype(str).str.zfill(11)
    # Use pandas nullable string so missing matches stay <NA> rather than becoming "nan".
    puma_series = joined["PUMA"].astype("string").str.replace(r"\.0$", "", regex=True)
    out["PUMA"] = puma_series.str.zfill(5)

    missing = int(out["PUMA"].isna().sum())
    if missing:
        logger.warning(
            "{} tracts did not match a PUMA via representative-point join; "
            "they will be excluded from PUMA-level allocation totals.",
            missing,
        )
    return out


def dissolve_service_area(service_path: str | Path) -> gpd.GeoDataFrame:
    """Read a service-area shapefile and dissolve all features into a single (multi)polygon.

    Returns a GeoDataFrame with one row and a `service_name` column derived from the
    input file stem. CRS is preserved.
    """
    gdf = read_geofile(service_path)
    if gdf.empty:
        raise ValueError(f"No features found in {service_path}")
    geom = unary_union(gdf.geometry)
    return gpd.GeoDataFrame(
        {"service_name": [Path(service_path).stem]}, geometry=[geom], crs=gdf.crs
    )


def compute_household_weighted_puma_shares(
    tracts_with_puma: gpd.GeoDataFrame,
    tract_households: pd.DataFrame,
    service_union: gpd.GeoDataFrame,
    urban_areas: gpd.GeoDataFrame | None = None,
    *,
    area_crs: str = CRS_EQUAL_AREA,
) -> pd.DataFrame:
    """For each PUMA, compute the share of its households that fall in the service area.

    All area arithmetic is done in `area_crs` (default EPSG:5070 Conus Albers) so the
    intersection areas are meaningful. If `urban_areas` is provided, the function also
    returns the urban share of the in-service households.

    Output columns:
      PUMA, households_total, households_in_service, households_in_service_urban,
      share_households_in_service, urban_share_within_service, households_in_service_rural.
    """
    th = tract_households.copy()
    if "tract_geoid" not in th.columns:
        if "geoid" in th.columns:
            th = th.rename(columns={"geoid": "tract_geoid"})
        else:
            raise ValueError("tract_households must include a 'tract_geoid' or 'geoid' column.")
    th["households"] = pd.to_numeric(th["households"], errors="coerce").fillna(0.0)

    t = tracts_with_puma.merge(th[["tract_geoid", "households"]], on="tract_geoid", how="left")
    t["households"] = t["households"].fillna(0.0)

    t_area = t.to_crs(area_crs).copy()
    s_area = service_union.to_crs(area_crs)
    service_geom = s_area.geometry.iloc[0]

    tract_area = t_area.geometry.area
    in_service_geom = t_area.geometry.intersection(service_geom)
    in_service_area = in_service_geom.area

    t_area["share_tract_in_service"] = np.where(
        tract_area > 0, (in_service_area / tract_area).clip(0, 1), 0.0
    )
    t_area["hh_in_service"] = t_area["households"] * t_area["share_tract_in_service"]

    have_urban = urban_areas is not None and not urban_areas.empty
    if have_urban:
        u = urban_areas.to_crs(area_crs)
        minx, miny, maxx, maxy = service_geom.bounds
        try:
            u = u.cx[minx:maxx, miny:maxy]
        except Exception:
            pass
        urban_union = unary_union(u.geometry)
        urban_in_service_area = in_service_geom.intersection(urban_union).area
        t_area["share_tract_in_service_urban"] = np.where(
            tract_area > 0, (urban_in_service_area / tract_area).clip(0, 1), 0.0
        )
        t_area["hh_in_service_urban"] = (
            t_area["households"] * t_area["share_tract_in_service_urban"]
        )
    else:
        t_area["hh_in_service_urban"] = 0.0

    by_puma = t_area.groupby("PUMA", as_index=False, dropna=True).agg(
        households_total=("households", "sum"),
        households_in_service=("hh_in_service", "sum"),
        households_in_service_urban=("hh_in_service_urban", "sum"),
    )
    by_puma["share_households_in_service"] = np.where(
        by_puma["households_total"] > 0,
        (by_puma["households_in_service"] / by_puma["households_total"]).clip(0, 1),
        0.0,
    )
    if have_urban:
        by_puma["urban_share_within_service"] = np.where(
            by_puma["households_in_service"] > 0,
            (by_puma["households_in_service_urban"] / by_puma["households_in_service"]).clip(0, 1),
            np.nan,
        )
        by_puma["households_in_service_rural"] = (
            by_puma["households_in_service"] - by_puma["households_in_service_urban"]
        )
    else:
        # No urban-area input -> no urban/rural split. Use NaN sentinels so downstream
        # consumers know the split is unavailable rather than treating everything as rural.
        by_puma["urban_share_within_service"] = np.nan
        by_puma["households_in_service_urban"] = np.nan
        by_puma["households_in_service_rural"] = np.nan
    return by_puma


def allocate_puma_counts_to_service(
    puma_overall: pd.DataFrame,
    puma_shares: pd.DataFrame,
    *,
    metrics: Sequence[str],
    replicates: dict[str, np.ndarray] | None = None,
    compute_moe: bool = True,
    replicate_count: int = PUMS_REPLICATE_COUNT,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Allocate PUMA-level metrics into a service territory.

    Returns:
      by_puma: per-PUMA point estimates inside the service area (plus urban/rural split).
      totals: 3-row totals (segment = all/urban/rural) with point estimates and optional
        90% MOEs derived by propagating replicate estimates through the share weighting.
      rates: long-form table of selected rates (point + MOE).

    `metrics` lists the columns in `puma_overall` to allocate (e.g.,
    `["hh_total_w", "hh_eb_w", "hh_le80_w", ...]`). `replicates` is the dict returned by
    `puma_table.run` keyed by metric name without the `_w` suffix (e.g. `"hh_total"`)
    mapping to (n_puma, replicate_count) arrays of replicate estimates per PUMA in the
    same row order as `puma_overall`. When provided, MOEs are computed via the SDR
    formula on the replicate-aggregated service totals.
    """
    df = puma_overall.copy()
    df["PUMA"] = df["PUMA"].astype(str).str.zfill(5)
    shares = puma_shares.copy()
    shares["PUMA"] = shares["PUMA"].astype(str).str.zfill(5)

    missing_metrics = [m for m in metrics if m not in df.columns]
    if missing_metrics:
        raise ValueError(
            f"Missing metric columns in puma_overall: {missing_metrics}. Have: {list(df.columns)[:20]}..."
        )

    out = df.merge(
        shares[
            [
                "PUMA",
                "share_households_in_service",
                "urban_share_within_service",
            ]
        ],
        on="PUMA",
        how="left",
    )
    out["share_households_in_service"] = out["share_households_in_service"].fillna(0.0)

    has_urban = out["urban_share_within_service"].notna().any()
    urb = out["urban_share_within_service"].fillna(0.0).astype(float).to_numpy()
    w_all = out["share_households_in_service"].astype(float).to_numpy()

    seg_weights = {
        "all": w_all,
        "urban": w_all * urb if has_urban else None,
        "rural": w_all * (1.0 - urb) if has_urban else None,
    }

    # Per-PUMA in-service allocations (the most granular output).
    for metric in metrics:
        base_val = pd.to_numeric(out[metric], errors="coerce").fillna(0.0).astype(float).to_numpy()
        out[f"{metric}_in_service"] = base_val * w_all
        if has_urban:
            out[f"{metric}_in_service_urban"] = out[f"{metric}_in_service"] * urb
            out[f"{metric}_in_service_rural"] = out[f"{metric}_in_service"] * (1.0 - urb)
        else:
            out[f"{metric}_in_service_urban"] = np.nan
            out[f"{metric}_in_service_rural"] = np.nan

    keep_cols = (
        ["PUMA", "share_households_in_service", "urban_share_within_service"]
        + list(metrics)
        + [f"{m}_in_service" for m in metrics]
        + [f"{m}_in_service_urban" for m in metrics]
        + [f"{m}_in_service_rural" for m in metrics]
    )
    by_puma = out[keep_cols].copy()

    # Totals + replicate-propagated MOEs.
    totals_rows = []
    for seg_name in ("all", "urban", "rural"):
        w = seg_weights[seg_name]
        if w is None:
            continue
        row: dict[str, float | str] = {"segment": seg_name}
        for metric in metrics:
            base_val = (
                pd.to_numeric(out[metric], errors="coerce").fillna(0.0).astype(float).to_numpy()
            )
            point = float(np.dot(w, base_val))
            row[f"{metric}_in_service"] = point
            if compute_moe and replicates is not None and metric in replicates:
                mat = replicates[metric]  # (n_puma, R) in row order of `out`
                if mat.shape != (len(out), replicate_count):
                    raise ValueError(
                        f"Replicate matrix for {metric!r} has shape {mat.shape}, "
                        f"expected ({len(out)}, {replicate_count})"
                    )
                reps_service = w @ mat  # (R,)
                row[f"{metric}_in_service_moe90"] = float(
                    sdr_moe(np.array([point]), reps_service[np.newaxis, :], z=Z_90)[0]
                )
            else:
                row[f"{metric}_in_service_moe90"] = float("nan") if compute_moe else None
        totals_rows.append(row)

    totals = pd.DataFrame(totals_rows)

    # Rates (selected). Numerator and denominator must both be in `metrics`.
    rate_defs = [
        ("pct_le80_of_total", "hh_le80_w", "hh_total_w"),
        ("pct_energy_burdened_of_valid", "hh_eb_w", "hh_burden_valid_w"),
        ("pct_high_energy_burdened_of_valid", "hh_heb_w", "hh_burden_valid_w"),
        ("pct_energy_burdened_among_le80", "hh_eb_le80_w", "hh_le80_w"),
        ("pct_high_energy_burdened_among_le80", "hh_heb_le80_w", "hh_le80_w"),
        ("pct_le60_smi_of_total", "hh_le60_smi_w", "hh_total_w"),
        ("pct_energy_burdened_among_le60_smi", "hh_eb_le60_smi_w", "hh_le60_smi_w"),
        ("pct_rent_burdened_of_rent_valid", "hh_rent_burdened_w", "hh_rent_valid_w"),
        ("pct_rent_burdened_among_le80", "hh_rent_burdened_le80_w", "hh_le80_w"),
    ]
    rate_rows = []
    for seg_name in ("all", "urban", "rural"):
        w = seg_weights[seg_name]
        if w is None:
            continue
        for rate_name, num, den in rate_defs:
            if num not in metrics or den not in metrics:
                continue
            num_arr = pd.to_numeric(out[num], errors="coerce").fillna(0.0).astype(float).to_numpy()
            den_arr = pd.to_numeric(out[den], errors="coerce").fillna(0.0).astype(float).to_numpy()
            num_point = float(np.dot(w, num_arr))
            den_point = float(np.dot(w, den_arr))
            est = (num_point / den_point) if den_point > 0 else float("nan")

            moe = float("nan")
            if compute_moe and replicates is not None and num in replicates and den in replicates:
                num_reps = w @ replicates[num]  # (R,)
                den_reps = w @ replicates[den]  # (R,)
                with np.errstate(divide="ignore", invalid="ignore"):
                    rep_ratio = np.where(den_reps > 0, num_reps / den_reps, np.nan)
                finite = np.isfinite(rep_ratio)
                if np.isfinite(est) and finite.sum() >= max(1, replicate_count // 2):
                    rep_ratio = np.where(finite, rep_ratio, est)
                    moe = float(sdr_moe(np.array([est]), rep_ratio[np.newaxis, :], z=Z_90)[0])
            rate_rows.append(
                {
                    "segment": seg_name,
                    "rate": rate_name,
                    "estimate": est,
                    "moe90": moe,
                    "numerator": num,
                    "denominator": den,
                    "numerator_est": num_point,
                    "denominator_est": den_point,
                }
            )
    rates = pd.DataFrame(rate_rows)
    return by_puma, totals, rates
