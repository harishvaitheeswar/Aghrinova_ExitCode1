"""
app/routes/soil.py
GET /soil — returns structured soil data for a given lat/lng coordinate.

Data source: ISRIC SoilGrids v2.0
  https://rest.isric.org/soilgrids/v2.0/properties/query
"""

from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import JSONResponse

import httpx

from app.models.soil import SoilResponse
from app.services.soilgrids import fetch_soil_data
from app.utils.http import get_http_client

router = APIRouter(tags=["Soil Intelligence"])


@router.get(
    "/soil",
    response_model=SoilResponse,
    summary="Soil properties at a coordinate",
    description=(
        "Queries the **ISRIC SoilGrids v2.0** API for the 0–5 cm depth layer "
        "and returns pH, organic carbon density, texture fractions (clay/sand/silt), "
        "a USDA texture class, and a confidence score derived from SoilGrids "
        "mean/uncertainty values."
    ),
)
async def get_soil(
    lat: float = Query(..., ge=-90,  le=90,  description="Latitude  (−90 to 90)"),
    lng: float = Query(..., ge=-180, le=180, description="Longitude (−180 to 180)"),
) -> SoilResponse:
    """
    ### Returns
    - **soil_type** — USDA texture class (e.g. *Sandy Clay Loam*)
    - **ph** — soil pH in water at 0–5 cm
    - **organic_carbon** — organic carbon density in g/dm³ at 0–5 cm
    - **texture** — `clay_pct`, `sand_pct`, `silt_pct`
    - **confidence_score** — 0.0 (uncertain) → 1.0 (confident)
    """
    try:
        async with get_http_client() as client:
            data = await fetch_soil_data(lat=lat, lng=lng, client=client)

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=503,
            detail=(
                "SoilGrids API timed out. The upstream service may be under load — "
                "please retry in a moment."
            ),
        )

    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in (502, 503, 504):
            raise HTTPException(
                status_code=503,
                detail=(
                    f"SoilGrids API is currently unreachable (upstream HTTP {status}). "
                    "Please try again shortly."
                ),
            )
        # Surface unexpected HTTP errors as 502 Bad Gateway
        raise HTTPException(
            status_code=502,
            detail=(
                f"Unexpected response from SoilGrids API (HTTP {status}). "
                "Please check the coordinates and try again."
            ),
        )

    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Could not reach the SoilGrids API: {exc}. "
                "Check your network connection or try again later."
            ),
        )

    return SoilResponse(**data)
