"""
app/models/health_score.py
Pydantic response schemas for the /health-score endpoint.

The composite Land Health Score (0–100) aggregates five signals:
  • NDVI trend          (40 %)
  • Rainfall adequacy   (30 %)
  • Soil quality         (20 %)
  • Temperature suitability (10 %)
  • OSM proximity        (used in confidence, not the score itself)
"""

from pydantic import BaseModel, Field
from typing import Any, Optional


class SignalResult(BaseModel):
    """Wrapper for one signal's raw output + its individual score/confidence."""

    score: float = Field(
        ..., ge=0, le=100,
        description="Normalised score for this signal (0–100)",
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="Individual confidence score (0.0–1.0)",
    )
    data: Optional[Any] = Field(
        None,
        description="Full raw result from the underlying service",
    )
    error: Optional[str] = Field(
        None,
        description="Error message if the service call failed",
    )


class SignalsContainer(BaseModel):
    """All five raw signal results nested together."""

    ndvi: SignalResult
    rainfall: SignalResult
    soil: SignalResult
    temperature: SignalResult
    osm: SignalResult


class HealthScoreResponse(BaseModel):
    """Composite Land Health Score response returned by GET /health-score."""

    lat: float = Field(..., description="Centroid latitude")
    lng: float = Field(..., description="Centroid longitude")

    score: float = Field(
        ..., ge=0, le=100,
        description=(
            "Composite Land Health Score (0–100). Weights: "
            "NDVI 40%, Rainfall 30%, Soil 20%, Temperature 10%."
        ),
    )
    status: str = Field(
        ...,
        description=(
            "Status label: 'Healthy' (75–100), "
            "'Moderate' (50–74), 'At Risk' (below 50)"
        ),
    )
    confidence_score: float = Field(
        ..., ge=0.0, le=1.0,
        description=(
            "Overall confidence as the weighted average of all five "
            "individual signal confidence scores."
        ),
    )
    factors: list[str] = Field(
        default_factory=list,
        description=(
            "Top 3 plain-English factors driving the score up or down, "
            "e.g. 'NDVI trending down -12% over 2 years'."
        ),
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description=(
            "2–3 actionable suggestions based on the signal scores, "
            "e.g. 'Consider drought-resistant crop varieties'."
        ),
    )
    signals: SignalsContainer = Field(
        ...,
        description="All five raw signal results with individual scores and data",
    )
    message: Optional[str] = Field(
        None,
        description="Informational message (e.g. partial-failure details)",
    )
