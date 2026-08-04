"""Build the packaged EIA-176 residential gas CSV from an NGQS RP4 JSON export.

Usage:
    curl -sL "https://www.eia.gov/naturalgas/ngqs/data/report/RP4/data/2022/2024/ICA/All" -o rp4.json
    uv run python scripts/build_eia176_residential_csv.py rp4.json

The EIA Natural Gas Query System (NGQS, https://www.eia.gov/naturalgas/ngqs/) serves
EIA Form 176 company-level annual data as JSON. Report RP4 returns one row per
company, state, and year with a `columns` descriptor naming every field; this script
resolves the residential SALES fields from that descriptor rather than hardcoding
field letters.

Sales versus transport: residential transport customers buy their gas from a third
party, so the transported revenue a distributor reports excludes the commodity cost.
Average-bill calibration therefore uses sales revenue divided by sales customers, and
transport-only customers are excluded. This mirrors the bundled-versus-delivery
treatment in the EIA-861 electric data.

The output overwrites src/energy_equity/data/eia176/eia176_residential.csv; update
provenance.csv alongside it. tests/unit/test_eia176_data_integrity.py must still pass.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

OUTPUT_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "energy_equity"
    / "data"
    / "eia176"
    / "eia176_residential.csv"
)

OWNERSHIP_HEADERS = (
    "Investor Owned",
    "Municipally Owned",
    "Privately Owned",
    "Cooperative",
    "Other Ownership",
)


def _field_map(columns: list[dict]) -> dict[str, str]:
    """Header name -> field letter, flattened across column groups."""
    out: dict[str, str] = {}
    for col in columns:
        for child in col.get("children", [col]):
            header = str(child.get("headerName", "")).replace("<BR>", " ").strip()
            field = child.get("field")
            if header and field:
                out[header] = field
    return out


def extract_residential(rp4_json_path: str | Path) -> pd.DataFrame:
    raw = json.loads(Path(rp4_json_path).read_text(encoding="utf-8"))
    fields = _field_map(raw["columns"])
    revenue_f = fields["Residential Sales Revenue (Dollars)"]
    customers_f = fields["Residential Sales Customers"]
    volume_f = fields["Residential Sales Volume (Mcf)"]
    year_f = fields["Year"]
    state_f = fields["State"]
    company_f = fields["Company"]
    name_f = fields["Company Name"]

    rows = []
    for r in raw["data"]:
        state = str(r.get(state_f, "")).strip()
        name = str(r.get(name_f, "")).strip()
        if len(state) != 2 or "TOTAL OF ALL" in name.upper():
            continue
        customers = r.get(customers_f) or 0
        revenue = r.get(revenue_f) or 0
        if customers <= 0:
            continue
        company_code = str(r.get(company_f, "")).strip()
        company_id = company_code.removesuffix(state)
        ownership = next(
            (h.removesuffix(" Ownership") for h in OWNERSHIP_HEADERS if r.get(fields[h]) == "X"),
            "Unknown",
        )
        rows.append(
            {
                "company_id": int(company_id),
                "company_name": name,
                "state": state,
                "customer_class": "residential",
                "revenue_thousand_dollars": round(float(revenue) / 1000.0, 1),
                "volume_mcf": float(r.get(volume_f) or 0),
                "customers": int(customers),
                "ownership": ownership,
                "year": int(r.get(year_f)),
            }
        )
    out = pd.DataFrame(rows).sort_values(["year", "state", "company_id"]).reset_index(drop=True)
    dupes = out.duplicated(subset=["year", "company_id", "state"]).sum()
    if dupes:
        raise SystemExit(f"{dupes} duplicate (year, company_id, state) rows; aborting.")
    return out


def main(paths: list[str]) -> None:
    if len(paths) != 1:
        raise SystemExit("Pass the path to one downloaded NGQS RP4 JSON file.")
    combined = extract_residential(paths[0])
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(OUTPUT_PATH, index=False)
    summary = combined.groupby("year").agg(
        rows=("company_id", "size"),
        customers=("customers", "sum"),
        revenue_thousand_dollars=("revenue_thousand_dollars", "sum"),
    )
    summary["avg_annual_bill"] = (
        summary["revenue_thousand_dollars"] * 1000 / summary["customers"]
    ).round(2)
    print(f"wrote {OUTPUT_PATH}")
    print(summary.to_string())


if __name__ == "__main__":
    main(sys.argv[1:])
