"""
app/routes/health.py
Health-check endpoint — confirms the API is running.
"""

from fastapi import APIRouter
from datetime import datetime, timezone

router = APIRouter(tags=["Health"])


@router.get("/health", summary="Health check")
async def health_check() -> dict:
    """
    Returns a simple status payload so load-balancers and CI pipelines
    can verify the service is alive.
    """
    return {
        "status": "ok",
        "service": "Landroid API",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
