from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import services as svc
from app.db import get_db
from app.deps import current_staff, staff_delivery
from app.models import Delivery, DeliveryStatus, LocationPing, Staff
from app.schemas import PingIn

router = APIRouter(prefix="/api")


def staff_delivery_out(d: Delivery) -> dict:
    return {
        "id": d.id,
        "customer_name": d.customer_name,
        "customer_phone": d.customer_phone,
        "address": d.address,
        "dest_lat": d.dest_lat,
        "dest_lng": d.dest_lng,
        "item_desc": d.item_desc,
        "status": d.status,
        "started_at": svc.iso(d.started_at),
        "arrived_at": svc.iso(d.arrived_at),
        "delivered_at": svc.iso(d.delivered_at),
    }


@router.get("/staff/me/deliveries")
def my_deliveries(staff: Staff = Depends(current_staff), db: Session = Depends(get_db)):
    items = svc.deliveries_for_day(db, svc.today_ist(), staff_id=staff.id)
    return {"staff_name": staff.name, "deliveries": [staff_delivery_out(d) for d in items]}


def _transition(delivery_id: int, action: str, staff: Staff, db: Session) -> dict:
    d = staff_delivery(delivery_id, staff, db)
    svc.apply_transition(db, d, action)
    db.commit()
    return staff_delivery_out(d)


@router.post("/deliveries/{delivery_id}/start")
def start(delivery_id: int, staff: Staff = Depends(current_staff), db: Session = Depends(get_db)):
    return _transition(delivery_id, "start", staff, db)


@router.post("/deliveries/{delivery_id}/arrive")
def arrive(delivery_id: int, staff: Staff = Depends(current_staff), db: Session = Depends(get_db)):
    return _transition(delivery_id, "arrive", staff, db)


@router.post("/deliveries/{delivery_id}/deliver")
def deliver(delivery_id: int, staff: Staff = Depends(current_staff), db: Session = Depends(get_db)):
    return _transition(delivery_id, "deliver", staff, db)


@router.post("/deliveries/{delivery_id}/ping")
def ping(
    delivery_id: int, body: PingIn, staff: Staff = Depends(current_staff), db: Session = Depends(get_db)
):
    d = staff_delivery(delivery_id, staff, db)
    if d.status != DeliveryStatus.on_the_way.value:
        # The client stops sharing location when it sees this.
        raise HTTPException(409, "Location sharing has stopped for this delivery")
    db.add(LocationPing(delivery_id=d.id, lat=body.lat, lng=body.lng))
    db.commit()
    return {"ok": True}
