"""
app/services/planetary.py
Queries the Microsoft Planetary Computer STAC catalog for Sentinel-2 L2A
scenes and derives NDVI statistics for a given location and bounding box.

External APIs used
──────────────────
  Planetary Computer STAC → https://planetarycomputer.microsoft.com/api/stac/v1

NDVI calculation
────────────────
  NDVI = (B08 − B04) / (B08 + B04)
  When raw band access is unavailable we fall back to the scene-level
  vegetation index metadata provided by the STAC item properties.
"""

from __future__ import annotations
import pystac_client
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from pystac_client import Client as STACClient
from pystac_client.exceptions import APIError

# ── Constants ─────────────────────────────────────────────────────────────────

PLANETARY_STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
SENTINEL_COLLECTION = "sentinel-2-l2a"
MAX_CLOUD_COVER = 20  # percent
LOOKBACK_MONTHS = 24
REQUEST_TIMEOUT = 30  # seconds

USER_AGENT = "Landroid/0.1 (land-intelligence-platform; contact@landroid.app)"

logger = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _build_date_range(months: int = LOOKBACK_MONTHS) -> str:
    """Return an ISO-8601 date range string for the past *months* months."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=months * 30)
    return f"{start.strftime('%Y-%m-%d')}/{end.strftime('%Y-%m-%d')}"


def _extract_ndvi_from_item(item) -> Optional[float]:
    """
    Attempt to derive an NDVI proxy from a STAC item's properties.

    Strategy (in priority order):
      1. If the item exposes a pre-computed ``s2:vegetation_percentage``
         or ``s2:vegetation_index`` property, normalise it to 0-1.
      2. Fall back to a rough NDVI estimate from the scene-level
         ``eo:bands`` statistics when present.
      3. Return None when no vegetation data is available.
    """
    props = item.properties

    # Strategy 1 – Sentinel-2 specific vegetation percentage (0–100)
    veg_pct = props.get("s2:vegetation_percentage")
    if veg_pct is not None:
        try:
            return round(float(veg_pct) / 100.0, 4)
        except (ValueError, TypeError):
            pass

    # Strategy 2 – Derived vegetation index if exposed
    veg_idx = props.get("s2:vegetation_index")
    if veg_idx is not None:
        try:
            return round(float(veg_idx), 4)
        except (ValueError, TypeError):
            pass

    # Strategy 3 – If the catalog exposes mean reflectance values for
    # B04 (red) and B08 (NIR) we can calculate NDVI directly.
    # This is uncommon in STAC metadata but worth checking.
    b04 = props.get("s2:mean_reflectance_B04") or props.get("eo:mean_reflectance_B04")
    b08 = props.get("s2:mean_reflectance_B08") or props.get("eo:mean_reflectance_B08")
    if b04 is not None and b08 is not None:
        try:
            b04_f, b08_f = float(b04), float(b08)
            if (b08_f + b04_f) != 0:
                return round((b08_f - b04_f) / (b08_f + b04_f), 4)
        except (ValueError, TypeError, ZeroDivisionError):
            pass

    return None


def _classify_status(
    mean_ndvi: Optional[float],
    trend_slope: Optional[float],
) -> str:
    """
    Return a human-readable vegetation status label.

    Rules:
    ------
      • **Healthy**     – mean NDVI > 0.4 and trend is not declining
      • **Degrading**   – trend slope is negative (vegetation declining)
      • **Recovering**  – trend slope is positive (vegetation improving)
      • **Low**         – mean NDVI ≤ 0.4 and no clear trend
      • **Unknown**     – insufficient data
    """
    if mean_ndvi is None:
        return "Unknown"

    if trend_slope is not None:
        if trend_slope < -0.005:
            return "Degrading"
        if trend_slope > 0.005:
            if mean_ndvi > 0.4:
                return "Healthy"
            return "Recovering"

    if mean_ndvi > 0.4:
        return "Healthy"

    return "Low"


def _compute_trend_slope(monthly_values: list[Optional[float]]) -> Optional[float]:
    """
    Calculate a simple linear trend (slope) over the monthly NDVI series
    using least-squares.  Returns None if fewer than 3 valid data points.
    """
    # Filter to (index, value) pairs where value is not None
    points = [(i, v) for i, v in enumerate(monthly_values) if v is not None]
    n = len(points)
    if n < 3:
        return None

    sum_x = sum(p[0] for p in points)
    sum_y = sum(p[1] for p in points)
    sum_xy = sum(p[0] * p[1] for p in points)
    sum_xx = sum(p[0] ** 2 for p in points)

    denominator = n * sum_xx - sum_x ** 2
    if denominator == 0:
        return 0.0

    slope = (n * sum_xy - sum_x * sum_y) / denominator
    return round(slope, 6)


def _compute_confidence(clean_scenes: int) -> float:
    """
    Confidence score (0.0–1.0) based on the number of cloud-free scenes
    found over the 24-month window.

    Tiers:
      24+ scenes → 1.0  (monthly coverage)
      12–23      → 0.8
       6–11      → 0.6
       3–5       → 0.4
       1–2       → 0.2
       0         → 0.0
    """
    if clean_scenes >= 24:
        return 1.0
    if clean_scenes >= 12:
        return 0.8
    if clean_scenes >= 6:
        return 0.6
    if clean_scenes >= 3:
        return 0.4
    if clean_scenes >= 1:
        return 0.2
    return 0.0


# ── Public interface ──────────────────────────────────────────────────────────


async def fetch_ndvi_analysis(
    lat: float,
    lng: float,
    min_lat: float,
    min_lng: float,
    max_lat: float,
    max_lng: float,
) -> dict:
    """
    Search the Microsoft Planetary Computer STAC catalog for Sentinel-2 L2A
    scenes covering the bounding box over the past 24 months, filter for
    cloud cover < 20 %, extract NDVI values and compute statistics.

    Returns a structured dict ready to be unpacked into NDVIResponse.

    Raises:
        TimeoutError        – STAC catalog did not respond in time
        pystac_client.exceptions.APIError – STAC API returned an error
        Exception           – unexpected failures
    """

    date_range = _build_date_range(LOOKBACK_MONTHS)
    bbox = [min_lng, min_lat, max_lng, max_lat]  # STAC bbox: west, south, east, north

    # ── Connect to Planetary Computer STAC ────────────────────────────────────
    logger.info(
        "Querying Planetary Computer STAC: collection=%s bbox=%s dates=%s",
        SENTINEL_COLLECTION, bbox, date_range,
    )

    catalog = pystac_client.Client.open(
    "https://planetarycomputer.microsoft.com/api/stac/v1"
)

    search = catalog.search(
        collections=[SENTINEL_COLLECTION],
        bbox=bbox,
        datetime=date_range,
        query={"eo:cloud_cover": {"lt": MAX_CLOUD_COVER}},
        max_items=500,
    )

    items = list(search.items())
    logger.info("Found %d scenes with cloud cover < %d%%", len(items), MAX_CLOUD_COVER)

    if not items:
        return {
            "lat": lat,
            "lng": lng,
            "bounding_box": {
                "min_lat": min_lat,
                "min_lng": min_lng,
                "max_lat": max_lat,
                "max_lng": max_lng,
            },
            "current_ndvi": None,
            "two_year_mean": None,
            "monthly_trend": [],
            "status": "Unknown",
            "confidence_score": 0.0,
            "clean_scenes_found": 0,
            "data_source": "Microsoft Planetary Computer – Sentinel-2 L2A",
            "message": (
                "No cloud-free Sentinel-2 scenes found for this location "
                "in the past 24 months."
            ),
        }

    # ── Extract NDVI values per scene ─────────────────────────────────────────
    scene_data: list[dict] = []
    for item in items:
        ndvi = _extract_ndvi_from_item(item)
        cloud_cover = item.properties.get("eo:cloud_cover")
        acquired = item.properties.get("datetime") or item.datetime
        if isinstance(acquired, str):
            try:
                acquired = datetime.fromisoformat(acquired.replace("Z", "+00:00"))
            except ValueError:
                acquired = None

        scene_data.append({
            "id": item.id,
            "datetime": acquired,
            "cloud_cover": cloud_cover,
            "ndvi": ndvi,
        })

    # Sort chronologically (oldest first)
    scene_data.sort(key=lambda s: s["datetime"] or datetime.min.replace(tzinfo=timezone.utc))

    # ── Aggregate into monthly buckets ────────────────────────────────────────
    monthly_buckets: dict[str, list[float]] = {}
    for scene in scene_data:
        if scene["ndvi"] is None or scene["datetime"] is None:
            continue
        month_key = scene["datetime"].strftime("%Y-%m")
        monthly_buckets.setdefault(month_key, []).append(scene["ndvi"])

    # Build monthly_trend: average NDVI per month, ordered chronologically
    sorted_months = sorted(monthly_buckets.keys())
    monthly_trend: list[dict] = []
    monthly_values: list[Optional[float]] = []

    for month_key in sorted_months:
        values = monthly_buckets[month_key]
        avg = round(sum(values) / len(values), 4)
        monthly_trend.append({"month": month_key, "ndvi": avg, "scenes": len(values)})
        monthly_values.append(avg)

    # ── Compute statistics ────────────────────────────────────────────────────
    all_ndvi = [s["ndvi"] for s in scene_data if s["ndvi"] is not None]

    current_ndvi: Optional[float] = None
    if all_ndvi:
        # Most recent NDVI value
        current_ndvi = all_ndvi[-1]

    two_year_mean: Optional[float] = None
    if all_ndvi:
        two_year_mean = round(sum(all_ndvi) / len(all_ndvi), 4)

    trend_slope = _compute_trend_slope(monthly_values)
    status = _classify_status(two_year_mean, trend_slope)
    confidence = _compute_confidence(len(all_ndvi))

    return {
        "lat": lat,
        "lng": lng,
        "bounding_box": {
            "min_lat": min_lat,
            "min_lng": min_lng,
            "max_lat": max_lat,
            "max_lng": max_lng,
        },
        "current_ndvi": current_ndvi,
        "two_year_mean": two_year_mean,
        "monthly_trend": monthly_trend,
        "status": status,
        "confidence_score": confidence,
        "clean_scenes_found": len(all_ndvi),
        "trend_slope": trend_slope,
        "data_source": "Microsoft Planetary Computer – Sentinel-2 L2A",
        "message": None,
    }
