from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Delivery, Staff

SESSION_KEY = "owner"


def require_owner(request: Request) -> None:
    if not request.session.get(SESSION_KEY):
        raise HTTPException(401, "Login required")


def current_staff(
    x_staff_token: str | None = Header(default=None), db: Session = Depends(get_db)
) -> Staff:
    """Staff are identified by the access token from their /s/<token> link,
    sent by the page in the X-Staff-Token header (headers are not written to access logs)."""
    if not x_staff_token:
        raise HTTPException(401, "Invalid staff link")
    staff = db.scalars(select(Staff).where(Staff.access_token == x_staff_token)).first()
    if not staff or not staff.active:
        raise HTTPException(401, "Invalid staff link")
    return staff


def staff_delivery(delivery_id: int, staff: Staff, db: Session) -> Delivery:
    d = db.get(Delivery, delivery_id)
    if not d or d.staff_id != staff.id:
        raise HTTPException(404, "Delivery not found")
    return d
