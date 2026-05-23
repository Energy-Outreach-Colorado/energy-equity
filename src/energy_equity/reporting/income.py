"""Income-distribution comparison: service-area vs statewide.

Builds a 16-row table comparing the ACS B19001 income distribution inside the
service-area to the same distribution at the statewide level. The service-area
distribution is computed by allocating each tract's bin counts by the share of that
tract's area inside the service polygon (the same area-weighting used elsewhere in
this package).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .metrics import safe_div

# The 16 B19001 bins with their inclusive upper bounds (USD). The top bin is open-ended.
B19001_BINS: list[tuple[str, str, float | None]] = [
    ("B19001_002E", "<$10,000", 10_000.0),
    ("B19001_003E", "$10,000–$14,999", 15_000.0),
    ("B19001_004E", "$15,000–$19,999", 20_000.0),
    ("B19001_005E", "$20,000–$24,999", 25_000.0),
    ("B19001_006E", "$25,000–$29,999", 30_000.0),
    ("B19001_007E", "$30,000–$34,999", 35_000.0),
    ("B19001_008E", "$35,000–$39,999", 40_000.0),
    ("B19001_009E", "$40,000–$44,999", 45_000.0),
    ("B19001_010E", "$45,000–$49,999", 50_000.0),
    ("B19001_011E", "$50,000–$59,999", 60_000.0),
    ("B19001_012E", "$60,000–$74,999", 75_000.0),
    ("B19001_013E", "$75,000–$99,999", 100_000.0),
    ("B19001_014E", "$100,000–$124,999", 125_000.0),
    ("B19001_015E", "$125,000–$149,999", 150_000.0),
    ("B19001_016E", "$150,000–$199,999", 200_000.0),
    ("B19001_017E", "$200,000+", None),
]


@dataclass(frozen=True)
class IncomeDistribution:
    """Wide-format counts per B19001 bin and total."""

    counts: dict[str, float]
    total: float


def aggregate_b19001(
    b19001_per_tract: pd.DataFrame,
    *,
    weight_col: str | None = None,
) -> IncomeDistribution:
    """Sum B19001 counts across tracts. `weight_col` (e.g. service-area area fraction)
    multiplies each tract's row before summing; pass None for an unweighted sum (statewide).
    """
    df = b19001_per_tract.copy()
    if weight_col is None:
        weights = pd.Series(1.0, index=df.index)
    else:
        if weight_col not in df.columns:
            raise KeyError(f"weight_col {weight_col!r} not found in b19001 per-tract frame.")
        weights = pd.to_numeric(df[weight_col], errors="coerce").fillna(0.0)

    counts: dict[str, float] = {}
    for col, _label, _upper in B19001_BINS:
        if col not in df.columns:
            counts[col] = float("nan")
            continue
        counts[col] = float((pd.to_numeric(df[col], errors="coerce").fillna(0.0) * weights).sum())
    total = float(sum(v for v in counts.values() if not np.isnan(v)))
    return IncomeDistribution(counts=counts, total=total)


def build_income_distribution_comparison(
    service: IncomeDistribution, statewide: IncomeDistribution
) -> pd.DataFrame:
    """Side-by-side per-bin comparison with absolute and percentage-point differences."""
    rows = []
    for col, label, upper in B19001_BINS:
        s_count = service.counts.get(col, float("nan"))
        st_count = statewide.counts.get(col, float("nan"))
        s_share = safe_div(s_count, service.total)
        st_share = safe_div(st_count, statewide.total)
        rows.append(
            {
                "bin_variable": col,
                "bin_label": label,
                "upper_bound": upper,
                "service_households": s_count,
                "statewide_households": st_count,
                "service_share": s_share,
                "statewide_share": st_share,
                "share_diff_pp": (s_share - st_share) * 100.0
                if np.isfinite(s_share) and np.isfinite(st_share)
                else float("nan"),
            }
        )
    return pd.DataFrame(rows)


def build_cutpoint_shares(
    service: IncomeDistribution, statewide: IncomeDistribution, *, cutoffs: Sequence[float]
) -> pd.DataFrame:
    """Cumulative shares of households with income below each cutoff (service vs statewide).

    Used to express things like "X% of service-area households earn under $50k".
    """
    from .metrics import share_below_cutoff

    s_counts = pd.Series([service.counts[c[0]] for c in B19001_BINS])
    st_counts = pd.Series([statewide.counts[c[0]] for c in B19001_BINS])
    uppers = pd.Series([c[2] if c[2] is not None else 1e9 for c in B19001_BINS])

    rows = []
    for cutoff in cutoffs:
        s_share = share_below_cutoff(s_counts, uppers, cutoff)
        st_share = share_below_cutoff(st_counts, uppers, cutoff)
        rows.append(
            {
                "cutoff": cutoff,
                "service_share_below": s_share,
                "statewide_share_below": st_share,
                "diff_pp": (s_share - st_share) * 100.0
                if np.isfinite(s_share) and np.isfinite(st_share)
                else float("nan"),
            }
        )
    return pd.DataFrame(rows)


def build_regressivity_table(
    annual_fixed_charge_increase: float,
    *,
    top_bin_midpoint: float = 300_000.0,
) -> pd.DataFrame:
    """Express the annual fixed-charge increase as a percentage of the bin midpoint.

    Useful for plotting the regressivity curve: a flat dollar increase is a larger share
    of income for low-income households than for high-income ones.
    """
    from .metrics import bin_midpoint

    rows = []
    for col, label, upper in B19001_BINS:
        # Lower bounds are inferred from the previous bin's upper.
        lower = 0.0
        if rows:
            prev_upper = rows[-1]["upper_bound"]
            lower = prev_upper if (prev_upper is not None and not np.isnan(prev_upper)) else lower
        mid = bin_midpoint(lower, upper, fallback_for_open_top=top_bin_midpoint)
        share = safe_div(annual_fixed_charge_increase, mid)
        rows.append(
            {
                "bin_variable": col,
                "bin_label": label,
                "upper_bound": upper if upper is not None else float("nan"),
                "midpoint_used": mid,
                "annual_increase": annual_fixed_charge_increase,
                "increase_as_share_of_income": share,
                "increase_as_pct_of_income": share * 100.0 if np.isfinite(share) else float("nan"),
            }
        )
    return pd.DataFrame(rows)
