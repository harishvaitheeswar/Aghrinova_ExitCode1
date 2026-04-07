"""
app/main.py
FastAPI application factory for the Landroid Land Intelligence Platform.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import health, soil, osm, ndvi, rainfall, temperature, health_score, canopy

# ── Application factory ───────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title="Landroid API",
        description=(
            "Land Intelligence Platform — satellite imagery analysis, "
            "parcel data, and geo-spatial insights powered by Supabase "
            "and Microsoft Planetary Computer."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Tighten in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(health.router)
    app.include_router(soil.router)
    app.include_router(osm.router)
    app.include_router(ndvi.router)
    app.include_router(rainfall.router)
    app.include_router(temperature.router)
    app.include_router(health_score.router)
    app.include_router(canopy.router)

    return app


app = create_app()

@app.get("/")
def root():
    return {"message": "Landroid API is running"}