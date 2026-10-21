"""Road routes from an OSRM server, with a straight-line fallback.

The public OSRM demo server is free and needs no key, but it is rate-limited and has
no uptime guarantee. For production, point ROUTING_URL at your own OSRM instance or
replace `fetch_route` with a paid routing API.
"""
import logging
import threading
from dataclasses import dataclass

import httpx

from app.config import settings

log = logging.getLogger("delivery")


@dataclass
class Route:
    coords: list[list[float]]  # [[lat, lng], ...] from start to destination
    distance_km: float


def fetch_route(from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> Route | None:
    """Driving route from OSRM, or None if routing is off or the server fails."""
    if not settings.ROUTING_URL:
        return None
    url = (f"{settings.ROUTING_URL}/route/v1/driving/"
           f"{from_lng:.6f},{from_lat:.6f};{to_lng:.6f},{to_lat:.6f}")
    try:
        r = httpx.get(url, params={"overview": "simplified", "geometries": "geojson"},
                      timeout=settings.ROUTING_TIMEOUT_SEC,
                      headers={"User-Agent": "delivery-tracking-mvp"})
        r.raise_for_status()
        data = r.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            return None
        best = data["routes"][0]
        coords = [[round(lat, 6), round(lng, 6)] for lng, lat in best["geometry"]["coordinates"]]
        return Route(coords=coords, distance_km=best["distance"] / 1000)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.warning("Routing failed, using straight line: %s", type(exc).__name__)
        return None


class RouteCache:
    """Keeps one route per delivery, recomputed only when a new GPS ping arrives.

    Pages poll every 5 seconds but pings come every 20, so this avoids calling the
    routing server on every page refresh.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._items: dict[int, tuple[int, Route | None]] = {}

    def get(self, delivery_id: int, ping_id: int, from_lat, from_lng, to_lat, to_lng) -> Route | None:
        with self._lock:
            hit = self._items.get(delivery_id)
        if hit and hit[0] == ping_id:
            return hit[1]
        route = fetch_route(from_lat, from_lng, to_lat, to_lng)
        with self._lock:
            self._items[delivery_id] = (ping_id, route)
            if len(self._items) > 500:
                self._items.pop(next(iter(self._items)))
        return route

    def clear(self):
        with self._lock:
            self._items.clear()


route_cache = RouteCache()
