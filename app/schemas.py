from typing import Literal

from pydantic import BaseModel, Field, field_validator

PHONE_PATTERN = r"^\+?[0-9 \-]{7,20}$"


class LoginIn(BaseModel):
    password: str


class StaffIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    phone: str = Field(pattern=PHONE_PATTERN)
    active: bool = True


class StaffPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, pattern=PHONE_PATTERN)
    active: bool | None = None


class DeliveryIn(BaseModel):
    customer_name: str = Field(min_length=1, max_length=100)
    customer_phone: str = Field(pattern=PHONE_PATTERN)
    address: str = Field(min_length=1, max_length=500)
    dest_lat: float = Field(ge=-90, le=90)
    dest_lng: float = Field(ge=-180, le=180)
    item_desc: str = Field(default="", max_length=500)
    staff_id: int

    @field_validator("customer_name", "address", "item_desc")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


class DeliveryPatch(BaseModel):
    staff_id: int | None = None
    status: Literal["cancelled"] | None = None


class PingIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
