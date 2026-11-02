"""``platform.emt_charges`` — one current MiFID charge per ISIN.

The source is not an SFTP drop. It is SEI's EMT Cost and Charge Effective Dates
report, cleaned to ``data/emt_charges.csv`` by ``scripts/build_emt_charges.py``.
The digest below is inside this function because interlace fingerprints a Python
model from the function source alone: a new extract that left the literal
unchanged would never rebuild.
"""

from __future__ import annotations

import os
from pathlib import Path

import pyarrow as pa
from interlace import model

from sei_sync.emt import publish

_FILE = Path(__file__).resolve().parent.parent / "data" / "emt_charges.csv"


@model(
    name="platform.emt_charges",
    materialise="virtual",
    strategy="replace",
    key=("isin",),
    tags=("sei", "entity", "conformed"),
    description="One current EMT cost and charge per ISIN, from the latest file-creation date",
    checks=[{"not_null": "isin"}, {"unique": "isin"}],
)
def platform_emt_charges() -> pa.Table:
    # Rewritten by scripts/build_emt_charges.py. Do not edit the hex by hand.
    digest = "97489efa8c9e577f2f1d0e910afd3f15d1ef9b057eff8383df92d4ab74c384af"
    override = os.environ.get("SEI_EMT_CHARGES_CSV")
    if override:
        return publish(Path(override), expected=None)
    return publish(_FILE, expected=digest)
