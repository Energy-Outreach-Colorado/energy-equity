"""Calibrate PUMS self-reported electric costs against EIA-861 administrative data.

ACS PUMS electric costs (ELEP) are survey self-reports; EIA Form 861 provides the
administrative average annual residential bill per utility (revenue / customers).
Following the DOE LEAD methodology, the level of the PUMS electric component can be
anchored to the EIA-861 average while preserving the PUMS distribution shape:

    factor = target_avg_annual_bill / observed_weighted_avg_annual_bill

The observed mean and the scaling both cover only households that report an electric
cost (raw ELEP present). Households whose electric cost is missing (included in rent,
no charge — indistinguishable in PUMS) are excluded from both sides, consistent with
EIA-861's customer denominator, and their cost columns are never modified.

Calibration must run before energy burden is thresholded: a cost level shift moves
households across the burden cutoffs nonlinearly, so it cannot be applied to
aggregated counts after the fact. `pums.prepare.prepare_household_microdata` invokes
this module between cost adjustment and burden computation when
`cfg.calibration.electric` is configured.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import pandas as pd
from loguru import logger

PACKAGED_EIA861_RESOURCE = files("energy_equity").joinpath("data/eia861/eia861_residential.csv")

EIA861_REQUIRED_COLUMNS = (
    "utility_number",
    "utility_name",
    "state",
    "customer_class",
    "revenue_thousand_dollars",
    "customers",
)

RESIDENTIAL_CLASS = "residential"


def load_eia861_average_bill(
    path: str | Path | None = None,
    *,
    utility_number: int | None = None,
    utility_name: str | None = None,
    state: str | None = None,
    year: int | None = None,
) -> dict:
    """Derive the average annual residential electric bill for one utility from a slim CSV.

    `path=None` reads the packaged `data/eia861/eia861_residential.csv` (see its README
    for coverage and derivation); pass a path to use your own table. Required columns:
    utility_number, utility_name, state, customer_class, revenue_thousand_dollars,
    customers. Optional: year, sales_mwh.

    Rows are filtered to `customer_class == "residential"`, the requested utility
    (by number and/or case-insensitive name), `state`, and `year` when given; exactly one
    row must remain — multi-state utilities and multi-year tables need the extra filters.
    Returns a dict with `target_annual_bill`
    (= revenue_thousand_dollars * 1000 / customers), the matched utility identifiers,
    and `avg_rate_per_kwh` when sales_mwh is available.
    """
    if utility_number is None and utility_name is None:
        raise ValueError("Provide utility_number and/or utility_name to select a utility.")

    if path is not None:
        df = pd.read_csv(path)
    else:
        path = "packaged eia861_residential.csv"
        with PACKAGED_EIA861_RESOURCE.open("r", encoding="utf-8") as fh:
            df = pd.read_csv(fh)
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = [c for c in EIA861_REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"EIA-861 CSV at {path} is missing required columns {missing}. "
            f"Found: {df.columns.tolist()}"
        )

    rows = df[df["customer_class"].astype(str).str.strip().str.lower() == RESIDENTIAL_CLASS]
    if utility_number is not None:
        rows = rows[pd.to_numeric(rows["utility_number"], errors="coerce") == float(utility_number)]
    if utility_name is not None:
        rows = rows[
            rows["utility_name"].astype(str).str.strip().str.lower() == utility_name.strip().lower()
        ]
    if state is not None:
        rows = rows[rows["state"].astype(str).str.strip().str.upper() == state.strip().upper()]
    if year is not None:
        if "year" not in rows.columns:
            raise ValueError(
                f"eia861_year={year} was requested but the CSV at {path} has no 'year' column."
            )
        rows = rows[pd.to_numeric(rows["year"], errors="coerce") == float(year)]

    selection = {
        "utility_number": utility_number,
        "utility_name": utility_name,
        "state": state,
        "year": year,
    }
    if len(rows) == 0:
        raise ValueError(f"No residential EIA-861 row in {path} matches {selection}.")
    if len(rows) > 1:
        raise ValueError(
            f"{len(rows)} residential EIA-861 rows in {path} match {selection}; "
            "narrow the selection (add utility_number, state, or eia861_year)."
        )

    row = rows.iloc[0]
    revenue = float(row["revenue_thousand_dollars"])
    customers = float(row["customers"])
    if customers <= 0:
        raise ValueError(f"EIA-861 row for {selection} has non-positive customers: {customers}")

    result = {
        "target_annual_bill": revenue * 1000.0 / customers,
        "utility_number": int(row["utility_number"]),
        "utility_name": str(row["utility_name"]),
        "state": str(row["state"]),
        "revenue_thousand_dollars": revenue,
        "customers": customers,
        "year": int(row["year"]) if "year" in rows.columns and pd.notna(row.get("year")) else None,
    }
    sales = row.get("sales_mwh")
    if sales is not None and pd.notna(sales) and float(sales) > 0:
        result["avg_rate_per_kwh"] = revenue * 1000.0 / (float(sales) * 1000.0)
    return result


def compute_electric_calibration(
    df: pd.DataFrame,
    *,
    target_annual_bill: float,
    service_shares: pd.DataFrame | None = None,
    weight_col: str = "WGTP",
    electric_col: str = "annual_electric_cost_adj",
    raw_electric_col: str = "ELEP",
) -> dict:
    """Compare the weighted observed average electric bill to a target; return diagnostics.

    The observed mean covers only households with a reported electric cost
    (`raw_electric_col` not NaN). Weights are `weight_col`, multiplied by each PUMA's
    `share_households_in_service` when `service_shares` is provided; otherwise the mean
    is statewide and flagged `scope="state"` with a warning, since the EIA-861 target is
    utility-specific.

    Returns observed/target averages, `factor` (target / observed), payer counts, and
    scope. Does not modify `df`.
    """
    payer = df[raw_electric_col].notna()
    weights = pd.to_numeric(df[weight_col], errors="coerce").fillna(0.0)

    if service_shares is not None:
        share_map = service_shares.set_index("PUMA")["share_households_in_service"]
        shares = (
            pd.to_numeric(df["PUMA"].map(share_map), errors="coerce").fillna(0.0).clip(lower=0.0)
        )
        weights = weights * shares
        scope = "service_area"
    else:
        scope = "state"
        logger.warning(
            "electric calibration: no service shares supplied; observed average is "
            "statewide, while the EIA-861 target is utility-specific"
        )

    cost = pd.to_numeric(df[electric_col], errors="coerce")
    mask = payer & cost.notna() & (weights > 0)
    weighted_payers = float(weights[mask].sum())
    if int(mask.sum()) == 0 or weighted_payers <= 0:
        raise ValueError(
            "electric calibration: no households with a reported electric cost and "
            "positive weight; cannot compute an observed average bill"
        )

    observed = float((cost[mask] * weights[mask]).sum() / weighted_payers)
    if observed <= 0:
        raise ValueError(
            f"electric calibration: observed average annual bill is {observed}; "
            "cannot derive a calibration factor"
        )

    return {
        "observed_avg_annual_bill": observed,
        "target_annual_bill": float(target_annual_bill),
        "factor": float(target_annual_bill) / observed,
        "n_payers_unweighted": int(mask.sum()),
        "payer_weighted_households": weighted_payers,
        "scope": scope,
    }


def apply_electric_calibration(
    df: pd.DataFrame,
    *,
    factor: float,
    raw_electric_col: str = "ELEP",
    electric_col: str = "annual_electric_cost_adj",
    gas_col: str = "annual_gas_cost_adj",
    other_col: str = "annual_other_fuel_cost_adj",
    total_col: str = "annual_energy_cost_adj",
) -> pd.DataFrame:
    """Scale the electric cost component of paying households and re-sum their totals.

    Only rows with `raw_electric_col` present are touched; all other rows keep their
    original component and total values (including NaN totals under
    missing_cost_rule="nan"). Operates in place.
    """
    payer = df[raw_electric_col].notna()
    df.loc[payer, electric_col] = df.loc[payer, electric_col].astype(float) * float(factor)
    df.loc[payer, total_col] = (
        df.loc[payer, electric_col].astype(float)
        + pd.to_numeric(df.loc[payer, gas_col], errors="coerce").fillna(0.0)
        + pd.to_numeric(df.loc[payer, other_col], errors="coerce").fillna(0.0)
    )
    return df
