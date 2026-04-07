"""
app/models/canopy.py
Pydantic schemas for the POST /canopy-count endpoint.
"""

from pydantic import BaseModel, Field
from typing import Optional


class CanopyResponse(BaseModel):
    """Structured canopy detection results returned by POST /canopy-count."""

    total_canopy_count: int = Field(
        ..., description="Total number of detected tree canopies"
    )
    density_per_acre: float = Field(
        ...,
        description=(
            "Canopy count divided by the approximate image area in acres, "
            "derived from GeoTIFF georeferencing metadata"
        ),
    )
    mean_canopy_area_px: float = Field(
        ...,
        description="Average detected canopy size in pixels",
    )
    stressed_canopy_count: int = Field(
        ...,
        description=(
            "Number of canopies whose area is more than 1.5 standard deviations "
            "below the mean — flagged as potentially stressed per the SRS"
        ),
    )
    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Detection confidence (0–1). Higher when detected blobs have "
            "consistent sizes; lower when highly variable."
        ),
    )
    detection_method: str = Field(
        ...,
        description="Detection algorithm used: 'blob' or 'watershed'",
    )
    data_source: str = Field(
        default="Birdscale Orthomosaic — OpenCV Detection",
        description="Upstream data source and processing pipeline",
    )
    message: Optional[str] = Field(
        default=None,
        description="Error or informational message (null on success)",
    )
