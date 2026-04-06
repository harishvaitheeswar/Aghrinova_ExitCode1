"""
app/services/rainfall.py
Queries the Microsoft Planetary Computer TerraClimate Zarr store for
monthly precipitation data (`ppt` variable) and derives rainfall statistics
for a given location.

External APIs used
──────────────────
  Planetary Computer STAC → https://planetarycomputer.microsoft.com/api/stac/v1
  TerraClimate Zarr store  → signed at request time via `planetary_computer`

TerraClimate
────────────
  Monthly gridded climate dataset at ~4 km resolution.
  Variable `ppt` = total precipitation in mm/month.
  Time range: 1958–2021.
  Historical window used: 2020-01 → 2021-12 (24 months).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional
from urllib.parse import urlparse

import adlfs
import planetary_computer
import pystac_client
import xarray as xr

# ── Constants ─────────────────────────────────────────────────────────────────

PLANETARY_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
TERRACLIMATE_COLLECTION = "terraclimate"
TIME_SLICE = slice("2020-01", "2021-12")  # 24 months of historical data
HEAT_STRESS_THRESHOLD_C = 35.0  # not used here, but kept for consistency

logger = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _open_terraclimate_dataset() -> xr.Dataset:
    """
    Fetch a fresh signed URL for TerraClimate Zarr from Planetary Computer
    and return an xarray Dataset.

    A new SAS token is obtained on every call so it never expires
    mid-request for long-running servers.
    """
    catalog = pystac_client.Client.open(
        PLANETARY_STAC_URL,
        modifier=planetary_computer.sign_inplace,
    )
    collection = catalog.get_collection(TERRACLIMATE_COLLECTION)
    url = collection.assets["zarr-https"].href

    parsed = urlparse(url)
    account_name = parsed.netloc.split(".")[0]
    sas_token = parsed.query
    container, *path_parts = parsed.path.lstrip("/").split("/", 1)
    blob_path = path_parts[0] if path_parts else ""

    fs = adlfs.AzureBlobFileSystem(account_name=account_name, sas_token=sas_token)
    store = fs.get_mapper(f"{container}/{blob_path}")
    return xr.open_zarr(store, consolidated=True)


def _compute_confidence(month_count: int) -> float:
    """
    Confidence score (0.0–1.0) based on the number of valid months found.

    Tiers:
      24+ months → 1.0  (full 2-year coverage)
      18–23      → 0.8
      12–17      → 0.6
       6–11      → 0.4
       1–5       → 0.2
       0         → 0.0
    """
    if month_count >= 24:
        return 1.0
    if month_count >= 18:
        return 0.8
    if month_count >= 12:
        return 0.6
    if month_count >= 6:
        return 0.4
    if month_count >= 1:
        return 0.2
    return 0.0


def _classify_surplus_deficit(deviation_pct: float) -> str:
    """
    Classify based on deviation from the 2-year mean.
      • surplus if > +10%
      • deficit if < −10%
      • normal  otherwise
    """
    if deviation_pct > 10.0:
        return "surplus"
    if deviation_pct < -10.0:
        return "deficit"
    return "normal"


# ── Public interface ──────────────────────────────────────────────────────────


async def fetch_rainfall_analysis(lat: float, lng: float) -> dict:
    """
    Open the TerraClimate Zarr store, extract the `ppt` (precipitation)
    time-series for the nearest grid cell to (lat, lng) over the
    2020-01 → 2021-12 window, and compute rainfall statistics.

    Returns a structured dict matching RainfallResponse fields.

    Raises:
        Exception – upstream failures (network, data access, etc.)
    """
    logger.info(
        "Fetching TerraClimate precipitation for lat=%s lng=%s",
        lat, lng,
    )

    # ── Open dataset & extract time-series ────────────────────────────────────
    ds = _open_terraclimate_dataset()
    ppt = ds["ppt"].sel(time=TIME_SLICE).sel(lat=lat, lon=lng, method="nearest")
    values = ppt.values  # numpy array of monthly mm values

    logger.info("Retrieved %d monthly precipitation values", len(values))

    if len(values) == 0:
        return {
            "lat": lat,
            "lng": lng,
            "annual_mm": None,
            "monthly_distribution": [],
            "deviation_from_normal": None,
            "surplus_or_deficit": None,
            "confidence_score": 0.0,
            "data_source": "Microsoft Planetary Computer – TerraClimate (ppt)",
            "message": (
                "No TerraClimate precipitation data found for this location "
                "in the 2020–2021 window."
            ),
        }

    # ── Build monthly records ─────────────────────────────────────────────────
    times = ppt.time.values  # numpy datetime64 array
    monthly_records: list[dict] = []
    for t, v in zip(times, values):
        ts = str(t)[:7]  # "YYYY-MM"
        val = float(v)
        monthly_records.append({"month_key": ts, "precip_mm": val})

    valid_count = len(monthly_records)

    # ── Aggregate into calendar-month averages (Jan–Dec) ──────────────────────
    calendar_month_totals: dict[int, list[float]] = defaultdict(list)
    yearly_buckets: dict[int, float] = defaultdict(float)

    for rec in monthly_records:
        cal_month = int(rec["month_key"].split("-")[1])
        year = int(rec["month_key"].split("-")[0])
        calendar_month_totals[cal_month].append(rec["precip_mm"])
        yearly_buckets[year] += rec["precip_mm"]

    monthly_distribution: list[float] = []
    for m in range(1, 13):
        if calendar_month_totals[m]:
            avg = round(
                sum(calendar_month_totals[m]) / len(calendar_month_totals[m]), 2
            )
        else:
            avg = 0.0
        monthly_distribution.append(avg)

    # ── Annual rainfall: average of per-year totals ───────────────────────────
    annual_totals = list(yearly_buckets.values())
    annual_mm = (
        round(sum(annual_totals) / len(annual_totals), 2) if annual_totals else None
    )

    # ── Deviation from the 2-year mean ────────────────────────────────────────
    deviation_from_normal: Optional[float] = None
    surplus_or_deficit: Optional[str] = None

    if annual_mm is not None and len(annual_totals) >= 2:
        two_year_mean = sum(annual_totals) / len(annual_totals)
        most_recent_year = max(yearly_buckets.keys())
        recent_total = yearly_buckets[most_recent_year]

        if two_year_mean > 0:
            deviation_from_normal = round(
                ((recent_total - two_year_mean) / two_year_mean) * 100, 2
            )
            surplus_or_deficit = _classify_surplus_deficit(deviation_from_normal)
    elif annual_mm is not None and len(annual_totals) == 1:
        deviation_from_normal = 0.0
        surplus_or_deficit = "normal"

    confidence = _compute_confidence(valid_count)

    return {
        "lat": lat,
        "lng": lng,
        "annual_mm": annual_mm,
        "monthly_distribution": monthly_distribution,
        "deviation_from_normal": deviation_from_normal,
        "surplus_or_deficit": surplus_or_deficit,
        "confidence_score": confidence,
        "data_source": "Microsoft Planetary Computer – TerraClimate (ppt)",
        "message": None,
    }
