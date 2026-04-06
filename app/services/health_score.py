"""
app/services/health_score.py
Orchestrates all five data services in parallel, normalises each signal
into a 0–100 score, and computes the composite Land Health Score.

Weights
───────
  NDVI trend            40 %
  Rainfall adequacy     30 %
  Soil quality          20 %
  Temperature suitability 10 %

Score → Status
──────────────
  75–100  → Healthy
  50–74   → Moderate
   0–49   → At Risk

Partial-failure strategy
────────────────────────
  If any individual service fails, that signal gets score=0 and
  confidence=0 rather than crashing the entire endpoint.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from app.services.planetary import fetch_ndvi_analysis
from app.services.rainfall import fetch_rainfall_analysis
from app.services.soilgrids import fetch_soil_data
from app.services.temperature import fetch_temperature_analysis
from app.services.osm import fetch_osm_proximity
from app.utils.http import get_http_client

logger = logging.getLogger(__name__)

# ── Weight configuration ──────────────────────────────────────────────────────

WEIGHT_NDVI = 0.40
WEIGHT_RAINFALL = 0.30
WEIGHT_SOIL = 0.20
WEIGHT_TEMPERATURE = 0.10

# Confidence weights — all five contribute equally
CONF_WEIGHT_NDVI = 0.20
CONF_WEIGHT_RAINFALL = 0.20
CONF_WEIGHT_SOIL = 0.20
CONF_WEIGHT_TEMPERATURE = 0.20
CONF_WEIGHT_OSM = 0.20


# ── Signal normalisation helpers ──────────────────────────────────────────────


def _normalise_ndvi(data: dict) -> float:
    """
    Map NDVI to a 0–100 score.

    Heuristic:
      • two_year_mean in [0.6, 1.0] → 80–100 (healthy vegetation)
      • [0.4, 0.6)                  → 60–80
      • [0.2, 0.4)                  → 30–60
      • [0.0, 0.2)                  → 0–30
      • trend_slope bonus/penalty   → ±10
    """
    mean_ndvi = data.get("two_year_mean")
    if mean_ndvi is None:
        return 0.0

    # Base score from mean NDVI (linear interpolation within bands)
    if mean_ndvi >= 0.6:
        base = 80 + (mean_ndvi - 0.6) / 0.4 * 20
    elif mean_ndvi >= 0.4:
        base = 60 + (mean_ndvi - 0.4) / 0.2 * 20
    elif mean_ndvi >= 0.2:
        base = 30 + (mean_ndvi - 0.2) / 0.2 * 30
    else:
        base = mean_ndvi / 0.2 * 30

    # Trend adjustment
    slope = data.get("trend_slope")
    if slope is not None:
        if slope > 0.005:
            base += 10
        elif slope < -0.005:
            base -= 10

    return max(0.0, min(100.0, round(base, 2)))


def _normalise_rainfall(data: dict) -> float:
    """
    Map rainfall adequacy to a 0–100 score.

    Heuristic:
      • 'normal' surplus_or_deficit → 80
      • 'surplus'                   → 60 (excess rain may cause problems)
      • 'deficit'                   → 30
      • deviation magnitude adjusts within the band
      • None / no data             → 0
    """
    flag = data.get("surplus_or_deficit")
    deviation = data.get("deviation_from_normal")

    if flag is None:
        return 0.0

    if flag == "normal":
        # Within ±10 % — excellent
        return 80.0 + min(20.0, abs(deviation or 0) * 2)

    if flag == "surplus":
        # Some surplus is ok, heavy surplus is risky
        penalty = min(50.0, abs(deviation or 0) * 0.5)
        return max(0.0, 80.0 - penalty)

    if flag == "deficit":
        # Deficit is concerning
        penalty = min(60.0, abs(deviation or 0) * 0.6)
        return max(0.0, 60.0 - penalty)

    return 0.0


def _normalise_soil(data: dict) -> float:
    """
    Map soil quality to a 0–100 score.

    Heuristic (simplified):
      • pH close to 6.5 (ideal for most crops) → high score
      • Organic carbon > 2 g/dm³              → bonus
      • Loam-type textures                     → bonus
    """
    score = 50.0  # neutral baseline

    # pH scoring: optimal range 5.5–7.5, ideal at 6.5
    ph = data.get("ph")
    if ph is not None:
        distance = abs(ph - 6.5)
        if distance <= 1.0:
            score += 25 * (1.0 - distance)  # up to +25
        elif distance <= 3.0:
            score -= 10 * (distance - 1.0)  # penalty

    # Organic carbon bonus
    oc = data.get("organic_carbon")
    if oc is not None and oc > 0:
        if oc >= 2.0:
            score += 15
        elif oc >= 1.0:
            score += 10
        else:
            score += 5

    # Texture bonus (loamy soils are best for most uses)
    soil_type = (data.get("soil_type") or "").lower()
    loamy_types = {"loam", "silt loam", "clay loam", "sandy clay loam", "silty clay loam"}
    if soil_type in loamy_types:
        score += 10
    elif "sand" in soil_type:
        score -= 5
    elif "clay" in soil_type and "loam" not in soil_type:
        score -= 5

    return max(0.0, min(100.0, round(score, 2)))


def _normalise_temperature(data: dict) -> float:
    """
    Map temperature suitability to a 0–100 score.

    Heuristic:
      • 0 heat stress months → 100
      • Each heat stress month subtracts points
      • No data → 0
    """
    trend = data.get("monthly_trend", [])
    if not trend:
        return 0.0

    heat_count = data.get("heat_stress_event_count", 0)
    total_months = len(trend)

    if total_months == 0:
        return 0.0

    # Fraction of months WITHOUT heat stress
    safe_fraction = (total_months - heat_count) / total_months
    return round(safe_fraction * 100, 2)


def _classify_status(score: float) -> str:
    """Map composite score to status label."""
    if score >= 75:
        return "Healthy"
    if score >= 50:
        return "Moderate"
    return "At Risk"


# ── Parallel service callers (with error isolation) ───────────────────────────


async def _safe_call_ndvi(
    lat: float, lng: float,
    min_lat: float, min_lng: float, max_lat: float, max_lng: float,
) -> tuple[dict | None, str | None]:
    """Call NDVI service; return (data, None) on success or (None, error_msg) on failure."""
    try:
        data = await fetch_ndvi_analysis(
            lat=lat, lng=lng,
            min_lat=min_lat, min_lng=min_lng,
            max_lat=max_lat, max_lng=max_lng,
        )
        return data, None
    except Exception as exc:
        logger.warning("NDVI service failed: %s", exc)
        return None, f"{type(exc).__name__}: {exc}"


async def _safe_call_rainfall(lat: float, lng: float) -> tuple[dict | None, str | None]:
    """Call rainfall service; return (data, None) on success or (None, error_msg) on failure."""
    try:
        data = await fetch_rainfall_analysis(lat=lat, lng=lng)
        return data, None
    except Exception as exc:
        logger.warning("Rainfall service failed: %s", exc)
        return None, f"{type(exc).__name__}: {exc}"


async def _safe_call_soil(lat: float, lng: float) -> tuple[dict | None, str | None]:
    """Call soil service; return (data, None) on success or (None, error_msg) on failure."""
    try:
        async with get_http_client() as client:
            data = await fetch_soil_data(lat=lat, lng=lng, client=client)
        return data, None
    except Exception as exc:
        logger.warning("Soil service failed: %s", exc)
        return None, f"{type(exc).__name__}: {exc}"


async def _safe_call_temperature(
    lat: float, lng: float,
    min_lat: float, min_lng: float, max_lat: float, max_lng: float,
) -> tuple[dict | None, str | None]:
    """Call temperature service; return (data, None) on success or (None, error_msg) on failure."""
    try:
        data = await fetch_temperature_analysis(
            lat=lat, lng=lng,
            min_lat=min_lat, min_lng=min_lng,
            max_lat=max_lat, max_lng=max_lng,
        )
        return data, None
    except Exception as exc:
        logger.warning("Temperature service failed: %s", exc)
        return None, f"{type(exc).__name__}: {exc}"


async def _safe_call_osm(
    lat: float, lng: float,
    min_lat: float, min_lng: float, max_lat: float, max_lng: float,
) -> tuple[dict | None, str | None]:
    """Call OSM service; return (data, None) on success or (None, error_msg) on failure."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            data = await fetch_osm_proximity(
                lat=lat, lng=lng,
                min_lat=min_lat, min_lng=min_lng,
                max_lat=max_lat, max_lng=max_lng,
                client=client,
            )
        return data, None
    except Exception as exc:
        logger.warning("OSM service failed: %s", exc)
        return None, f"{type(exc).__name__}: {exc}"


# ── Public interface ──────────────────────────────────────────────────────────


async def compute_health_score(
    lat: float,
    lng: float,
    min_lat: float,
    min_lng: float,
    max_lat: float,
    max_lng: float,
) -> dict:
    """
    Run all five services in parallel, normalise each signal,
    compute the weighted composite score, and return a structured dict
    ready to be unpacked into HealthScoreResponse.
    """

    # ── Fire all five services concurrently ───────────────────────────────────
    (
        (ndvi_data, ndvi_err),
        (rainfall_data, rainfall_err),
        (soil_data, soil_err),
        (temp_data, temp_err),
        (osm_data, osm_err),
    ) = await asyncio.gather(
        _safe_call_ndvi(lat, lng, min_lat, min_lng, max_lat, max_lng),
        _safe_call_rainfall(lat, lng),
        _safe_call_soil(lat, lng),
        _safe_call_temperature(lat, lng, min_lat, min_lng, max_lat, max_lng),
        _safe_call_osm(lat, lng, min_lat, min_lng, max_lat, max_lng),
    )

    # ── Normalise each signal ─────────────────────────────────────────────────
    ndvi_score = _normalise_ndvi(ndvi_data) if ndvi_data else 0.0
    ndvi_conf = ndvi_data.get("confidence_score", 0.0) if ndvi_data else 0.0

    rainfall_score = _normalise_rainfall(rainfall_data) if rainfall_data else 0.0
    rainfall_conf = rainfall_data.get("confidence_score", 0.0) if rainfall_data else 0.0

    soil_score = _normalise_soil(soil_data) if soil_data else 0.0
    soil_conf = soil_data.get("confidence_score", 0.0) if soil_data else 0.0

    temp_score = _normalise_temperature(temp_data) if temp_data else 0.0
    temp_conf = temp_data.get("confidence_score", 0.0) if temp_data else 0.0

    # OSM doesn't contribute to the health score but does contribute to confidence
    osm_score = 0.0  # not used in composite — OSM is contextual, not health-related
    osm_conf = osm_data.get("confidence_score", 0.0) if osm_data else 0.0

    # ── Composite score (weighted) ────────────────────────────────────────────
    composite = (
        ndvi_score * WEIGHT_NDVI
        + rainfall_score * WEIGHT_RAINFALL
        + soil_score * WEIGHT_SOIL
        + temp_score * WEIGHT_TEMPERATURE
    )
    composite = round(max(0.0, min(100.0, composite)), 2)

    # ── Overall confidence (weighted average of all five) ─────────────────────
    overall_confidence = round(
        ndvi_conf * CONF_WEIGHT_NDVI
        + rainfall_conf * CONF_WEIGHT_RAINFALL
        + soil_conf * CONF_WEIGHT_SOIL
        + temp_conf * CONF_WEIGHT_TEMPERATURE
        + osm_conf * CONF_WEIGHT_OSM,
        4,
    )

    status = _classify_status(composite)

    # ── Collect partial-failure messages ───────────────────────────────────────
    failures = []
    if ndvi_err:
        failures.append(f"NDVI: {ndvi_err}")
    if rainfall_err:
        failures.append(f"Rainfall: {rainfall_err}")
    if soil_err:
        failures.append(f"Soil: {soil_err}")
    if temp_err:
        failures.append(f"Temperature: {temp_err}")
    if osm_err:
        failures.append(f"OSM: {osm_err}")

    message: Optional[str] = None
    if failures:
        message = (
            f"{len(failures)} of 5 signals failed (scores set to 0 for those): "
            + "; ".join(failures)
        )

    return {
        "lat": lat,
        "lng": lng,
        "score": composite,
        "status": status,
        "confidence_score": overall_confidence,
        "signals": {
            "ndvi": {
                "score": ndvi_score,
                "confidence": ndvi_conf,
                "data": ndvi_data,
                "error": ndvi_err,
            },
            "rainfall": {
                "score": rainfall_score,
                "confidence": rainfall_conf,
                "data": rainfall_data,
                "error": rainfall_err,
            },
            "soil": {
                "score": soil_score,
                "confidence": soil_conf,
                "data": soil_data,
                "error": soil_err,
            },
            "temperature": {
                "score": temp_score,
                "confidence": temp_conf,
                "data": temp_data,
                "error": temp_err,
            },
            "osm": {
                "score": osm_score,
                "confidence": osm_conf,
                "data": osm_data,
                "error": osm_err,
            },
        },
        "message": message,
    }
