"""
app/routes/health_score.py
GET /health-score — Composite Land Health Score aggregating all five
data signals (NDVI, Rainfall, Soil, Temperature, OSM).

Weights: NDVI 40 %, Rainfall 30 %, Soil 20 %, Temperature 10 %.
Status:  Healthy (75–100) │ Moderate (50–74) │ At Risk (< 50)

Individual service failures are isolated — partial results are always
returned rather than failing the whole endpoint.
"""

from fastapi import APIRouter, Query, HTTPException

from app.models.health_score import HealthScoreResponse
from app.services.health_score import compute_health_score

router = APIRouter(tags=["Land Health Score"])


@router.get(
    "/health-score",
    response_model=HealthScoreResponse,
    summary="Composite Land Health Score",
    description=(
        "Calls all five underlying services (**NDVI**, **Rainfall**, **Soil**, "
        "**Temperature**, **OSM proximity**) in parallel and computes a single "
        "composite Land Health Score from 0 to 100. Weights: NDVI trend 40 %, "
        "rainfall adequacy 30 %, soil quality 20 %, temperature suitability 10 %. "
        "The response includes the overall score, a status label (Healthy / "
        "Moderate / At Risk), an overall confidence score (weighted average of "
        "all five individual confidence scores), and the raw results from each "
        "signal nested inside a `signals` object. If any individual service "
        "fails, its score and confidence default to 0 — partial results are "
        "always preferred over a total failure."
    ),
)
async def get_health_score(
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
) -> HealthScoreResponse:
    """
    ### Parameters
    | Name      | Description                        |
    |-----------|------------------------------------|
    | `lat`     | Centroid latitude                  |
    | `lng`     | Centroid longitude                 |
    | `min_lat` | South edge of the bounding box     |
    | `min_lng` | West edge of the bounding box      |
    | `max_lat` | North edge of the bounding box     |
    | `max_lng` | East edge of the bounding box      |

    ### Returns
    - **score** — Composite Land Health Score (0–100)
    - **status** — Healthy ∣ Moderate ∣ At Risk
    - **confidence_score** — Weighted average of all five signal confidences
    - **signals** — `{ndvi, rainfall, soil, temperature, osm}` with raw data
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

    # ── Compute composite score (all errors handled internally) ───────────────
    try:
        data = await compute_health_score(
            lat=lat, lng=lng,
            min_lat=min_lat, min_lng=min_lng,
            max_lat=max_lat, max_lng=max_lng,
        )
    except Exception as exc:
        # This should not normally happen since individual services are
        # isolated, but catch any orchestration-level bug.
        error_name = type(exc).__name__
        raise HTTPException(
            status_code=500,
            detail=(
                f"Unexpected error computing health score "
                f"({error_name}): {exc}."
            ),
        )

    return HealthScoreResponse(**data)
