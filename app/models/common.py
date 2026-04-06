"""
app/models/common.py
Shared Pydantic models / response schemas used across multiple routes.
"""

from pydantic import BaseModel
from typing import Any, Optional


class HealthResponse(BaseModel):
    status: str
    service: str
    timestamp: str


class ErrorResponse(BaseModel):
    detail: str
    code: Optional[str] = None


class APIResponse(BaseModel):
    """Generic wrapper for successful API responses."""

    success: bool = True
    data: Optional[Any] = None
    message: Optional[str] = None
