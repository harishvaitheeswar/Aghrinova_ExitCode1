"""
app/models/temperature.py
Pydantic response schemas for the /temperature endpoint.
Data source: TerraClimate (tmax) via Microsoft Planetary Computer Zarr.

NOTE: TerraClimate operates at ~4 km grid resolution,
so all temperature outputs represent **regional-level** precision,
not parcel-level measurements.
"""

from pydantic import BaseModel, Field
from typing import Optional


class MonthlyTemperature(BaseModel):
    """Temperature average for a single calendar month."""

    month: str = Field(..., description="Year-month string, e.g. '2025-03'")
    mean_temp_c: Optional[float] = Field(
        None,
        description="Mean temperature in °C for the month",
    )


class TemperatureResponse(BaseModel):
    """Full temperature analysis response returned by GET /temperature."""

    lat: float = Field(..., description="Centroid latitude (for reference)")
    lng: float = Field(..., description="Centroid longitude (for reference)")

    monthly_trend: list[MonthlyTemperature] = Field(
        default_factory=list,
        description=(
            "Monthly mean temperature values, ordered chronologically. "
            "Precision: regional-level (~0.25° / ~28 km grid), NOT parcel-level."
        ),
    )
    heat_stress_event_count: int = Field(
        ...,
        ge=0,
        description=(
            "Number of months where the mean temperature exceeded 35 °C, "
            "indicating potential heat-stress conditions."
        ),
    )
    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence based on the number of monthly data points found (0.0–1.0)",
    )
    precision_label: str = Field(
        default="regional-level (~4 km grid)",
        description=(
            "Indicates that temperature values are regional estimates, "
            "not parcel-level measurements."
        ),
    )
    data_source: str = Field(
        default="Microsoft Planetary Computer – TerraClimate (tmax)",
        description="Data source attribution",
    )
    message: Optional[str] = Field(
        None,
        description="Informational message (e.g. no-data explanation)",
    )
