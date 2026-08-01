"""Unit tests for energy_equity.tables.affordability_gap."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from energy_equity.tables.affordability_gap import (
    build_affordability_gap_by_puma,
    build_affordability_gap_summary,
    compute_affordability_gap,
)


class TestComputeAffordabilityGap:
    def test_gap_math(self) -> None:
        df = pd.DataFrame(
            {
                "annual_energy_cost_adj": [3000.0, 1200.0, 6000.0],
                "income_adjusted": [20000.0, 20000.0, 100000.0],
            }
        )
        gap = compute_affordability_gap(df, threshold=0.06)
        assert gap.iloc[0] == pytest.approx(3000.0 - 0.06 * 20000.0)
        assert gap.iloc[1] == pytest.approx(0.0)
        assert gap.iloc[2] == pytest.approx(0.0)

    def test_at_threshold_is_zero(self) -> None:
        df = pd.DataFrame({"annual_energy_cost_adj": [1200.0], "income_adjusted": [20000.0]})
        gap = compute_affordability_gap(df, threshold=0.06)
        assert gap.iloc[0] == pytest.approx(0.0)

    def test_undefined_cases(self) -> None:
        df = pd.DataFrame(
            {
                "annual_energy_cost_adj": [3000.0, 3000.0, np.nan],
                "income_adjusted": [0.0, -5000.0, 20000.0],
            }
        )
        gap = compute_affordability_gap(df, threshold=0.06)
        assert gap.isna().all()

    def test_higher_threshold_never_increases_gap(self) -> None:
        rng = np.random.default_rng(3)
        df = pd.DataFrame(
            {
                "annual_energy_cost_adj": rng.uniform(500, 6000, 50),
                "income_adjusted": rng.uniform(5000, 150000, 50),
            }
        )
        low = compute_affordability_gap(df, threshold=0.06)
        high = compute_affordability_gap(df, threshold=0.10)
        assert (high <= low + 1e-9).all()


def summary_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "PUMA": ["00800", "00800", "00900", "00900"],
            "WGTP": [10.0, 20.0, 30.0, 40.0],
            "w_service": [5.0, 10.0, 15.0, 20.0],
            "w_service_urban": [np.nan, np.nan, np.nan, np.nan],
            "w_service_rural": [np.nan, np.nan, np.nan, np.nan],
            "annual_energy_cost_adj": [3200.0, 1000.0, 5000.0, 2400.0],
            "income_adjusted": [20000.0, 50000.0, 40000.0, 0.0],
            "is_low_income": [True, False, True, True],
            "le_60_smi": [False, False, False, False],
            "TEN": [3, 1, 1, 3],
        }
    )


class TestBuildAffordabilityGapSummary:
    def test_hand_computed_totals(self) -> None:
        out = build_affordability_gap_summary(summary_frame(), thresholds=[0.06])
        row = out[(out["segment"] == "all") & (out["population"] == "All households")].iloc[0]
        assert row["gap_valid_households"] == pytest.approx(30.0)
        assert row["households_in_gap"] == pytest.approx(20.0)
        gap_hh1 = 3200.0 - 0.06 * 20000.0
        gap_hh3 = 5000.0 - 0.06 * 40000.0
        expected_total = 5.0 * gap_hh1 + 15.0 * gap_hh3
        assert row["total_gap_dollars"] == pytest.approx(expected_total)
        assert row["mean_gap_per_household_in_gap"] == pytest.approx(expected_total / 20.0)
        assert row["households_in_gap_rate"] == pytest.approx(20.0 / 30.0)

    def test_population_restriction(self) -> None:
        out = build_affordability_gap_summary(summary_frame(), thresholds=[0.06])
        low = out[(out["segment"] == "all") & (out["population"] == "<=80% AMI")].iloc[0]
        gap_hh1 = 3200.0 - 0.06 * 20000.0
        gap_hh3 = 5000.0 - 0.06 * 40000.0
        assert low["gap_valid_households"] == pytest.approx(20.0)
        assert low["total_gap_dollars"] == pytest.approx(5.0 * gap_hh1 + 15.0 * gap_hh3)
        renters = out[(out["segment"] == "all") & (out["population"] == "<=80% AMI renters")].iloc[
            0
        ]
        assert renters["total_gap_dollars"] == pytest.approx(5.0 * gap_hh1)

    def test_moe_nan_without_replicates(self) -> None:
        out = build_affordability_gap_summary(summary_frame(), thresholds=[0.06])
        assert out["total_gap_dollars_moe90"].isna().all()
        assert out["households_in_gap_moe90"].isna().all()

    def test_moe_hand_computed(self) -> None:
        df = summary_frame()
        df["WGTP1"] = df["WGTP"] * 1.1
        df["WGTP2"] = df["WGTP"] * 0.9
        out = build_affordability_gap_summary(
            df, thresholds=[0.06], replicate_weight_cols=["WGTP1", "WGTP2"]
        )
        row = out[(out["segment"] == "all") & (out["population"] == "All households")].iloc[0]
        point = row["total_gap_dollars"]
        rep_estimates = np.array([point * 1.1, point * 0.9])
        factor = 4.0 / 2
        expected_moe = 1.6448536269514722 * np.sqrt(
            factor * float(((rep_estimates - point) ** 2).sum())
        )
        assert row["total_gap_dollars_moe90"] == pytest.approx(expected_moe)

    def test_multiple_thresholds_ordering(self) -> None:
        out = build_affordability_gap_summary(summary_frame(), thresholds=[0.06, 0.10])
        all_rows = out[(out["segment"] == "all") & (out["population"] == "All households")]
        by_t = all_rows.set_index("threshold")["total_gap_dollars"]
        assert by_t[0.10] <= by_t[0.06]


class TestBuildAffordabilityGapByPuma:
    def test_per_puma_totals(self) -> None:
        out = build_affordability_gap_by_puma(summary_frame(), thresholds=[0.06])
        out = out.set_index("PUMA")
        gap_hh1 = 3200.0 - 0.06 * 20000.0
        gap_hh3 = 5000.0 - 0.06 * 40000.0
        assert out.loc["00800", "total_gap_dollars"] == pytest.approx(5.0 * gap_hh1)
        assert out.loc["00900", "total_gap_dollars"] == pytest.approx(15.0 * gap_hh3)
        assert out.loc["00900", "gap_valid_households"] == pytest.approx(15.0)
        assert out.loc["00800", "households_in_gap"] == pytest.approx(5.0)

    def test_puma_totals_match_summary(self) -> None:
        df = summary_frame()
        by_puma = build_affordability_gap_by_puma(df, thresholds=[0.06])
        summary = build_affordability_gap_summary(df, thresholds=[0.06])
        all_row = summary[
            (summary["segment"] == "all") & (summary["population"] == "All households")
        ].iloc[0]
        assert by_puma["total_gap_dollars"].sum() == pytest.approx(all_row["total_gap_dollars"])
        assert by_puma["households_in_gap"].sum() == pytest.approx(all_row["households_in_gap"])
