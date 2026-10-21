import os
import tempfile
from pathlib import Path

# Point the app at a throwaway SQLite file before any app module is imported.
_tmpdir = tempfile.mkdtemp(prefix="delivery-tests-")
# Set TEST_DATABASE_URL to run the suite against PostgreSQL instead. Its tables are dropped.
os.environ["DATABASE_URL"] = os.getenv("TEST_DATABASE_URL") or f"sqlite:///{Path(_tmpdir) / 'test.db'}"
os.environ["OWNER_PASSWORD"] = "test-pass"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["BASE_URL"] = "http://testserver"
os.environ["ROUTING_URL"] = ""  # tests never call the internet

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import services as svc  # noqa: E402
from app.db import Base, SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Staff  # noqa: E402
from app.routing import route_cache  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    init_db()
    svc.track_limiter.hits.clear()
    route_cache.clear()
    yield


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def owner():
    c = TestClient(app)
    r = c.post("/api/owner/login", json={"password": "test-pass"})
    assert r.status_code == 200
    return c


@pytest.fixture
def anon():
    return TestClient(app)


@pytest.fixture
def staff_member(db):
    s = Staff(name="Ravi Kumar", phone="+919800000001")
    db.add(s)
    db.commit()
    return s


@pytest.fixture
def other_staff(db):
    s = Staff(name="Suresh Babu", phone="+919800000002")
    db.add(s)
    db.commit()
    return s


def make_delivery(owner, staff_id, **over):
    body = {
        "customer_name": "Lakshmi N",
        "customer_phone": "+919811111101",
        "address": "12 Gandhi St, Chennai",
        "dest_lat": 13.0418,
        "dest_lng": 80.2341,
        "item_desc": "Inverter",
        "staff_id": staff_id,
    }
    body.update(over)
    r = owner.post("/api/deliveries", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def staff_headers(staff):
    return {"X-Staff-Token": staff.access_token}
