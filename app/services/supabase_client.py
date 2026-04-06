"""
app/services/supabase_client.py
Lazily-initialised Supabase client.
Credentials are fetched from the centralised settings object — never hardcoded.
"""

from supabase import create_client, Client
from app.config import settings

_client: Client | None = None


def get_supabase() -> Client:
    """Return a cached Supabase client, creating it on first call."""
    global _client
    if _client is None:
        _client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
    return _client
