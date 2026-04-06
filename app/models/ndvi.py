"""
app/models/ndvi.py
Pydantic response schemas for the /ndvi endpoint.
"""

from pydantic import BaseModel, Field
from typing import Optional


class BoundingBox(BaseModel):
    min_lat: float
    min_lng: float
    max_lat: float
    max_lng: float


class MonthlyNDVI(BaseModel):
    """NDVI average for a single calendar month."""
    month: str = Field(..., description="Year-month string, e.g. '2025-03'")
    ndvi: float = Field(..., description="Mean NDVI for the month (0.0–1.0)")
    scenes: int = Field(..., description="Number of cloud-free scenes averaged")


class NDVIResponse(BaseModel):
    """Full NDVI analysis response returned by GET /ndvi."""

    lat: float = Field(..., description="Centroid latitude")
    lng: float = Field(..., description="Centroid longitude")
    bounding_box: BoundingBox

    current_ndvi: Optional[float] = Field(
        None,
        description="Most recent NDVI value from the latest clean scene",
    )
    two_year_mean: Optional[float] = Field(
        None,
        description="Mean NDVI across all clean scenes in the 24-month window",
    )
    monthly_trend: list[MonthlyNDVI] = Field(
        default_factory=list,
        description="Monthly NDVI averages, ordered chronologically",
    )
    status: str = Field(
        ...,
        description=(
            "Vegetation status label: Healthy, Degrading, Recovering, "
            "Low, or Unknown"
        ),
    )
    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence based on number of clean scenes (0.0–1.0)",
    )
    clean_scenes_found: int = Field(
        ...,
        description="Total number of cloud-free scenes used in the analysis",
    )
    trend_slope: Optional[float] = Field(
        None,
        description="Linear regression slope of monthly NDVI values",
    )
    data_source: str = Field(
        default="Microsoft Planetary Computer – Sentinel-2 L2A",
        description="Data source attribution",
    )
    message: Optional[str] = Field(
        None,
        description="Informational message (e.g. no-data explanation)",
    )
