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


def _generate_recommendations(
    composite: float,
    ndvi_score: float,
    rainfall_score: float,
    soil_score: float,
    soil_conf: float,
    temp_score: float,
) -> list[str]:
    """
    Generate 2–3 actionable, plain-English recommendations from signal scores.

    Rules (evaluated in order, capped at 3):
      1. Low NDVI + low rainfall           → drought-resistant varieties
      2. Low temperature score              → irrigation during heat stress
      3. Low NDVI (standalone)              → vegetation restoration
      4. Low rainfall (standalone)          → water harvesting / irrigation
      5. Poor soil (only if confidence > 0) → soil amendment
      6. High overall score                 → positive outlook
    """
    recs: list[str] = []

    # 1. Combined drought signal
    if ndvi_score < 50 and rainfall_score < 50:
        recs.append(
            "Consider drought-resistant crop varieties — both vegetation "
            "health and rainfall are below average."
        )

    # 2. Temperature / heat-stress
    if temp_score < 60:
        recs.append(
            "Plan irrigation during March–May heat stress period to "
            "protect crops from high-temperature damage."
        )

    # 3. NDVI alone (skip if already covered by #1)
    if ndvi_score < 50 and rainfall_score >= 50 and len(recs) < 3:
        recs.append(
            "Vegetation cover is declining — explore cover cropping or "
            "agroforestry to restore green cover."
        )

    # 4. Rainfall alone (skip if already covered by #1)
    if rainfall_score < 50 and ndvi_score >= 50 and len(recs) < 3:
        recs.append(
            "Rainfall is below normal — invest in rainwater harvesting "
            "or drip irrigation to secure water supply."
        )

    # 5. Soil quality (skip when confidence is 0 → no soil data)
    if soil_conf > 0 and soil_score < 50 and len(recs) < 3:
        recs.append(
            "Soil quality is limited — consider lime or gypsum amendments "
            "and organic mulching to improve fertility."
        )

    # 6. Positive message when things look good
    if composite > 75 and len(recs) < 3:
        recs.append(
            "Land is in good health — suitable for high-value crop "
            "cultivation such as horticulture or spice farming."
        )

    # Ensure at least one generic recommendation if nothing matched
    if not recs:
        recs.append(
            "Monitor seasonal changes and re-check the health score "
            "before each cropping cycle for best results."
        )

    return recs[:3]


def _generate_factors(
    ndvi_score: float,
    ndvi_data: dict | None,
    rainfall_score: float,
    rainfall_data: dict | None,
    soil_score: float,
    soil_data: dict | None,
    temp_score: float,
    temp_data: dict | None,
) -> list[str]:
    """
    Build a list of plain-English factor strings describing what is
    driving the composite score up or down.  Returns the top 3 factors
    sorted by absolute impact (weight × deviation from 100).

    Each entry is a tuple (abs_impact, text) so we can rank them.
    """
    candidates: list[tuple[float, str]] = []

    # ── NDVI factors ──────────────────────────────────────────────────────────
    if ndvi_data:
        trend_slope = ndvi_data.get("trend_slope")
        two_year_mean = ndvi_data.get("two_year_mean")

        if trend_slope is not None and two_year_mean and two_year_mean > 0:
            pct_change = round(trend_slope / two_year_mean * 100, 1)
            direction = "up" if pct_change >= 0 else "down"
            candidates.append((
                abs(100 - ndvi_score) * WEIGHT_NDVI,
                f"NDVI trending {direction} {pct_change:+.1f}% over 2 years",
            ))
        elif two_year_mean is not None:
            label = "strong" if two_year_mean >= 0.6 else "moderate" if two_year_mean >= 0.4 else "weak"
            candidates.append((
                abs(100 - ndvi_score) * WEIGHT_NDVI,
                f"Vegetation health is {label} (mean NDVI {two_year_mean:.2f})",
            ))
    elif ndvi_score == 0.0:
        candidates.append((100 * WEIGHT_NDVI, "NDVI data unavailable"))

    # ── Rainfall factors ──────────────────────────────────────────────────────
    if rainfall_data:
        deviation = rainfall_data.get("deviation_from_normal")
        flag = rainfall_data.get("surplus_or_deficit")

        if deviation is not None and flag:
            abs_dev = abs(round(deviation, 1))
            if flag == "deficit":
                candidates.append((
                    abs(100 - rainfall_score) * WEIGHT_RAINFALL,
                    f"Rainfall {abs_dev}% below seasonal normal",
                ))
            elif flag == "surplus":
                candidates.append((
                    abs(100 - rainfall_score) * WEIGHT_RAINFALL,
                    f"Rainfall {abs_dev}% above seasonal normal",
                ))
            else:
                candidates.append((
                    abs(100 - rainfall_score) * WEIGHT_RAINFALL,
                    f"Rainfall within {abs_dev}% of seasonal normal",
                ))
    elif rainfall_score == 0.0:
        candidates.append((100 * WEIGHT_RAINFALL, "Rainfall data unavailable"))

    # ── Temperature factors ───────────────────────────────────────────────────
    if temp_data:
        heat_count = temp_data.get("heat_stress_event_count", 0)
        total_months = len(temp_data.get("monthly_trend", []))

        if heat_count > 0:
            candidates.append((
                abs(100 - temp_score) * WEIGHT_TEMPERATURE,
                f"Temperature stress detected in {heat_count} month{'s' if heat_count != 1 else ''}",
            ))
        elif total_months > 0:
            candidates.append((
                abs(100 - temp_score) * WEIGHT_TEMPERATURE,
                "No temperature stress detected",
            ))
    elif temp_score == 0.0:
        candidates.append((100 * WEIGHT_TEMPERATURE, "Temperature data unavailable"))

    # ── Soil factors ──────────────────────────────────────────────────────────
    if soil_data:
        ph = soil_data.get("ph")
        oc = soil_data.get("organic_carbon")
        soil_type = soil_data.get("soil_type", "")

        parts: list[str] = []
        if ph is not None:
            distance = abs(ph - 6.5)
            if distance <= 1.0:
                parts.append(f"near-ideal pH {ph:.1f}")
            else:
                parts.append(f"pH {ph:.1f} ({'acidic' if ph < 6.5 else 'alkaline'})")
        if oc is not None:
            level = "high" if oc >= 2.0 else "moderate" if oc >= 1.0 else "low"
            parts.append(f"{level} organic carbon")
        if soil_type:
            parts.append(f"{soil_type} texture")

        if parts:
            candidates.append((
                abs(100 - soil_score) * WEIGHT_SOIL,
                "Soil profile: " + ", ".join(parts),
            ))
    elif soil_score == 0.0:
        candidates.append((100 * WEIGHT_SOIL, "Soil data unavailable"))

    # ── Sort by absolute impact (descending) and return top 3 ─────────────────
    candidates.sort(key=lambda c: c[0], reverse=True)
    return [text for _, text in candidates[:3]]


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

    # ── Generate top-3 plain-English factors ────────────────────────────────────
    factors = _generate_factors(
        ndvi_score=ndvi_score, ndvi_data=ndvi_data,
        rainfall_score=rainfall_score, rainfall_data=rainfall_data,
        soil_score=soil_score, soil_data=soil_data,
        temp_score=temp_score, temp_data=temp_data,
    )

    # ── Generate 2–3 actionable recommendations ────────────────────────────────
    recommendations = _generate_recommendations(
        composite=composite,
        ndvi_score=ndvi_score,
        rainfall_score=rainfall_score,
        soil_score=soil_score,
        soil_conf=soil_conf,
        temp_score=temp_score,
    )

    return {
        "lat": lat,
        "lng": lng,
        "score": composite,
        "status": status,
        "confidence_score": overall_confidence,
        "factors": factors,
        "recommendations": recommendations,
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
