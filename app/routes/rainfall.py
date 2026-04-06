"""
app/routes/rainfall.py
GET /rainfall — CHIRPS-2.0 precipitation analysis for a given centroid.

Data source:
  • Microsoft Planetary Computer STAC – CHIRPS-2.0 collection
"""

from fastapi import APIRouter, Query, HTTPException

from app.models.rainfall import RainfallResponse
from app.services.rainfall import fetch_rainfall_analysis

router = APIRouter(tags=["Rainfall Intelligence"])


@router.get(
    "/rainfall",
    response_model=RainfallResponse,
    summary="Rainfall analysis for a land parcel",
    description=(
        "Queries the **Microsoft Planetary Computer STAC** catalog for "
        "CHIRPS-2.0 precipitation data around the given centroid over "
        "the past 24 months. Returns: estimated annual rainfall (mm), "
        "a 12-element monthly distribution array (Jan–Dec), deviation "
        "from the 2-year mean (%), a surplus/deficit/normal flag, and "
        "a confidence score. Handles no-data and upstream timeouts gracefully."
    ),
)
async def get_rainfall(
    lat: float = Query(
        ..., ge=-90, le=90,
        description="Centroid latitude (−90 to 90)",
    ),
    lng: float = Query(
        ..., ge=-180, le=180,
        description="Centroid longitude (−180 to 180)",
    ),
) -> RainfallResponse:
    """
    ### Parameters
    | Name  | Description          |
    |-------|----------------------|
    | `lat` | Centroid latitude    |
    | `lng` | Centroid longitude   |

    ### Returns
    - **annual_mm** — estimated total annual rainfall in millimetres
    - **monthly_distribution** — 12-element array of mean monthly rainfall (Jan–Dec)
    - **deviation_from_normal** — % above or below the 2-year mean
    - **surplus_or_deficit** — 'surplus' ∣ 'deficit' ∣ 'normal'
    - **confidence_score** — 0.0 (no data) → 1.0 (≥ 24 items)
    """

    # ── Call service layer ────────────────────────────────────────────────────
    try:
        data = await fetch_rainfall_analysis(lat=lat, lng=lng)

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
                f"Failed to retrieve rainfall data from Planetary Computer "
                f"({error_name}): {exc}. Please try again shortly."
            ),
        )

    return RainfallResponse(**data)
