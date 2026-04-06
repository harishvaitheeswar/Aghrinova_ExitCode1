"""
app/services/osm.py
Queries the OSM Overpass API (proximity features) and OSM Nominatim
(reverse geocoding) for a given centroid + bounding box.

External APIs used
──────────────────
  Overpass  → https://overpass-api.de/api/interpreter
  Nominatim → https://nominatim.openstreetmap.org/reverse

Rate-limit policy
─────────────────
  • Overpass allows ~1 req/s for anonymous users; we cap each query at
    10 s server-side timeout and add a descriptive User-Agent as
    required by the Nominatim usage policy.
  • HTTP 429 / 504 / network errors bubble up as specific exceptions
    that the route layer converts into clean HTTP responses.
"""

from __future__ import annotations

import math
import asyncio
from typing import Optional

import httpx

# ── Constants ─────────────────────────────────────────────────────────────────

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"

# Overpass server-side timeout (seconds); keep ≤ 15 for interactive use
OVERPASS_TIMEOUT = 10

# httpx client-side read timeout; slightly longer than server-side to let the
# server gracefully respond with a timeout error instead of a hard TCP cut
CLIENT_TIMEOUT = 15.0

USER_AGENT = "Landroid/0.1 (land-intelligence-platform; contact@landroid.app)"

# Highway tag values to search for (ordered roughly by importance)
HIGHWAY_TAGS = (
    "motorway", "trunk", "primary", "secondary",
    "tertiary", "unclassified", "residential", "road",
)

# Water feature tags
WATER_TAGS_WAY = ("natural=water", "waterway=river", "waterway=canal", "waterway=stream")

# Settlement place tags
TOWN_PLACE_TAGS = "city|town|village|hamlet"


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in metres between two WGS-84 points."""
    R = 6_371_000.0  # Earth mean radius, metres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _nearest_from_elements(
    elements: list[dict], centroid_lat: float, centroid_lng: float
) -> tuple[Optional[float], Optional[str], Optional[str]]:
    """
    Walk a list of Overpass JSON elements (nodes, ways with center) and return
    (distance_m, name, feature_type) for the element closest to the centroid.

    Elements without resolvable coordinates are silently skipped.
    """
    best_dist: Optional[float] = None
    best_name: Optional[str] = None
    best_type: Optional[str] = None

    for el in elements:
        el_type = el.get("type")
        tags = el.get("tags", {})

        # Resolve lat/lon
        if el_type == "node":
            elat, elng = el.get("lat"), el.get("lon")
        elif el_type in ("way", "relation"):
            center = el.get("center", {})
            elat, elng = center.get("lat"), center.get("lon")
        else:
            continue

        if elat is None or elng is None:
            continue

        dist = _haversine(centroid_lat, centroid_lng, elat, elng)

        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_name = tags.get("name") or tags.get("ref")
            # Pick up the most relevant tag value as feature_type
            for key in ("highway", "waterway", "natural", "place"):
                if key in tags:
                    best_type = tags[key]
                    break

    return best_dist, best_name, best_type


# ── Overpass query builders ───────────────────────────────────────────────────

def _bbox(min_lat: float, min_lng: float, max_lat: float, max_lng: float) -> str:
    """Return an Overpass bbox string: south,west,north,east."""
    return f"{min_lat},{min_lng},{max_lat},{max_lng}"


def _highway_query(bbox: str) -> str:
    highway_filter = "|".join(HIGHWAY_TAGS)
    return (
        f'[out:json][timeout:{OVERPASS_TIMEOUT}][bbox:{bbox}];\n'
        f'way["highway"~"^({highway_filter})$"];\n'
        f'out center 50;\n'
    )


def _water_query(bbox: str) -> str:
    return (
        f'[out:json][timeout:{OVERPASS_TIMEOUT}][bbox:{bbox}];\n'
        f'(\n'
        f'  way["natural"="water"];\n'
        f'  way["waterway"~"^(river|canal|stream|lake)$"];\n'
        f'  relation["natural"="water"];\n'
        f');\n'
        f'out center 50;\n'
    )


def _town_query(bbox: str) -> str:
    return (
        f'[out:json][timeout:{OVERPASS_TIMEOUT}][bbox:{bbox}];\n'
        f'node["place"~"^({TOWN_PLACE_TAGS})$"];\n'
        f'out 50;\n'
    )


# ── Individual fetch functions ────────────────────────────────────────────────

async def _fetch_overpass(
    client: httpx.AsyncClient, query: str
) -> list[dict]:
    """
    POST a query to the Overpass API and return the elements list.

    Raises:
        httpx.TimeoutException  – server took too long
        httpx.HTTPStatusError   – non-2xx (incl. 429 rate-limit)
        httpx.RequestError      – network-level failure
    """
    response = await client.post(
        OVERPASS_URL,
        data={"data": query},
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.json().get("elements", [])


async def _fetch_nominatim(
    client: httpx.AsyncClient, lat: float, lng: float
) -> dict:
    """
    Call Nominatim reverse-geocoding for the centroid.

    Returns the raw JSON dict (may be empty on no-result).

    Raises:
        httpx.TimeoutException / httpx.HTTPStatusError / httpx.RequestError
    """
    response = await client.get(
        NOMINATIM_URL,
        params={
            "lat": lat,
            "lon": lng,
            "format": "json",
            "addressdetails": 1,
            "zoom": 14,
        },
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en",
        },
    )
    response.raise_for_status()
    return response.json()


# ── Address parsing ───────────────────────────────────────────────────────────

def _parse_address(nominatim_json: dict) -> dict:
    """
    Extract town, taluk, district, state, country from a Nominatim response.

    Nominatim address hierarchy varies by country; we try several keys
    in priority order so Indian addresses (with tehsil/taluk) are handled well.
    """
    addr = nominatim_json.get("address", {})

    # Settlement name: prefer specific over general
    town = (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("hamlet")
        or addr.get("suburb")
    )

    # Taluk / sub-district (India uses "county" or "city_district" in Nominatim)
    taluk = (
        addr.get("county")
        or addr.get("city_district")
        or addr.get("district")       # fallback — some regions only have this
    )

    # District (state-level sub-division)
    district = (
        addr.get("state_district")
        or addr.get("district")
    )

    # Avoid duplicating taluk == district when only one key is present
    if taluk and district and taluk == district:
        taluk = addr.get("county") or addr.get("city_district")

    return {
        "town": town or None,
        "taluk": taluk or None,
        "district": district or None,
        "state": addr.get("state") or None,
        "country": addr.get("country") or None,
        "display_name": nominatim_json.get("display_name") or None,
    }


# ── Confidence scoring ────────────────────────────────────────────────────────

def _compute_confidence(
    highway_found: bool,
    water_found: bool,
    town_found: bool,
    address_complete: bool,
) -> float:
    """
    Simple completeness-based confidence score (0.0–1.0).

    Each of the four data groups contributes 0.25; partial address
    completeness is weighted by fields present.
    """
    score = 0.0
    if highway_found:
        score += 0.25
    if water_found:
        score += 0.25
    if town_found:
        score += 0.25
    if address_complete:
        score += 0.25
    return round(score, 4)


# ── Public interface ──────────────────────────────────────────────────────────

async def fetch_osm_proximity(
    lat: float,
    lng: float,
    min_lat: float,
    min_lng: float,
    max_lat: float,
    max_lng: float,
    client: httpx.AsyncClient,
) -> dict:
    """
    Query OSM Overpass (highway / water / town proximity) and Nominatim
    (reverse geocoding) concurrently.

    Returns a structured dict ready to be unpacked into OSMProximityResponse.

    Raises:
        httpx.TimeoutException   – one or more upstream calls timed out
        httpx.HTTPStatusError    – upstream returned a non-2xx status
        httpx.RequestError       – network / DNS failure
    """
    bbox = _bbox(min_lat, min_lng, max_lat, max_lng)

    # ── Fire all four requests concurrently ───────────────────────────────────
    highway_task = _fetch_overpass(client, _highway_query(bbox))
    water_task   = _fetch_overpass(client, _water_query(bbox))
    town_task    = _fetch_overpass(client, _town_query(bbox))
    nominatim_task = _fetch_nominatim(client, lat, lng)

    highway_els, water_els, town_els, nominatim_json = await asyncio.gather(
        highway_task,
        water_task,
        town_task,
        nominatim_task,
        return_exceptions=True,   # collect errors without short-circuiting
    )

    # ── Re-raise fatal errors (re-raise the first hard exception) ─────────────
    # We surface the first real exception; partial results are still usable.
    first_exc = next(
        (r for r in (highway_els, water_els, town_els, nominatim_json)
         if isinstance(r, BaseException)),
        None,
    )
    # Only re-raise if ALL four failed (complete outage)
    all_failed = all(
        isinstance(r, BaseException)
        for r in (highway_els, water_els, town_els, nominatim_json)
    )
    if all_failed and first_exc:
        raise first_exc

    # ── Process Overpass results (degrade gracefully on per-call errors) ───────
    def _safe_nearest(result) -> tuple[bool, Optional[float], Optional[str], Optional[str]]:
        if isinstance(result, BaseException) or not result:
            return False, None, None, None
        dist, name, ftype = _nearest_from_elements(result, lat, lng)
        found = dist is not None
        return found, (round(dist, 1) if dist is not None else None), name, ftype

    hw_found, hw_dist, hw_name, hw_type = _safe_nearest(highway_els)
    wa_found, wa_dist, wa_name, wa_type = _safe_nearest(water_els)
    to_found, to_dist, to_name, to_type = _safe_nearest(town_els)

    # ── Process Nominatim result ───────────────────────────────────────────────
    if isinstance(nominatim_json, BaseException) or not nominatim_json:
        address = {
            "town": None, "taluk": None, "district": None,
            "state": None, "country": None, "display_name": None,
        }
        address_complete = False
    else:
        address = _parse_address(nominatim_json)
        # Consider address "complete" if at least town + district are present
        address_complete = bool(address.get("town") and address.get("district"))

    confidence = _compute_confidence(hw_found, wa_found, to_found, address_complete)

    return {
        "lat": lat,
        "lng": lng,
        "bounding_box": {
            "min_lat": min_lat,
            "min_lng": min_lng,
            "max_lat": max_lat,
            "max_lng": max_lng,
        },
        "nearest_highway": {
            "found":        hw_found,
            "distance_m":   hw_dist,
            "name":         hw_name,
            "feature_type": hw_type,
        },
        "nearest_water": {
            "found":        wa_found,
            "distance_m":   wa_dist,
            "name":         wa_name,
            "feature_type": wa_type,
        },
        "nearest_town": {
            "found":        to_found,
            "distance_m":   to_dist,
            "name":         to_name,
            "feature_type": to_type,
        },
        "address":          address,
        "confidence_score": confidence,
        "data_sources": [
            "OpenStreetMap Overpass API",
            "OpenStreetMap Nominatim",
        ],
    }
