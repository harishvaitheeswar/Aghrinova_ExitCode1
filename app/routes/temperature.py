"""
app/routes/temperature.py
GET /temperature — ERA5-PDS temperature analysis for a given bounding box.

Data source:
  • Microsoft Planetary Computer STAC – ERA5-PDS collection

NOTE: ERA5 has ~0.25° grid resolution (~28 km).  All temperature outputs
      are labelled as **regional-level** precision, NOT parcel-level.
"""

from fastapi import APIRouter, Query, HTTPException

from app.models.temperature import TemperatureResponse
from app.services.temperature import fetch_temperature_analysis

router = APIRouter(tags=["Temperature Intelligence"])


@router.get(
    "/temperature",
    response_model=TemperatureResponse,
    summary="Regional temperature analysis for a land area",
    description=(
        "Queries the **Microsoft Planetary Computer STAC** catalog for "
        "ERA5-PDS reanalysis temperature data covering the bounding box "
        "over the past 24 months. Returns: a monthly temperature trend "
        "array, a heat-stress event count (months where mean temp ≥ 35 °C), "
        "and a confidence score. **Precision: regional-level (~28 km grid), "
        "not parcel-level.** Handles no-data and upstream timeouts gracefully."
    ),
)
async def get_temperature(
    lat: float = Query(
        ..., ge=-90, le=90,
        description="Centroid latitude (for reference/labelling)",
    ),
    lng: float = Query(
        ..., ge=-180, le=180,
        description="Centroid longitude (for reference/labelling)",
    ),
    min_lat: float = Query(
        ..., ge=-90, le=90,
        description="Bounding box southern boundary latitude",
    ),
    min_lng: float = Query(
        ..., ge=-180, le=180,
        description="Bounding box western boundary longitude",
    ),
    max_lat: float = Query(
        ..., ge=-90, le=90,
        description="Bounding box northern boundary latitude",
    ),
    max_lng: float = Query(
        ..., ge=-180, le=180,
        description="Bounding box eastern boundary longitude",
    ),
) -> TemperatureResponse:
    """
    ### Parameters
    | Name      | Description                            |
    |-----------|----------------------------------------|
    | `lat`     | Centroid latitude (reference only)      |
    | `lng`     | Centroid longitude (reference only)     |
    | `min_lat` | South edge of the bounding box         |
    | `min_lng` | West edge of the bounding box          |
    | `max_lat` | North edge of the bounding box         |
    | `max_lng` | East edge of the bounding box          |

    ### Returns
    - **monthly_trend** — array of `{month, mean_temp_c}` objects (regional precision)
    - **heat_stress_event_count** — months where mean temp ≥ 35 °C
    - **confidence_score** — 0.0 (no data) → 1.0 (≥ 24 items)
    - **precision_label** — always 'regional-level (~0.25° / ~28 km grid)'
    """

    # ── Bounding box sanity checks ────────────────────────────────────────────
    if min_lat >= max_lat:
        raise HTTPException(
            status_code=422,
            detail="min_lat must be less than max_lat.",
        )
    if min_lng >= max_lng:
        raise HTTPException(
            status_code=422,
            detail="min_lng must be less than max_lng.",
        )

    # ── Call service layer ────────────────────────────────────────────────────
    try:
        data = await fetch_temperature_analysis(
            lat=lat, lng=lng,
            min_lat=min_lat, min_lng=min_lng,
            max_lat=max_lat, max_lng=max_lng,
        )

    except TimeoutError:
        raise HTTPException(
            status_code=503,
            detail=(
                "The Planetary Computer STAC API did not respond in time. "
                "Please retry in a moment."
            ),
        )

    except Exception as exc:
        error_name = type(exc).__name__
        raise HTTPException(
            status_code=502,
            detail=(
                f"Failed to retrieve temperature data from Planetary Computer "
                f"({error_name}): {exc}. Please try again shortly."
            ),
        )

    return TemperatureResponse(**data)
