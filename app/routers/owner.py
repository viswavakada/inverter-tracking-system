import secrets
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import services as svc
from app.config import settings
from app.db import get_db
from app.deps import SESSION_KEY, require_owner
from app.models import ACTIVE_STATUSES, FINAL_STATUSES, Delivery, DeliveryStatus, Staff, utcnow
from app.schemas import DeliveryIn, DeliveryPatch, LoginIn, StaffIn, StaffPatch

router = APIRouter(prefix="/api")
owner_only = [Depends(require_owner)]


# ---------------------------------------------------------------- serializers
def staff_out(s: Staff) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "phone": s.phone,
        "active": s.active,
        "link": f"{settings.BASE_URL}/s/{s.access_token}",
    }


def delivery_out(db: Session, d: Delivery, now=None) -> dict:
    """Owner view of a delivery."""
    ping = svc.last_ping(db, d.id) if d.status in ACTIVE_STATUSES else None
    eta, route = svc.route_and_eta(d, ping)
    return {
        "id": d.id,
        "customer_name": d.customer_name,
        "customer_phone": d.customer_phone,
        "address": d.address,
        "dest_lat": d.dest_lat,
        "dest_lng": d.dest_lng,
        "item_desc": d.item_desc,
        "staff_id": d.staff_id,
        "staff_name": d.staff.name if d.staff else None,
        "status": d.status,
        "track_url": f"{settings.BASE_URL}/track/{d.track_token}",
        "created_at": svc.iso(d.created_at),
        "started_at": svc.iso(d.started_at),
        "arrived_at": svc.iso(d.arrived_at),
        "delivered_at": svc.iso(d.delivered_at),
        "last_ping_at": svc.iso(ping.recorded_at) if ping else None,
        "last_lat": ping.lat if ping else None,
        "last_lng": ping.lng if ping else None,
        "eta_minutes": eta,
        "route": route,
        "flags": svc.delivery_flags(d, ping.recorded_at if ping else None, now),
    }


def get_active_staff(db: Session, staff_id: int) -> Staff:
    s = db.get(Staff, staff_id)
    if not s or not s.active:
        raise HTTPException(400, "Staff not found or inactive")
    return s


# ---------------------------------------------------------------- auth
@router.post("/owner/login")
def login(body: LoginIn, request: Request):
    if not secrets.compare_digest(body.password.encode(), settings.OWNER_PASSWORD.encode()):
        raise HTTPException(401, "Wrong password")
    request.session[SESSION_KEY] = True
    return {"ok": True}


@router.post("/owner/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/owner/me", dependencies=owner_only)
def owner_me():
    return {"ok": True}


# ---------------------------------------------------------------- deliveries
@router.post("/deliveries", dependencies=owner_only, status_code=201)
def create_delivery(body: DeliveryIn, db: Session = Depends(get_db)):
    get_active_staff(db, body.staff_id)
    d = Delivery(**body.model_dump())
    db.add(d)
    db.flush()
    svc.log_event(db, d.id, "created")
    db.commit()
    db.refresh(d)
    return delivery_out(db, d)


@router.get("/deliveries", dependencies=owner_only)
def list_deliveries(date_: str = Query("today", alias="date"), db: Session = Depends(get_db)):
    if date_ == "today":
        day = svc.today_ist()
    else:
        try:
            day = date.fromisoformat(date_)
        except ValueError:
            raise HTTPException(400, "date must be 'today' or YYYY-MM-DD")
    now = utcnow()
    return [delivery_out(db, d, now) for d in svc.deliveries_for_day(db, day)]


@router.patch("/deliveries/{delivery_id}", dependencies=owner_only)
def update_delivery(delivery_id: int, body: DeliveryPatch, db: Session = Depends(get_db)):
    d = db.get(Delivery, delivery_id)
    if not d:
        raise HTTPException(404, "Delivery not found")
    if d.status in FINAL_STATUSES:
        raise HTTPException(409, f"Delivery is already {d.status}")
    if body.status == "cancelled":
        svc.apply_transition(db, d, "cancel")
    elif body.staff_id is not None and body.staff_id != d.staff_id:
        get_active_staff(db, body.staff_id)
        d.staff_id = body.staff_id
        # The new person has not set off yet: back to 'assigned'.
        d.status = DeliveryStatus.assigned.value
        d.started_at = None
        d.arrived_at = None
        svc.log_event(db, d.id, "reassigned")
    db.commit()
    db.refresh(d)
    return delivery_out(db, d)


# ---------------------------------------------------------------- dashboard
@router.get("/dashboard", dependencies=owner_only)
def dashboard(db: Session = Depends(get_db)):
    now = utcnow()
    staff_list = list(db.scalars(select(Staff).where(Staff.active.is_(True)).order_by(Staff.name)))
    deliveries = svc.deliveries_for_day(db, svc.today_ist(now))
    rows = [delivery_out(db, d, now) for d in deliveries]

    staff_rows = []
    for s in staff_list:
        mine = [r for r in rows if r["staff_id"] == s.id]
        active = [r for r in mine if r["status"] in ACTIVE_STATUSES]
        current = active[0] if active else None
        staff_rows.append({
            "id": s.id,
            "name": s.name,
            "phone": s.phone,
            "status": current["status"] if current else "idle",
            "active_delivery": (
                {k: current[k] for k in ("id", "customer_name", "address", "status", "eta_minutes")}
                if current else None
            ),
            "last_ping_at": current["last_ping_at"] if current else None,
            "last_lat": current["last_lat"] if current else None,
            "last_lng": current["last_lng"] if current else None,
            "pending_count": sum(1 for r in mine if r["status"] == DeliveryStatus.assigned.value),
            "done_today": sum(1 for r in mine if r["status"] == DeliveryStatus.delivered.value),
            "flags": svc.staff_flags([r["flags"] for r in active]),
        })

    return {
        "now": svc.iso(now),
        "staff": staff_rows,
        "deliveries": rows,
        "counts": {
            st.value: sum(1 for r in rows if r["status"] == st.value) for st in DeliveryStatus
        },
    }


# ---------------------------------------------------------------- staff CRUD
@router.get("/staff", dependencies=owner_only)
def list_staff(include_inactive: bool = False, db: Session = Depends(get_db)):
    q = select(Staff).order_by(Staff.name)
    if not include_inactive:
        q = q.where(Staff.active.is_(True))
    return [staff_out(s) for s in db.scalars(q)]


@router.post("/staff", dependencies=owner_only, status_code=201)
def create_staff(body: StaffIn, db: Session = Depends(get_db)):
    s = Staff(**body.model_dump())
    db.add(s)
    db.commit()
    db.refresh(s)
    return staff_out(s)


@router.get("/staff/{staff_id}", dependencies=owner_only)
def get_staff(staff_id: int, db: Session = Depends(get_db)):
    s = db.get(Staff, staff_id)
    if not s:
        raise HTTPException(404, "Staff not found")
    return staff_out(s)


@router.patch("/staff/{staff_id}", dependencies=owner_only)
def update_staff(staff_id: int, body: StaffPatch, db: Session = Depends(get_db)):
    s = db.get(Staff, staff_id)
    if not s:
        raise HTTPException(404, "Staff not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return staff_out(s)


@router.delete("/staff/{staff_id}", dependencies=owner_only)
def delete_staff(staff_id: int, db: Session = Depends(get_db)):
    """Deactivates rather than deletes, so delivery history keeps its staff name.
    A deactivated staff link stops working immediately."""
    s = db.get(Staff, staff_id)
    if not s:
        raise HTTPException(404, "Staff not found")
    if svc.count_open_for_staff(db, s.id):
        raise HTTPException(409, "Reassign or cancel this person's open deliveries first")
    s.active = False
    db.commit()
    return {"ok": True}
