"""
app/services/temperature.py
Queries the Microsoft Planetary Computer TerraClimate Zarr store for
monthly maximum temperature data (`tmax` variable) and derives temperature
statistics for a given location.

External APIs used
──────────────────
  Planetary Computer STAC → https://planetarycomputer.microsoft.com/api/stac/v1
  TerraClimate Zarr store  → signed at request time via `planetary_computer`

TerraClimate
────────────
  Monthly gridded climate dataset at ~4 km resolution.
  Variable `tmax` = maximum temperature in °C (stored as °C × 10,
  i.e. tenths of a degree — divide by 10 to get °C).
  Time range: 1958–2021.
  Historical window used: 2020-01 → 2021-12 (24 months).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional
from urllib.parse import urlparse

import adlfs
import numpy as np
import planetary_computer
import pystac_client
import xarray as xr

# ── Constants ─────────────────────────────────────────────────────────────────

PLANETARY_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
TERRACLIMATE_COLLECTION = "terraclimate"
TIME_SLICE = slice("2020-01", "2021-12")  # 24 months of historical data
HEAT_STRESS_THRESHOLD_C = 35.0  # °C
TMAX_SCALE_FACTOR = 0.1  # TerraClimate stores tmax as °C × 10

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


# ── Public interface ──────────────────────────────────────────────────────────


async def fetch_temperature_analysis(
    lat: float,
    lng: float,
    min_lat: float,
    min_lng: float,
    max_lat: float,
    max_lng: float,
) -> dict:
    """
    Open the TerraClimate Zarr store, extract the `tmax` (maximum temperature)
    time-series for the nearest grid cell to (lat, lng) over the
    2020-01 → 2021-12 window, and compute temperature statistics.

    The bounding-box parameters (min_lat, … max_lng) are accepted for
    API compatibility but the point query uses the centroid for TerraClimate's
    ~4 km grid — no spatial averaging is done.

    Returns a structured dict matching TemperatureResponse fields.

    Raises:
        Exception – upstream failures (network, data access, etc.)
    """
    logger.info(
        "Fetching TerraClimate tmax for lat=%s lng=%s",
        lat, lng,
    )

    # ── Open dataset & extract time-series ────────────────────────────────────
    ds = _open_terraclimate_dataset()
    tmax_raw = ds["tmax"].sel(time=TIME_SLICE).sel(lat=lat, lon=lng, method="nearest")
    raw_values = tmax_raw.values  # numpy array (stored as °C × 10)

    logger.info("Retrieved %d monthly tmax values", len(raw_values))

    if len(raw_values) == 0:
        return {
            "lat": lat,
            "lng": lng,
            "monthly_trend": [],
            "heat_stress_event_count": 0,
            "confidence_score": 0.0,
            "precision_label": "regional-level (~4 km grid)",
            "data_source": "Microsoft Planetary Computer – TerraClimate (tmax)",
            "message": (
                "No TerraClimate temperature data found for this location "
                "in the 2020–2021 window."
            ),
        }

    # ── Convert to °C and build monthly trend ─────────────────────────────────
    times = tmax_raw.time.values  # numpy datetime64 array
    monthly_trend: list[dict] = []
    heat_stress_count = 0
    valid_count = 0

    for t, v in zip(times, raw_values):
        month_key = str(t)[:7]  # "YYYY-MM"
        temp_c_raw = float(v)

        # TerraClimate stores tmax as tenths of °C → divide by 10
        # But some versions store in plain °C.  Use the scale factor
        # only if the value looks like it's in tenths (> 100 would be
        # unlikely in °C but common in tenths).
        if abs(temp_c_raw) > 100:
            temp_c = round(temp_c_raw * TMAX_SCALE_FACTOR, 2)
        else:
            temp_c = round(temp_c_raw, 2)

        # Skip NaN / fill values
        if np.isnan(temp_c):
            continue

        valid_count += 1
        monthly_trend.append({"month": month_key, "mean_temp_c": temp_c})

        if temp_c >= HEAT_STRESS_THRESHOLD_C:
            heat_stress_count += 1

    if valid_count == 0:
        return {
            "lat": lat,
            "lng": lng,
            "monthly_trend": [],
            "heat_stress_event_count": 0,
            "confidence_score": _compute_confidence(0),
            "precision_label": "regional-level (~4 km grid)",
            "data_source": "Microsoft Planetary Computer – TerraClimate (tmax)",
            "message": (
                "TerraClimate data found but all temperature values were NaN "
                "for this location."
            ),
        }

    confidence = _compute_confidence(valid_count)

    return {
        "lat": lat,
        "lng": lng,
        "monthly_trend": monthly_trend,
        "heat_stress_event_count": heat_stress_count,
        "confidence_score": confidence,
        "precision_label": "regional-level (~4 km grid)",
        "data_source": "Microsoft Planetary Computer – TerraClimate (tmax)",
        "message": None,
    }
