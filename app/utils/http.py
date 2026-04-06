"""
app/utils/http.py
Shared async HTTP client utilities (built on httpx).
Use get_http_client() inside request handlers for connection reuse.
"""

import httpx
from typing import AsyncGenerator
from contextlib import asynccontextmanager


@asynccontextmanager
async def get_http_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """
    Async context manager that yields a configured httpx.AsyncClient.

    Example usage:
        async with get_http_client() as client:
            response = await client.get("https://example.com")
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield client
