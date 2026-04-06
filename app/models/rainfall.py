"""
app/models/rainfall.py
Pydantic response schemas for the /rainfall endpoint.
Data source: TerraClimate (ppt) via Microsoft Planetary Computer Zarr.
"""

from pydantic import BaseModel, Field
from typing import Optional


class RainfallResponse(BaseModel):
    """Full rainfall analysis response returned by GET /rainfall."""

    lat: float = Field(..., description="Centroid latitude")
    lng: float = Field(..., description="Centroid longitude")

    annual_mm: Optional[float] = Field(
        None,
        description="Estimated total annual rainfall in millimetres (extrapolated from the 24-month window)",
    )
    monthly_distribution: list[float] = Field(
        default_factory=list,
        description=(
            "Array of 12 values representing mean monthly rainfall (mm) "
            "for Jan–Dec, averaged over the 24-month window"
        ),
    )
    deviation_from_normal: Optional[float] = Field(
        None,
        description=(
            "Percentage deviation of annual rainfall from the 2-year mean. "
            "Positive = above normal, negative = below normal."
        ),
    )
    surplus_or_deficit: Optional[str] = Field(
        None,
        description=(
            "'surplus' if >10%% above the 2-year mean, "
            "'deficit' if >10%% below, otherwise 'normal'"
        ),
    )
    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence based on the number of monthly data points found (0.0–1.0)",
    )
    data_source: str = Field(
        default="Microsoft Planetary Computer – TerraClimate (ppt)",
        description="Data source attribution",
    )
    message: Optional[str] = Field(
        None,
        description="Informational message (e.g. no-data explanation)",
    )
