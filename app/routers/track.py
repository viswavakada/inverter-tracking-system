from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import services as svc
from app.db import get_db
from app.models import FINAL_STATUSES, Delivery, DeliveryStatus

router = APIRouter(prefix="/api")

STATUS_TEXT = {
    "assigned": "Your order is being prepared for delivery",
    "on_the_way": "On the way",
    "arrived": "The delivery person has arrived",
    "delivered": "Delivered",
    "cancelled": "This delivery was cancelled",
}


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.get("/track/{track_token}")
def track(track_token: str, request: Request, db: Session = Depends(get_db)):
    if not svc.track_limiter.allow(client_ip(request)):
        raise HTTPException(429, "Too many requests, please wait a minute")
    d = db.scalars(select(Delivery).where(Delivery.track_token == track_token)).first()
    if not d:
        raise HTTPException(404, "Tracking link not found")

    # Location is only shared while the delivery is in progress.
    ping = svc.last_ping(db, d.id) if d.status not in FINAL_STATUSES else None
    eta, route = svc.route_and_eta(d, ping)

    status_text = STATUS_TEXT[d.status]
    if eta is not None:
        status_text = f"On the way, about {eta} min away"

    staff = d.staff
    return {
        "status": d.status,
        "status_text": status_text,
        "item_desc": d.item_desc,
        "address": d.address,
        "destination": {"lat": d.dest_lat, "lng": d.dest_lng},
        "staff": (
            {"first_name": staff.name.split()[0], "phone": staff.phone}
            if staff and d.status not in FINAL_STATUSES else None
        ),
        "last_position": (
            {"lat": ping.lat, "lng": ping.lng, "recorded_at": svc.iso(ping.recorded_at)} if ping else None
        ),
        "eta_minutes": eta,
        "route": route,
        "timestamps": {
            "created_at": svc.iso(d.created_at),
            "started_at": svc.iso(d.started_at),
            "arrived_at": svc.iso(d.arrived_at),
            "delivered_at": svc.iso(d.delivered_at),
        },
        "delivered_at_local": svc.fmt_ist_hhmm(d.delivered_at),
    }
