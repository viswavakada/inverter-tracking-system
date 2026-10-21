import enum
import secrets
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


class UTCDateTime(TypeDecorator):
    """Stores naive UTC in the DB and always returns timezone-aware UTC datetimes.

    SQLite drops tzinfo, so values are normalised on the way in and out.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


class DeliveryStatus(str, enum.Enum):
    assigned = "assigned"
    on_the_way = "on_the_way"
    arrived = "arrived"
    delivered = "delivered"
    cancelled = "cancelled"


ACTIVE_STATUSES = (DeliveryStatus.on_the_way.value, DeliveryStatus.arrived.value)
FINAL_STATUSES = (DeliveryStatus.delivered.value, DeliveryStatus.cancelled.value)


class Staff(Base):
    __tablename__ = "staff"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str] = mapped_column(String(20))
    access_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, default=new_token)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    deliveries: Mapped[list["Delivery"]] = relationship(back_populates="staff")


class Delivery(Base):
    __tablename__ = "deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_name: Mapped[str] = mapped_column(String(100))
    customer_phone: Mapped[str] = mapped_column(String(20))
    address: Mapped[str] = mapped_column(Text)
    dest_lat: Mapped[float] = mapped_column(Float)
    dest_lng: Mapped[float] = mapped_column(Float)
    item_desc: Mapped[str] = mapped_column(Text, default="")
    staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default=DeliveryStatus.assigned.value, index=True)
    track_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, default=new_token)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    arrived_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    staff: Mapped[Staff | None] = relationship(back_populates="deliveries")
    pings: Mapped[list["LocationPing"]] = relationship(
        back_populates="delivery", order_by="LocationPing.recorded_at", cascade="all, delete-orphan"
    )
    events: Mapped[list["DeliveryEvent"]] = relationship(
        back_populates="delivery", order_by="DeliveryEvent.created_at", cascade="all, delete-orphan"
    )


class LocationPing(Base):
    __tablename__ = "location_pings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey("deliveries.id"), index=True)
    lat: Mapped[float] = mapped_column(Float)
    lng: Mapped[float] = mapped_column(Float)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)

    delivery: Mapped[Delivery] = relationship(back_populates="pings")


class DeliveryEvent(Base):
    __tablename__ = "delivery_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    delivery_id: Mapped[int] = mapped_column(ForeignKey("deliveries.id"), index=True)
    event: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    delivery: Mapped[Delivery] = relationship(back_populates="events")
