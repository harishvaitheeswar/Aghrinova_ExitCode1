"""
app/models/osm.py
Pydantic schemas for the /osm-proximity endpoint.
"""

from pydantic import BaseModel, Field
from typing import Optional


class BoundingBox(BaseModel):
    min_lat: float = Field(..., description="Southern boundary latitude")
    min_lng: float = Field(..., description="Western boundary longitude")
    max_lat: float = Field(..., description="Northern boundary latitude")
    max_lng: float = Field(..., description="Eastern boundary longitude")


class ProximityFeature(BaseModel):
    """Distance + metadata for a single nearest OSM feature."""

    found: bool = Field(..., description="Whether a matching feature was found in the search area")
    distance_m: Optional[float] = Field(
        None, description="Straight-line distance in metres from the centroid"
    )
    name: Optional[str] = Field(None, description="OSM name tag of the nearest feature, if any")
    feature_type: Optional[str] = Field(
        None, description="Specific OSM tag value (e.g. 'primary', 'river', 'town')"
    )


class AddressInfo(BaseModel):
    """Reverse-geocoded administrative hierarchy from OSM Nominatim."""

    town: Optional[str] = Field(
        None,
        description="Nearest named settlement (city / town / village / hamlet)",
    )
    taluk: Optional[str] = Field(
        None,
        description="Sub-district administrative unit (maps to Nominatim county / suburb)",
    )
    district: Optional[str] = Field(
        None,
        description="District / state-district level administrative unit",
    )
    state: Optional[str] = Field(None, description="State or province")
    country: Optional[str] = Field(None, description="Country name")
    display_name: Optional[str] = Field(
        None, description="Full human-readable address string from Nominatim"
    )


class OSMProximityResponse(BaseModel):
    """Structured response from GET /osm-proximity."""

    lat: float = Field(..., description="Query centroid latitude")
    lng: float = Field(..., description="Query centroid longitude")
    bounding_box: BoundingBox

    nearest_highway: ProximityFeature
    nearest_water: ProximityFeature
    nearest_town: ProximityFeature

    address: AddressInfo

    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Overall data completeness score (0.0–1.0). "
            "Computed from how many of the four data groups "
            "(highway, water, town proximity + Nominatim address) "
            "returned usable results."
        ),
    )
    data_sources: list[str] = Field(
        default_factory=list,
        description="Upstream APIs used to produce this response",
    )
