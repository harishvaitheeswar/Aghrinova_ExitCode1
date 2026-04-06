"""
app/routes/ndvi.py
GET /ndvi — Sentinel-2 NDVI analysis for a given centroid + bounding box.

Data source:
  • Microsoft Planetary Computer STAC – Sentinel-2 L2A collection
"""

from fastapi import APIRouter, Query, HTTPException

from app.models.ndvi import NDVIResponse
from app.services.planetary import fetch_ndvi_analysis

router = APIRouter(tags=["NDVI Intelligence"])


@router.get(
    "/ndvi",
    response_model=NDVIResponse,
    summary="NDVI vegetation analysis for a land parcel",
    description=(
        "Queries the **Microsoft Planetary Computer STAC** catalog for "
        "Sentinel-2 L2A scenes over the past 24 months. Filters for scenes "
        "with cloud cover below 20 %, extracts NDVI values, and returns: "
        "the latest NDVI, a two-year mean, monthly trend array, a vegetation "
        "status label (Healthy / Degrading / Recovering / Low / Unknown), "
        "and a confidence score proportional to the number of clean scenes. "
        "Handles no-data and upstream timeouts gracefully."
    ),
)
async def get_ndvi(
    lat: float = Query(
        ..., ge=-90, le=90,
        description="Centroid latitude (−90 to 90)",
    ),
    lng: float = Query(
        ..., ge=-180, le=180,
        description="Centroid longitude (−180 to 180)",
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
) -> NDVIResponse:
    """
    ### Parameters
    | Name      | Description |
    |-----------|-------------|
    | `lat`     | Centroid latitude |
    | `lng`     | Centroid longitude |
    | `min_lat` | South edge of the bounding box |
    | `min_lng` | West edge of the bounding box |
    | `max_lat` | North edge of the bounding box |
    | `max_lng` | East edge of the bounding box |

    ### Returns
    - **current_ndvi** — latest NDVI value from the most recent clean scene
    - **two_year_mean** — mean NDVI across all clean scenes (24 months)
    - **monthly_trend** — array of `{month, ndvi, scenes}` objects
    - **status** — Healthy ∣ Degrading ∣ Recovering ∣ Low ∣ Unknown
    - **confidence_score** — 0.0 (no data) → 1.0 (≥ 24 clean scenes)
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
        data = await fetch_ndvi_analysis(
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
        # pystac_client.exceptions.APIError and other unexpected errors
        error_name = type(exc).__name__
        raise HTTPException(
            status_code=502,
            detail=(
                f"Failed to retrieve NDVI data from Planetary Computer "
                f"({error_name}): {exc}. Please try again shortly."
            ),
        )

    return NDVIResponse(**data)
