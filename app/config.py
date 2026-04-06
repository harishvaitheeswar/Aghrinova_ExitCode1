"""
app/config.py
Centralised settings loaded from environment variables via python-dotenv.
No values are hardcoded here – all secrets live in .env (git-ignored).
"""

import os
from dotenv import load_dotenv

# Load variables from .env into the process environment
load_dotenv()


class Settings:
    """Application-wide settings resolved from environment variables."""

    # ── Supabase ──────────────────────────────────────────────────────────────
    SUPABASE_URL: str = os.environ["SUPABASE_URL"]
    SUPABASE_KEY: str = os.environ["SUPABASE_KEY"]

    # ── Microsoft Planetary Computer ─────────────────────────────────────────

    # ── App meta ──────────────────────────────────────────────────────────────
    APP_NAME: str = os.environ.get("APP_NAME", "Landroid")
    APP_VERSION: str = os.environ.get("APP_VERSION", "0.1.0")
    DEBUG: bool = os.environ.get("DEBUG", "false").lower() == "true"


# Singleton exposed for import throughout the app
settings = Settings()
