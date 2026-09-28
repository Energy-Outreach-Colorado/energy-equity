"""Calibrate PUMS utility costs against every utility territory in a state at once.

`calibration` anchors one fuel to one utility's administrative average bill, which suits
an analysis of a single service area. A statewide product such as a map needs every
utility at the same time. PUMS locates a household only to its PUMA, and one PUMA can be
served by several utilities, so the utility behind any single household is unknown. This
module estimates one factor per utility territory and gives each PUMA the blend of its
utilities' factors.

Estimation. A territory has PUMA household shares s_pu, from
`geo.allocation.compute_household_weighted_puma_shares` or `puma_shares_from_units`. Its
observed average annual bill weights each paying household by WGTP * s_pu, exactly as
`calibration.compute_bill_calibration` does for a single service area, and its own
factor is target_u / observed_u. Every factor is estimated from uncalibrated costs before
any is applied, so the order of territories and fuels does not matter.

Overlap. A PUMA holds about 100,000 people, so the weighted households of a small utility
are mostly other utilities' customers and its own factor mostly describes its neighbours.
The overlap of a territory is the household-weighted mean of its shares over its own
weight, sum_p s_pu^2 H_p / sum_p s_pu H_p, where H_p is the PUMA's weighted household
count. A territory whose overlap is below `min_overlap` (0.35 by default) takes the
pooled factor of the reliable territories with the same EIA ownership class, which is
sum(target * W) / sum(observed * W) with W the payer-weighted households. A class with no
reliable territory falls back to the pool of all reliable territories of the fuel. With
the 2024 Colorado 1-year PUMS, the own electric factors of reliable cooperatives mostly
ran 0.69 to 0.91 and those of municipal utilities 0.58 to 0.68 (checked 2026-09-27),
which is why pooling is by class rather than statewide.

Blending. The factor applied in PUMA p is sum_u s_pu f_u / sum_u s_pu over the
territories of that fuel, so overlapping territories are averaged rather than summed.
Households in a PUMA that no territory touches keep factor 1.

Combined bills. Some utilities, Xcel Energy among them, send one bill for gas and
electricity, and PUMS records such a household with GASFP = 2 (gas included in the
electricity payment) and the whole bill in ELEP. In the 2024 Colorado 1-year PUMS 25.9%
of weighted electric payers are in this group, and adding them to the combined-bill
households' gas count brings PUMS gas households within 1% of the EIA-176 customer total
(checked 2026-09-27). These households are left out of the electric average. Their
electric cost is calibrated separately, per electric territory, against the combined
target T_e,u + T_g,u, where T_g,u is the blended EIA gas target over territory u
weighted by its combined-bill households. The combined factors are pooled and blended
exactly like the others. When the housing data has no GASFP column or no gas territories
are given, these households are treated as ordinary electric payers.

Uncertainty in the factors is not propagated to margins of error, the same convention
as the single-territory calibration and the geographic shares.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd
from loguru import logger

DEFAULT_MIN_OVERLAP = 0.35
COMBINED_BILL_GASFP = 2

FACTOR_COLUMNS = (
    "fuel",
    "utility_id",
    "name",
    "ownership",
    "target_annual_bill",
    "observed_avg_annual_bill",
    "own_factor",
    "overlap",
    "n_payers_unweighted",
    "payer_weighted_households",
    "reliable",
    "factor",
    "factor_source",
)


@dataclass(frozen=True)
class Territory:
    """One utility's service territory for a single fuel.

    `shares` has a `PUMA` column and a `share_households_in_service` column giving the
    share of each PUMA's households inside the territory. `ownership` is the EIA
    ownership class (for example "Cooperative" or "Municipal") used to pool the factors
    of territories that overlap their PUMAs too little to estimate their own.
    `target_annual_bill` is the administrative average annual residential bill.
    """

    utility_id: int
    name: str
    ownership: str
    target_annual_bill: float
    shares: pd.DataFrame


@dataclass
class TerritoryCalibrationResult:
    """Per-territory factors for each fuel and the blended factor applied in each PUMA.

    `electric`, `gas` and `combined` hold one row per territory with the columns in
    `FACTOR_COLUMNS`, or are None when that fuel was not calibrated. `puma_factors` has
    one row per PUMA in the microdata with `electric_factor`, `gas_factor` and
    `combined_factor`, NaN where no territory of that fuel touches the PUMA.
    """

    electric: pd.DataFrame | None
    gas: pd.DataFrame | None
    combined: pd.DataFrame | None
    puma_factors: pd.DataFrame
    min_overlap: float


def territory_share_series(territory: Territory) -> pd.Series:
    """The territory's household shares indexed by five-digit PUMA code, clipped to [0, 1]."""
    shares = territory.shares
    puma = shares["PUMA"].astype(str).str.zfill(5)
    values = pd.to_numeric(shares["share_households_in_service"], errors="coerce").fillna(0.0)
    return values.groupby(puma).sum().clip(lower=0.0, upper=1.0)


def territory_overlap(shares: pd.Series, puma_households: pd.Series) -> float:
    """Household-weighted mean share over the territory's own weight.

    1 means every weighted household the territory draws on sits in a PUMA it fully
    covers, and values near 0 mean its weight comes from slivers of PUMAs that are
    mostly served by others. Returns 0 when the territory touches no households.
    """
    households = puma_households.reindex(shares.index).fillna(0.0)
    weight = float((shares * households).sum())
    if weight <= 0:
        return 0.0
    return float((shares * shares * households).sum() / weight)


def _pooled_factor(rows: pd.DataFrame) -> float:
    weights = rows["payer_weighted_households"]
    denominator = float((rows["observed_avg_annual_bill"] * weights).sum())
    if denominator <= 0:
        return float("nan")
    return float((rows["target_annual_bill"] * weights).sum() / denominator)


def estimate_territory_factors(
    df: pd.DataFrame,
    territories: Sequence[Territory],
    *,
    cost_col: str,
    payer: pd.Series,
    weight_col: str = "WGTP",
    min_overlap: float = DEFAULT_MIN_OVERLAP,
    fuel: str = "fuel",
) -> pd.DataFrame:
    """Estimate each territory's own factor, its overlap, and the factor to apply.

    `payer` marks the households whose `cost_col` enters the observed averages. A
    territory is reliable when its overlap is at least `min_overlap` and its own factor
    is positive and finite. Reliable territories apply their own factor
    (`factor_source` "own"), and the rest apply the pooled factor of reliable
    territories with the same ownership class ("pooled_ownership") or of all reliable
    territories ("pooled_all"). A territory left with no usable factor applies 1
    ("uncalibrated"). Does not modify `df`.
    """
    ids = [t.utility_id for t in territories]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{fuel} territories must have unique utility_id values: {ids}")

    weights = pd.to_numeric(df[weight_col], errors="coerce").fillna(0.0)
    puma = df["PUMA"].astype(str).str.zfill(5)
    puma_households = weights.groupby(puma).sum()
    cost = pd.to_numeric(df[cost_col], errors="coerce")
    eligible = payer.astype(bool) & cost.notna() & (weights > 0)

    records = []
    for territory in territories:
        shares = territory_share_series(territory)
        territory_weights = weights * puma.map(shares).fillna(0.0)
        mask = eligible & (territory_weights > 0)
        weighted = float(territory_weights[mask].sum())
        observed = (
            float((cost[mask] * territory_weights[mask]).sum() / weighted)
            if weighted > 0
            else float("nan")
        )
        target = float(territory.target_annual_bill)
        own = target / observed if observed > 0 and np.isfinite(target) else float("nan")
        records.append(
            {
                "fuel": fuel,
                "utility_id": territory.utility_id,
                "name": territory.name,
                "ownership": territory.ownership,
                "target_annual_bill": target,
                "observed_avg_annual_bill": observed,
                "own_factor": own,
                "overlap": territory_overlap(shares, puma_households),
                "n_payers_unweighted": int(mask.sum()),
                "payer_weighted_households": weighted,
            }
        )

    out = pd.DataFrame.from_records(records, columns=list(FACTOR_COLUMNS[:10]))
    usable = np.isfinite(out["own_factor"]) & (out["own_factor"] > 0)
    out["reliable"] = usable & (out["overlap"] >= min_overlap)
    reliable = out[out["reliable"]]
    pooled_all = _pooled_factor(reliable) if len(reliable) else float("nan")

    factors: list[float] = []
    sources: list[str] = []
    for row in out.itertuples(index=False):
        if row.reliable:
            factors.append(float(row.own_factor))
            sources.append("own")
            continue
        same_class = reliable[reliable["ownership"] == row.ownership]
        pooled = _pooled_factor(same_class) if len(same_class) else float("nan")
        if np.isfinite(pooled) and pooled > 0:
            factors.append(pooled)
            sources.append("pooled_ownership")
        elif np.isfinite(pooled_all) and pooled_all > 0:
            factors.append(pooled_all)
            sources.append("pooled_all")
        else:
            factors.append(1.0)
            sources.append("uncalibrated")
    out["factor"] = factors
    out["factor_source"] = sources
    return out[list(FACTOR_COLUMNS)]


def blend_territory_values(
    values: Mapping[int, float], territories: Sequence[Territory]
) -> pd.Series:
    """Share-weighted mean of a per-territory value in each PUMA.

    Returns a Series indexed by PUMA with sum_u s_pu v_u / sum_u s_pu over the
    territories whose value is finite, and NaN for PUMAs that no such territory touches.
    """
    numerator = pd.Series(dtype=float)
    denominator = pd.Series(dtype=float)
    for territory in territories:
        value = values.get(territory.utility_id)
        if value is None or not np.isfinite(value):
            continue
        shares = territory_share_series(territory)
        numerator = numerator.add(shares * float(value), fill_value=0.0)
        denominator = denominator.add(shares, fill_value=0.0)
    blended = numerator / denominator
    return blended.where(denominator > 0)


def _combined_bill_territories(
    df: pd.DataFrame,
    electric: Sequence[Territory],
    gas: Sequence[Territory],
    combined: pd.Series,
    weights: pd.Series,
    puma: pd.Series,
) -> list[Territory]:
    """Electric territories retargeted to the combined electric plus gas bill."""
    gas_target = blend_territory_values(
        {t.utility_id: float(t.target_annual_bill) for t in gas}, gas
    )
    combined_households = weights.where(combined, 0.0).groupby(puma).sum()
    out = []
    for territory in electric:
        shares = territory_share_series(territory)
        weight = shares * combined_households.reindex(shares.index).fillna(0.0)
        target = gas_target.reindex(shares.index)
        ok = target.notna() & (weight > 0)
        total = float(weight[ok].sum())
        gas_part = float((weight[ok] * target[ok]).sum() / total) if total > 0 else float("nan")
        out.append(
            Territory(
                utility_id=territory.utility_id,
                name=territory.name,
                ownership=territory.ownership,
                target_annual_bill=float(territory.target_annual_bill) + gas_part,
                shares=territory.shares,
            )
        )
    return out


def build_territories(
    polygons,
    crosswalk: pd.DataFrame,
    *,
    fuel: str,
    tracts_with_puma,
    tract_households: pd.DataFrame,
    state: str,
    year: int,
    name_col: str = "Name",
    eia_csv: str | None = None,
) -> list[Territory]:
    """Build one `Territory` per EIA utility from territory polygons and a crosswalk.

    `polygons` is a GeoDataFrame of service-territory features whose `name_col` names
    the utility. `crosswalk` has `territory_name` and `eia_id` columns and may map
    several polygon names to one EIA identifier, as when a utility's territory is drawn
    in pieces. `fuel` is "electric" (EIA-861 utility numbers) or "gas" (EIA-176 company
    identifiers). Each identifier's polygons are dissolved and turned into
    household-weighted PUMA shares with
    `geo.allocation.compute_household_weighted_puma_shares`, and its target and
    ownership class come from the packaged EIA table for `state` and `year`, or from
    `eia_csv` when given. A crosswalk name with no matching polygon raises, so a renamed
    feature cannot silently drop a utility.
    """
    import geopandas as gpd
    from shapely.ops import unary_union

    from .calibration import load_eia176_average_bill, load_eia861_average_bill
    from .geo.allocation import compute_household_weighted_puma_shares

    if fuel not in ("electric", "gas"):
        raise ValueError(f"fuel must be 'electric' or 'gas', got {fuel!r}")
    missing = sorted(set(crosswalk["territory_name"]) - set(polygons[name_col]))
    if missing:
        raise ValueError(f"crosswalk names with no {fuel} territory polygon: {missing}")

    territories = []
    for eia_id, group in crosswalk.groupby("eia_id", sort=True):
        features = polygons[polygons[name_col].isin(group["territory_name"])]
        union = gpd.GeoDataFrame(geometry=[unary_union(features.geometry)], crs=polygons.crs)
        shares = compute_household_weighted_puma_shares(tracts_with_puma, tract_households, union)
        if fuel == "electric":
            eia = load_eia861_average_bill(
                eia_csv, utility_number=int(eia_id), state=state, year=year
            )
            name = eia["utility_name"]
        else:
            eia = load_eia176_average_bill(eia_csv, company_id=int(eia_id), state=state, year=year)
            name = eia["company_name"]
        territories.append(
            Territory(
                utility_id=int(eia_id),
                name=str(name),
                ownership=str(eia.get("ownership", "unknown")),
                target_annual_bill=float(eia["target_annual_bill"]),
                shares=shares[["PUMA", "share_households_in_service"]],
            )
        )
    return territories


def _log_factors(table: pd.DataFrame) -> None:
    for row in table.itertuples(index=False):
        logger.info(
            "{} calibration, {}: observed ${:.0f}, target ${:.0f}, own factor {:.3f}, "
            "overlap {:.2f}, applied {:.3f} ({})",
            row.fuel,
            row.name,
            row.observed_avg_annual_bill,
            row.target_annual_bill,
            row.own_factor,
            row.overlap,
            row.factor,
            row.factor_source,
        )


def apply_territory_calibration(
    df: pd.DataFrame,
    *,
    electric: Sequence[Territory] = (),
    gas: Sequence[Territory] = (),
    min_overlap: float = DEFAULT_MIN_OVERLAP,
    weight_col: str = "WGTP",
    raw_electric_col: str = "ELEP",
    raw_gas_col: str = "GASP",
    gas_flag_col: str = "GASFP",
    electric_col: str = "annual_electric_cost_adj",
    gas_col: str = "annual_gas_cost_adj",
    other_col: str = "annual_other_fuel_cost_adj",
    total_col: str = "annual_energy_cost_adj",
) -> TerritoryCalibrationResult:
    """Estimate territory factors, blend them per PUMA, and rescale costs in place.

    Must run after `pums.energy_cost.apply_energy_cost_adjustment` and before burden is
    computed, since a level shift moves households across the burden thresholds. Only
    households with a reported cost for a fuel are rescaled for that fuel, and each
    rescaled household's total re-sums from its components. Rows with no reported cost
    keep their original values, including NaN totals under missing_cost_rule="nan".
    """
    if not electric and not gas:
        raise ValueError("apply_territory_calibration needs electric or gas territories")

    weights = pd.to_numeric(df[weight_col], errors="coerce").fillna(0.0)
    puma = df["PUMA"].astype(str).str.zfill(5)
    electric_payer = df[raw_electric_col].notna()
    gas_payer = df[raw_gas_col].notna()

    combined = pd.Series(False, index=df.index)
    if electric and gas and gas_flag_col in df.columns:
        flag = pd.to_numeric(df[gas_flag_col], errors="coerce")
        combined = electric_payer & (flag == COMBINED_BILL_GASFP)
    elif electric and gas:
        logger.warning(
            "no {} column; households with gas on their electric bill are calibrated "
            "as ordinary electric payers",
            gas_flag_col,
        )

    electric_table = None
    if electric:
        electric_table = estimate_territory_factors(
            df,
            electric,
            cost_col=electric_col,
            payer=electric_payer & ~combined,
            weight_col=weight_col,
            min_overlap=min_overlap,
            fuel="electric",
        )
    gas_table = None
    if gas:
        gas_table = estimate_territory_factors(
            df,
            gas,
            cost_col=gas_col,
            payer=gas_payer,
            weight_col=weight_col,
            min_overlap=min_overlap,
            fuel="gas",
        )
    combined_table = None
    if combined.any():
        combined_territories = _combined_bill_territories(
            df, electric, gas, combined, weights, puma
        )
        combined_table = estimate_territory_factors(
            df,
            combined_territories,
            cost_col=electric_col,
            payer=combined,
            weight_col=weight_col,
            min_overlap=min_overlap,
            fuel="combined",
        )

    pumas = pd.Index(sorted(puma.unique()), name="PUMA")
    puma_factors = pd.DataFrame(index=pumas)
    for column, table, territories in (
        ("electric_factor", electric_table, electric),
        ("gas_factor", gas_table, gas),
        ("combined_factor", combined_table, electric),
    ):
        if table is None:
            puma_factors[column] = np.nan
            continue
        values = dict(zip(table["utility_id"], table["factor"], strict=True))
        puma_factors[column] = blend_territory_values(values, territories).reindex(pumas)

    electric_factor = puma.map(puma_factors["electric_factor"]).fillna(1.0)
    gas_factor = puma.map(puma_factors["gas_factor"]).fillna(1.0)
    combined_factor = puma.map(puma_factors["combined_factor"]).fillna(1.0)

    if electric:
        separate = electric_payer & ~combined
        df.loc[separate, electric_col] = (
            df.loc[separate, electric_col].astype(float) * electric_factor[separate]
        )
        df.loc[combined, electric_col] = (
            df.loc[combined, electric_col].astype(float) * combined_factor[combined]
        )
    if gas:
        df.loc[gas_payer, gas_col] = (
            df.loc[gas_payer, gas_col].astype(float) * gas_factor[gas_payer]
        )

    touched = pd.Series(False, index=df.index)
    if electric:
        touched |= electric_payer
    if gas:
        touched |= gas_payer
    total = pd.Series(0.0, index=df.index)
    for col in (electric_col, gas_col, other_col):
        total = total + pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    df.loc[touched, total_col] = total[touched]

    for table in (electric_table, gas_table, combined_table):
        if table is not None:
            _log_factors(table)

    return TerritoryCalibrationResult(
        electric=electric_table,
        gas=gas_table,
        combined=combined_table,
        puma_factors=puma_factors.reset_index(),
        min_overlap=min_overlap,
    )
