"""
app/models/soil.py
Pydantic schemas for the /soil endpoint.
"""

from pydantic import BaseModel, Field
from typing import Optional


class SoilQueryParams(BaseModel):
    lat: float = Field(..., ge=-90, le=90, description="Latitude (-90 to 90)")
    lng: float = Field(..., ge=-180, le=180, description="Longitude (-180 to 180)")


class SoilResponse(BaseModel):
    """Structured soil data returned by GET /soil."""

    lat: float
    lng: float
    soil_type: str = Field(
        ..., description="USDA texture class derived from clay/sand/silt fractions"
    )
    ph: Optional[float] = Field(
        None, description="Soil pH in water (0–14) at 0–5 cm depth"
    )
    organic_carbon: Optional[float] = Field(
        None, description="Organic carbon density in g/dm³ at 0–5 cm depth"
    )
    texture: dict = Field(
        ...,
        description=(
            "Clay, sand, and silt percentages at 0–5 cm depth "
            "(source: SoilGrids g/kg ÷ 10)"
        ),
    )
    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Derived from mean/uncertainty ratio across all available layers. "
            "1.0 = highly confident, 0.0 = very uncertain."
        ),
    )
    depth_label: str = Field(default="0-5cm", description="Depth interval reported")
    data_source: str = Field(
        default="ISRIC SoilGrids v2.0",
        description="Upstream data provider",
    )
