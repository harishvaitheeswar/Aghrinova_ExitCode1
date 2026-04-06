"""
app/routes/osm.py
GET /osm-proximity — nearest highway, water body, and town distances
for a given centroid + bounding box, plus Nominatim reverse geocoding.

Data sources:
  • OSM Overpass API  — https://overpass-api.de/api/interpreter
  • OSM Nominatim     — https://nominatim.openstreetmap.org/reverse
"""

from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import JSONResponse

import httpx

from app.models.osm import OSMProximityResponse
from app.services.osm import fetch_osm_proximity
from app.utils.http import get_http_client

router = APIRouter(tags=["OSM Intelligence"])


@router.get(
    "/osm-proximity",
    response_model=OSMProximityResponse,
    summary="OSM proximity analysis for a land parcel",
    description=(
        "Queries the **OpenStreetMap Overpass API** for the nearest highway, "
        "water body, and town/settlement within the supplied bounding box. "
        "Also calls **Nominatim** reverse geocoding on the centroid to resolve "
        "town name, taluk, and district. "
        "All four upstream requests are fired concurrently; individual failures "
        "degrade gracefully — the response always includes a `confidence_score` "
        "reflecting how much data was successfully retrieved."
    ),
)
async def get_osm_proximity(
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
) -> OSMProximityResponse:
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
    - **nearest_highway** — distance in metres + road class + name
    - **nearest_water** — distance in metres + waterway type + name
    - **nearest_town** — distance in metres + settlement type + name
    - **address** — town, taluk, district, state, country from Nominatim
    - **confidence_score** — 0.0 (all lookups failed) → 1.0 (all succeeded)
    """

    # ── Basic bbox sanity check ───────────────────────────────────────────────
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
        async with get_http_client() as client:
            # Override the default 30 s timeout with a tighter OSM-appropriate one
            client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
            async with client:
                data = await fetch_osm_proximity(
                    lat=lat, lng=lng,
                    min_lat=min_lat, min_lng=min_lng,
                    max_lat=max_lat, max_lng=max_lng,
                    client=client,
                )

    except httpx.TimeoutException:
        raise HTTPException(
            status_code=503,
            detail=(
                "One or more OSM API calls timed out. The upstream services may "
                "be under heavy load — please retry in a moment."
            ),
        )

    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 429:
            raise HTTPException(
                status_code=429,
                detail=(
                    "OSM API rate limit reached. Please wait a few seconds "
                    "before retrying."
                ),
            )
        if status in (502, 503, 504):
            raise HTTPException(
                status_code=503,
                detail=(
                    f"OSM upstream service is currently unreachable "
                    f"(HTTP {status}). Please try again shortly."
                ),
            )
        raise HTTPException(
            status_code=502,
            detail=(
                f"Unexpected response from OSM API (HTTP {status}). "
                "Please check your parameters and try again."
            ),
        )

    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Could not reach OSM services: {exc}. "
                "Check your network connection or try again later."
            ),
        )

    return OSMProximityResponse(**data)
