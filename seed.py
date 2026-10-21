"""Create 3 staff and 5 sample deliveries (Chennai-area coordinates).

Usage:
    python seed.py           add sample data to the existing database
    python seed.py --reset   drop all tables first, then add sample data
"""
import sys

from app.config import settings
from app.db import Base, SessionLocal, engine, init_db
from app.models import Delivery, DeliveryEvent, Staff

STAFF = [
    ("Ravi Kumar", "+919800000001"),
    ("Suresh Babu", "+919800000002"),
    ("Arun Prakash", "+919800000003"),
]

# customer, phone, address, lat, lng, item, staff index
DELIVERIES = [
    ("Lakshmi N", "+919811111101", "12 Gandhi St, T. Nagar, Chennai", 13.0418, 80.2341, "Luminous 1100VA inverter", 0),
    ("Mohammed I", "+919811111102", "45 Anna Salai, Teynampet, Chennai", 13.0405, 80.2504, "Exide 150Ah tubular battery", 0),
    ("Priya S", "+919811111103", "8 Velachery Main Rd, Velachery, Chennai", 12.9815, 80.2180, "Microtek 900VA + battery", 1),
    ("Karthik R", "+919811111104", "22 2nd Ave, Anna Nagar, Chennai", 13.0850, 80.2101, "Amaron 100Ah battery", 1),
    ("Deepa V", "+919811111105", "5 Beach Rd, Besant Nagar, Chennai", 13.0002, 80.2668, "Su-Kam 1500VA inverter", 2),
]


def main():
    if "--reset" in sys.argv:
        from app import models  # noqa: F401

        Base.metadata.drop_all(bind=engine)
    init_db()
    db = SessionLocal()
    try:
        staff = [Staff(name=n, phone=p) for n, p in STAFF]
        db.add_all(staff)
        db.flush()
        deliveries = []
        for cust, phone, addr, lat, lng, item, si in DELIVERIES:
            d = Delivery(customer_name=cust, customer_phone=phone, address=addr,
                         dest_lat=lat, dest_lng=lng, item_desc=item, staff_id=staff[si].id)
            db.add(d)
            db.flush()
            db.add(DeliveryEvent(delivery_id=d.id, event="created"))
            deliveries.append(d)
        db.commit()

        print("Staff links (open on each staff member's phone):")
        for s in staff:
            print(f"  {s.name:<14} {settings.BASE_URL}/s/{s.access_token}")
        print("\nCustomer tracking links:")
        for d in deliveries:
            print(f"  #{d.id} {d.customer_name:<12} {settings.BASE_URL}/track/{d.track_token}")
        print(f"\nOwner dashboard: {settings.BASE_URL}/owner")
    finally:
        db.close()


if __name__ == "__main__":
    main()
