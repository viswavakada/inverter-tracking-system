"""Business logic shared by the routers: ETA, alert flags, status transitions, cleanup."""
import math
import re
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.routing import route_cache
from app.models import (
    ACTIVE_STATUSES,
    FINAL_STATUSES,
    Delivery,
    DeliveryEvent,
    DeliveryStatus,
    LocationPing,
    utcnow,
)

# India has no daylight saving, so a fixed offset is exact and needs no tz database.
IST = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")

AVG_SPEED_KMH = 25.0


# ---------------------------------------------------------------- time helpers
def to_ist(dt: datetime | None) -> datetime | None:
    return dt.astimezone(IST) if dt else None


def fmt_ist_hhmm(dt: datetime | None) -> str | None:
    return to_ist(dt).strftime("%H:%M") if dt else None


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def ist_day_range_utc(day: date) -> tuple[datetime, datetime]:
    """UTC start/end of a calendar day in Asia/Kolkata."""
    start = datetime(day.year, day.month, day.day, tzinfo=IST)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def today_ist(now: datetime | None = None) -> date:
    return (now or utcnow()).astimezone(IST).date()


# ---------------------------------------------------------------- ETA
def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def eta_from_km(km: float) -> int:
    """Minutes at the assumed average speed, rounded up to the nearest 5 (minimum 5)."""
    minutes = km / AVG_SPEED_KMH * 60
    return max(5, int(math.ceil(minutes / 5.0)) * 5)


def estimate_eta_minutes(from_lat: float, from_lng: float, to_lat: float, to_lng: float) -> int:
    """Straight-line ETA, used when no road route is available."""
    return eta_from_km(haversine_km(from_lat, from_lng, to_lat, to_lng))


def route_and_eta(d: Delivery, ping: LocationPing | None) -> tuple[int | None, list[list[float]] | None]:
    """ETA and road route from the staff member's last position to the customer.

    Only for deliveries that are on the way. Uses the road distance when the routing
    server answers, otherwise the straight-line distance and no route line.
    """
    if d.status != DeliveryStatus.on_the_way.value or not ping:
        return None, None
    route = route_cache.get(d.id, ping.id, ping.lat, ping.lng, d.dest_lat, d.dest_lng)
    if route:
        return eta_from_km(route.distance_km), route.coords
    return estimate_eta_minutes(ping.lat, ping.lng, d.dest_lat, d.dest_lng), None


# ---------------------------------------------------------------- queries
def last_ping(db: Session, delivery_id: int) -> LocationPing | None:
    return db.scalars(
        select(LocationPing)
        .where(LocationPing.delivery_id == delivery_id)
        .order_by(LocationPing.recorded_at.desc(), LocationPing.id.desc())
        .limit(1)
    ).first()


def deliveries_for_day(db: Session, day: date, staff_id: int | None = None) -> list[Delivery]:
    """Deliveries created on `day` (IST), plus any older ones that are still open."""
    start, end = ist_day_range_utc(day)
    q = select(Delivery).where(
        or_(
            (Delivery.created_at >= start) & (Delivery.created_at < end),
            Delivery.status.not_in(FINAL_STATUSES) & (Delivery.created_at < end),
        )
    )
    if staff_id is not None:
        q = q.where(Delivery.staff_id == staff_id)
    return list(db.scalars(q.order_by(Delivery.created_at, Delivery.id)))


# ---------------------------------------------------------------- flags
def delivery_flags(d: Delivery, last_ping_at: datetime | None, now: datetime | None = None) -> list[str]:
    """'delayed' and 'signal_lost' flags for one delivery."""
    now = now or utcnow()
    flags = []
    if d.status == DeliveryStatus.on_the_way.value and d.started_at:
        if now - d.started_at > timedelta(minutes=settings.DELAY_ON_THE_WAY_MIN):
            flags.append("delayed")
        reference = last_ping_at or d.started_at
        if now - reference >= timedelta(minutes=settings.SIGNAL_LOST_MIN):
            flags.append("signal_lost")
    elif d.status == DeliveryStatus.arrived.value and d.arrived_at:
        if now - d.arrived_at > timedelta(minutes=settings.DELAY_ARRIVED_MIN):
            flags.append("delayed")
    return flags


def staff_flags(active_deliveries_flags: list[list[str]]) -> list[str]:
    """Staff is 'idle' with no on_the_way/arrived delivery, else inherits delivery flags."""
    if not active_deliveries_flags:
        return ["idle"]
    merged: list[str] = []
    for fl in active_deliveries_flags:
        for f in fl:
            if f not in merged:
                merged.append(f)
    return merged


# ---------------------------------------------------------------- transitions
TRANSITIONS = {
    "start": ({DeliveryStatus.assigned.value}, DeliveryStatus.on_the_way.value),
    "arrive": ({DeliveryStatus.on_the_way.value}, DeliveryStatus.arrived.value),
    "deliver": ({DeliveryStatus.on_the_way.value, DeliveryStatus.arrived.value}, DeliveryStatus.delivered.value),
    "cancel": (
        {DeliveryStatus.assigned.value, DeliveryStatus.on_the_way.value, DeliveryStatus.arrived.value},
        DeliveryStatus.cancelled.value,
    ),
}


def apply_transition(db: Session, d: Delivery, action: str) -> Delivery:
    allowed_from, target = TRANSITIONS[action]
    if d.status not in allowed_from:
        raise HTTPException(409, f"Cannot {action} a delivery that is {d.status}")
    now = utcnow()
    d.status = target
    if target == DeliveryStatus.on_the_way.value:
        d.started_at = now
    elif target == DeliveryStatus.arrived.value:
        d.arrived_at = now
    elif target == DeliveryStatus.delivered.value:
        d.arrived_at = d.arrived_at or now
        d.delivered_at = now
    db.add(DeliveryEvent(delivery_id=d.id, event=target))
    return d


def log_event(db: Session, delivery_id: int, event: str) -> None:
    db.add(DeliveryEvent(delivery_id=delivery_id, event=event))


# ---------------------------------------------------------------- cleanup
def cleanup_old_pings(db: Session, now: datetime | None = None) -> int:
    cutoff = (now or utcnow()) - timedelta(days=settings.PING_RETENTION_DAYS)
    result = db.execute(delete(LocationPing).where(LocationPing.recorded_at < cutoff))
    db.commit()
    return result.rowcount or 0


# ---------------------------------------------------------------- rate limit
class RateLimiter:
    """Small in-memory sliding-window limiter, per key. Fine for a single process."""

    def __init__(self, limit: int, window_sec: int):
        self.limit = limit
        self.window = window_sec
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        if len(self.hits) > 10000:  # drop idle keys so memory stays bounded
            for k in [k for k, v in self.hits.items() if not v]:
                del self.hits[k]
        return True


track_limiter = RateLimiter(settings.TRACK_RATE_LIMIT, settings.TRACK_RATE_WINDOW_SEC)


# ---------------------------------------------------------------- token redaction
_TOKEN_PATH = re.compile(r"(/(?:s|track|api/track)/)[A-Za-z0-9_\-]+")


def redact(text: str) -> str:
    """Replace tokens in URL paths so they never reach the logs."""
    return _TOKEN_PATH.sub(r"\1<redacted>", text)


def count_open_for_staff(db: Session, staff_id: int) -> int:
    return db.scalar(
        select(func.count(Delivery.id)).where(
            Delivery.staff_id == staff_id, Delivery.status.not_in(FINAL_STATUSES)
        )
    ) or 0


