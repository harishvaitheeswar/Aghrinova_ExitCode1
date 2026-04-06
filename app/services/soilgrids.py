"""
app/services/soilgrids.py
Calls the ISRIC SoilGrids v2.0 REST API and parses the response into a
structured dict consumed by the /soil route.

SoilGrids unit notes
─────────────────────
  phh2o  → pH × 10   (divide by 10 → real pH)
  ocd    → g/dm³ × 10 (divide by 10 → g/dm³)
  clay   → g/kg       (divide by 10 → %)
  sand   → g/kg       (divide by 10 → %)
  silt   → g/kg       (divide by 10 → %)
"""

from __future__ import annotations

import httpx
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────

SOILGRIDS_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
TARGET_DEPTH = "0-5cm"

# Properties to request: texture fractions + pH + organic carbon density
PROPERTIES = ["phh2o", "ocd", "clay", "sand", "silt"]

# Conversion divisors (raw SoilGrids value → real-world unit)
UNIT_DIVISORS: dict[str, float] = {
    "phh2o": 10.0,   # → pH (0–14)
    "ocd":   10.0,   # → g/dm³
    "clay":  10.0,   # → %
    "sand":  10.0,   # → %
    "silt":  10.0,   # → %
}


# ── USDA texture classification ───────────────────────────────────────────────

def _classify_texture(clay: float, sand: float, silt: float) -> str:  # noqa: C901
    """
    Simplified USDA texture triangle classification.
    Falls back to 'Unknown' when fractions are unavailable.
    """
    if clay is None or sand is None or silt is None:
        return "Unknown"

    # Normalise to 100 % if needed
    total = clay + sand + silt
    if total > 0 and abs(total - 100.0) > 5.0:
        clay  = clay  / total * 100.0
        sand  = sand  / total * 100.0
        silt  = silt  / total * 100.0

    if clay >= 40:
        if sand >= 45:
            return "Sandy Clay"
        if silt >= 40:
            return "Silty Clay"
        return "Clay"
    if clay >= 27:
        if sand >= 45:
            return "Sandy Clay Loam"
        if silt >= 27 and sand < 20:
            return "Silty Clay Loam"
        return "Clay Loam"
    if clay >= 20:
        if sand >= 52:
            return "Sandy Loam"
        return "Loam"
    if silt >= 80:
        return "Silt"
    if silt >= 50:
        return "Silt Loam"
    if sand >= 70:
        return "Sandy Loam"
    if sand >= 85:
        return "Sand"
    return "Loam"


# ── Parsing helpers ───────────────────────────────────────────────────────────

def _extract_depth_values(
    layer: dict, depth_label: str
) -> tuple[Optional[float], Optional[float]]:
    """Return (mean, uncertainty) for the requested depth label, or (None, None)."""
    for depth in layer.get("depths", []):
        if depth.get("label") == depth_label:
            values = depth.get("values", {})
            return values.get("mean"), values.get("uncertainty")
    return None, None


def _parse_layers(layers: list[dict]) -> dict[str, dict]:
    """
    Build a flat lookup  {property_name: {mean: float|None, uncertainty: float|None}}
    with real-world unit conversions applied.
    """
    result: dict[str, dict] = {}
    for layer in layers:
        name = layer.get("name", "")
        if name not in PROPERTIES:
            continue
        raw_mean, raw_uncertainty = _extract_depth_values(layer, TARGET_DEPTH)
        divisor = UNIT_DIVISORS.get(name, 1.0)
        result[name] = {
            "mean": raw_mean / divisor if raw_mean is not None else None,
            "uncertainty": raw_uncertainty / divisor if raw_uncertainty is not None else None,
        }
    return result


def _compute_confidence(parsed: dict[str, dict]) -> float:
    """
    Confidence score (0–1) = mean of per-layer confidence values.

    Per-layer confidence:
      • If mean == 0 or unavailable → 0
      • Otherwise = max(0, 1 − |uncertainty / mean|)
    Clamped to [0.0, 1.0].
    """
    scores: list[float] = []
    for info in parsed.values():
        mean = info.get("mean")
        unc  = info.get("uncertainty")
        if mean is None or mean == 0:
            scores.append(0.0)
        elif unc is None:
            scores.append(1.0)
        else:
            scores.append(max(0.0, 1.0 - abs(unc / mean)))

    if not scores:
        return 0.0
    return round(min(1.0, sum(scores) / len(scores)), 4)


# ── Public interface ──────────────────────────────────────────────────────────

async def fetch_soil_data(lat: float, lng: float, client: httpx.AsyncClient) -> dict:
    """
    Query SoilGrids and return a parsed soil data dict.

    Raises:
        httpx.HTTPStatusError   – non-2xx from SoilGrids
        httpx.RequestError      – network / timeout issues
    """
    params = {
        "lon":      lng,
        "lat":      lat,
        "property": PROPERTIES,
        "depth":    TARGET_DEPTH,
        "value":    ["mean", "uncertainty"],
    }

    response = await client.get(SOILGRIDS_URL, params=params)
    response.raise_for_status()
    body = response.json()

    layers: list[dict] = (
        body.get("properties", {}).get("layers", [])
    )

    parsed = _parse_layers(layers)

    ph              = parsed.get("phh2o", {}).get("mean")
    organic_carbon  = parsed.get("ocd",   {}).get("mean")
    clay            = parsed.get("clay",  {}).get("mean")
    sand            = parsed.get("sand",  {}).get("mean")
    silt            = parsed.get("silt",  {}).get("mean")

    soil_type       = _classify_texture(clay, sand, silt)
    confidence      = _compute_confidence(parsed)

    def _r(v: Optional[float], ndigits: int = 2) -> Optional[float]:
        return round(v, ndigits) if v is not None else None

    return {
        "lat":              lat,
        "lng":              lng,
        "soil_type":        soil_type,
        "ph":               _r(ph),
        "organic_carbon":   _r(organic_carbon),
        "texture": {
            "clay_pct":  _r(clay),
            "sand_pct":  _r(sand),
            "silt_pct":  _r(silt),
        },
        "confidence_score": confidence,
        "depth_label":      TARGET_DEPTH,
        "data_source":      "ISRIC SoilGrids v2.0",
    }
