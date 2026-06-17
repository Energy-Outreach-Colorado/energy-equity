"""Guards the packaged LIHEAP SMI CSV against transcription/edit errors.

Every 60% SMI value must equal the CFR 96.85 derivation from that geography's implied
4-person 60% SMI (the size-4 row): floor(base60 * pct(size)). This catches a bad cell the
moment the file is hand-edited.
"""

from __future__ import annotations

import math

from energy_equity.thresholds.smi import load_packaged_smi_table

PCT = {
    1: 0.52,
    2: 0.68,
    3: 0.84,
    4: 1.00,
    5: 1.16,
    6: 1.32,
    7: 1.35,
    8: 1.38,
    9: 1.41,
    10: 1.44,
    11: 1.47,
    12: 1.50,
}


def test_every_row_matches_cfr_96_85_formula() -> None:
    df = load_packaged_smi_table()
    failures: list[str] = []
    for (fips, fy), g in df.groupby(["state_fips", "fy"]):
        sizes = g.set_index("hh_size")["smi60_annual"].astype(int).to_dict()
        assert set(sizes) == set(PCT), f"{fips} fy{fy}: sizes {sorted(sizes)} != 1..12"
        base60 = sizes[4]  # 60% SMI for a 4-person household (pct = 1.00)
        for size, pct in PCT.items():
            expected = math.floor(base60 * pct)
            if sizes[size] != expected:
                failures.append(f"{fips} fy{fy} size{size}: {sizes[size]} != {expected}")
    assert not failures, "SMI rows inconsistent with CFR 96.85:\n  " + "\n  ".join(failures[:20])


def test_table_shape() -> None:
    df = load_packaged_smi_table()
    # 52 geographies x 3 fiscal years (FY2025/2026/2027) x 12 sizes.
    assert len(df) == 52 * 3 * 12
    assert sorted(int(y) for y in df["fy"].unique()) == [2025, 2026, 2027]
