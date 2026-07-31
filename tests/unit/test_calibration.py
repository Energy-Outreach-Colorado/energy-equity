"""Unit tests for energy_equity.calibration (EIA-861 electric bill normalization)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from energy_equity.calibration import (
    apply_electric_calibration,
    compute_electric_calibration,
    load_eia861_average_bill,
)
from energy_equity.pums.energy_cost import (
    apply_energy_cost_adjustment,
    compute_energy_burden_pums,
)


def write_eia861_csv(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "eia861.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def eia861_row(**overrides) -> dict:
    row = {
        "utility_number": 15466,
        "utility_name": "Public Service Co of Colorado",
        "state": "CO",
        "customer_class": "residential",
        "revenue_thousand_dollars": 1_500_000.0,
        "customers": 1_200_000,
        "sales_mwh": 10_000_000.0,
        "year": 2023,
    }
    row.update(overrides)
    return row


class TestLoadEia861AverageBill:
    def test_average_bill_math(self, tmp_path: Path) -> None:
        path = write_eia861_csv(tmp_path, [eia861_row()])
        result = load_eia861_average_bill(path, utility_number=15466)
        assert result["target_annual_bill"] == pytest.approx(1_500_000.0 * 1000 / 1_200_000)
        assert result["utility_name"] == "Public Service Co of Colorado"
        assert result["year"] == 2023

    def test_avg_rate_from_sales(self, tmp_path: Path) -> None:
        path = write_eia861_csv(tmp_path, [eia861_row()])
        result = load_eia861_average_bill(path, utility_number=15466)
        assert result["avg_rate_per_kwh"] == pytest.approx(
            1_500_000.0 * 1000 / (10_000_000.0 * 1000)
        )

    def test_missing_columns_rejected(self, tmp_path: Path) -> None:
        row = eia861_row()
        row.pop("customers")
        path = write_eia861_csv(tmp_path, [row])
        with pytest.raises(ValueError, match="customers"):
            load_eia861_average_bill(path, utility_number=15466)

    def test_selection_required(self, tmp_path: Path) -> None:
        path = write_eia861_csv(tmp_path, [eia861_row()])
        with pytest.raises(ValueError, match="utility_number"):
            load_eia861_average_bill(path)

    def test_no_match(self, tmp_path: Path) -> None:
        path = write_eia861_csv(tmp_path, [eia861_row()])
        with pytest.raises(ValueError, match="No residential"):
            load_eia861_average_bill(path, utility_number=99999)

    def test_non_residential_rows_ignored(self, tmp_path: Path) -> None:
        path = write_eia861_csv(
            tmp_path,
            [eia861_row(), eia861_row(customer_class="commercial", customers=50_000)],
        )
        result = load_eia861_average_bill(path, utility_number=15466)
        assert result["customers"] == pytest.approx(1_200_000)

    def test_ambiguous_match_rejected(self, tmp_path: Path) -> None:
        path = write_eia861_csv(tmp_path, [eia861_row(year=2022), eia861_row(year=2023)])
        with pytest.raises(ValueError, match="narrow the selection"):
            load_eia861_average_bill(path, utility_number=15466)

    def test_year_filter(self, tmp_path: Path) -> None:
        path = write_eia861_csv(
            tmp_path,
            [eia861_row(year=2022, customers=1_000_000), eia861_row(year=2023)],
        )
        result = load_eia861_average_bill(path, utility_number=15466, year=2022)
        assert result["customers"] == pytest.approx(1_000_000)

    def test_year_requested_but_absent(self, tmp_path: Path) -> None:
        row = eia861_row()
        row.pop("year")
        path = write_eia861_csv(tmp_path, [row])
        with pytest.raises(ValueError, match="no 'year' column"):
            load_eia861_average_bill(path, utility_number=15466, year=2023)

    def test_utility_name_case_insensitive(self, tmp_path: Path) -> None:
        path = write_eia861_csv(tmp_path, [eia861_row()])
        result = load_eia861_average_bill(path, utility_name="public service co of colorado")
        assert result["utility_number"] == 15466

    def test_state_filter(self, tmp_path: Path) -> None:
        path = write_eia861_csv(
            tmp_path,
            [eia861_row(state="CO"), eia861_row(state="TX", customers=400_000)],
        )
        result = load_eia861_average_bill(path, utility_number=15466, state="tx")
        assert result["customers"] == pytest.approx(400_000)

    def test_packaged_table_default(self) -> None:
        result = load_eia861_average_bill(utility_number=15466, state="CO", year=2024)
        assert result["utility_name"] == "Public Service Co of Colorado"
        assert 900 <= result["target_annual_bill"] <= 1400
        assert 0.05 <= result["avg_rate_per_kwh"] <= 0.30


def household_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "PUMA": ["00800", "00800", "00900", "00900"],
            "WGTP": [10.0, 10.0, 20.0, 20.0],
            "ELEP": [100.0, np.nan, 200.0, np.nan],
            "annual_electric_cost_adj": [1200.0, 0.0, 2400.0, 0.0],
            "annual_gas_cost_adj": [600.0, 0.0, 0.0, 0.0],
            "annual_other_fuel_cost_adj": [0.0, 0.0, 300.0, 0.0],
            "annual_energy_cost_adj": [1800.0, 0.0, 2700.0, 0.0],
        }
    )


class TestComputeElectricCalibration:
    def test_excludes_non_payers(self) -> None:
        diag = compute_electric_calibration(household_frame(), target_annual_bill=1000.0)
        expected = (1200.0 * 10 + 2400.0 * 20) / 30
        assert diag["observed_avg_annual_bill"] == pytest.approx(expected)
        assert diag["n_payers_unweighted"] == 2
        assert diag["payer_weighted_households"] == pytest.approx(30.0)
        assert diag["scope"] == "state"
        assert diag["factor"] == pytest.approx(1000.0 / expected)

    def test_service_share_weighting(self) -> None:
        shares = pd.DataFrame(
            {"PUMA": ["00800", "00900"], "share_households_in_service": [1.0, 0.5]}
        )
        diag = compute_electric_calibration(
            household_frame(), target_annual_bill=1000.0, service_shares=shares
        )
        expected = (1200.0 * 10 * 1.0 + 2400.0 * 20 * 0.5) / (10 * 1.0 + 20 * 0.5)
        assert diag["observed_avg_annual_bill"] == pytest.approx(expected)
        assert diag["scope"] == "service_area"

    def test_puma_outside_service_gets_zero_weight(self) -> None:
        shares = pd.DataFrame({"PUMA": ["00800"], "share_households_in_service": [1.0]})
        diag = compute_electric_calibration(
            household_frame(), target_annual_bill=1000.0, service_shares=shares
        )
        assert diag["observed_avg_annual_bill"] == pytest.approx(1200.0)
        assert diag["n_payers_unweighted"] == 1

    def test_no_payers_raises(self) -> None:
        df = household_frame()
        df["ELEP"] = np.nan
        with pytest.raises(ValueError, match="no households"):
            compute_electric_calibration(df, target_annual_bill=1000.0)

    def test_does_not_modify_frame(self) -> None:
        df = household_frame()
        before = df.copy()
        compute_electric_calibration(df, target_annual_bill=1000.0)
        pd.testing.assert_frame_equal(df, before)


class TestApplyElectricCalibration:
    def test_scales_payers_and_resums_totals(self) -> None:
        df = household_frame()
        apply_electric_calibration(df, factor=0.5)
        assert df.loc[0, "annual_electric_cost_adj"] == pytest.approx(600.0)
        assert df.loc[0, "annual_energy_cost_adj"] == pytest.approx(1200.0)
        assert df.loc[2, "annual_electric_cost_adj"] == pytest.approx(1200.0)
        assert df.loc[2, "annual_energy_cost_adj"] == pytest.approx(1500.0)

    def test_non_payers_untouched(self) -> None:
        df = household_frame()
        apply_electric_calibration(df, factor=0.5)
        assert df.loc[1, "annual_electric_cost_adj"] == pytest.approx(0.0)
        assert df.loc[1, "annual_energy_cost_adj"] == pytest.approx(0.0)

    def test_nan_rule_totals_survive(self) -> None:
        df = pd.DataFrame(
            {
                "PUMA": ["00800", "00800"],
                "WGTP": [10.0, 10.0],
                "ELEP": [100.0, np.nan],
                "GASP": [np.nan, np.nan],
                "FULP": [np.nan, np.nan],
                "adjhsg_factor": [1.0, 1.0],
            }
        )
        apply_energy_cost_adjustment(df, missing_cost_rule="nan")
        assert np.isnan(df.loc[1, "annual_energy_cost_adj"])
        apply_electric_calibration(df, factor=2.0)
        assert df.loc[0, "annual_electric_cost_adj"] == pytest.approx(2400.0)
        assert df.loc[0, "annual_energy_cost_adj"] == pytest.approx(2400.0)
        assert np.isnan(df.loc[1, "annual_energy_cost_adj"])

    def test_calibration_moves_households_across_burden_threshold(self) -> None:
        df = pd.DataFrame(
            {
                "PUMA": ["00800"],
                "WGTP": [10.0],
                "HINCP": [120_000.0],
                "ADJINC": [1_000_000],
                "ADJHSG": [1_000_000],
                "ELEP": [550.0],
                "GASP": [0.0],
                "FULP": [0.0],
                "income_adjusted": [120_000.0],
                "adjhsg_factor": [1.0],
            }
        )
        apply_energy_cost_adjustment(df, missing_cost_rule="zero")
        compute_energy_burden_pums(df)
        assert not df.loc[0, "energy_burdened"]

        apply_electric_calibration(df, factor=1.2)
        compute_energy_burden_pums(df)
        assert df.loc[0, "energy_burden"] == pytest.approx(0.066)
        assert df.loc[0, "energy_burdened"]
