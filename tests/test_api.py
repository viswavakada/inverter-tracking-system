from datetime import timedelta

from sqlalchemy import select

from app import services as svc
from app.models import Delivery, DeliveryEvent, LocationPing, utcnow
from tests.conftest import make_delivery, staff_headers


# ---------------------------------------------------------------- auth
def test_owner_endpoints_require_login(anon):
    assert anon.get("/api/dashboard").status_code == 401
    assert anon.get("/api/deliveries?date=today").status_code == 401
    assert anon.post("/api/owner/login", json={"password": "nope"}).status_code == 401


def test_staff_endpoints_require_valid_token(anon, owner, staff_member):
    d = make_delivery(owner, staff_member.id)
    assert anon.get("/api/staff/me/deliveries").status_code == 401
    assert anon.get("/api/staff/me/deliveries", headers={"X-Staff-Token": "bogus"}).status_code == 401
    assert anon.post(f"/api/deliveries/{d['id']}/start", headers={"X-Staff-Token": "bogus"}).status_code == 401


def test_staff_cannot_touch_other_staff_delivery(anon, owner, staff_member, other_staff):
    d = make_delivery(owner, staff_member.id)
    r = anon.post(f"/api/deliveries/{d['id']}/start", headers=staff_headers(other_staff))
    assert r.status_code == 404


def test_deactivated_staff_link_stops_working(anon, owner, staff_member):
    assert owner.delete(f"/api/staff/{staff_member.id}").status_code == 200
    assert anon.get("/api/staff/me/deliveries", headers=staff_headers(staff_member)).status_code == 401


# ---------------------------------------------------------------- create / list
def test_create_delivery_generates_track_token(owner, db, staff_member):
    d = make_delivery(owner, staff_member.id)
    assert d["status"] == "assigned"
    row = db.get(Delivery, d["id"])
    assert len(row.track_token) >= 32
    assert d["track_url"].endswith(row.track_token)

    listed = owner.get("/api/deliveries?date=today").json()
    assert [x["id"] for x in listed] == [d["id"]]


def test_create_delivery_rejects_inactive_staff(owner, staff_member):
    owner.patch(f"/api/staff/{staff_member.id}", json={"active": False})
    r = owner.post("/api/deliveries", json={
        "customer_name": "A", "customer_phone": "+919811111101", "address": "x",
        "dest_lat": 13, "dest_lng": 80, "staff_id": staff_member.id})
    assert r.status_code == 400


# ---------------------------------------------------------------- transitions
def test_happy_path_transitions(anon, owner, db, staff_member):
    d = make_delivery(owner, staff_member.id)
    h = staff_headers(staff_member)
    base = f"/api/deliveries/{d['id']}"

    assert anon.post(f"{base}/arrive", headers=h).status_code == 409  # not started yet
    r = anon.post(f"{base}/start", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "on_the_way" and r.json()["started_at"]
    assert anon.post(f"{base}/start", headers=h).status_code == 409  # cannot start twice

    r = anon.post(f"{base}/arrive", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "arrived"

    r = anon.post(f"{base}/deliver", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "delivered" and r.json()["delivered_at"]

    events = [e.event for e in db.scalars(select(DeliveryEvent).where(DeliveryEvent.delivery_id == d["id"]))]
    assert events == ["created", "on_the_way", "arrived", "delivered"]


def test_cannot_deliver_before_start(anon, owner, db, staff_member):
    d = make_delivery(owner, staff_member.id)
    r = anon.post(f"/api/deliveries/{d['id']}/deliver", headers=staff_headers(staff_member))
    assert r.status_code == 409


def test_cancel_and_no_transitions_after(anon, owner, staff_member):
    d = make_delivery(owner, staff_member.id)
    r = owner.patch(f"/api/deliveries/{d['id']}", json={"status": "cancelled"})
    assert r.json()["status"] == "cancelled"
    assert anon.post(f"/api/deliveries/{d['id']}/start", headers=staff_headers(staff_member)).status_code == 409
    assert owner.patch(f"/api/deliveries/{d['id']}", json={"status": "cancelled"}).status_code == 409


def test_reassign_resets_to_assigned(anon, owner, staff_member, other_staff):
    d = make_delivery(owner, staff_member.id)
    anon.post(f"/api/deliveries/{d['id']}/start", headers=staff_headers(staff_member))
    r = owner.patch(f"/api/deliveries/{d['id']}", json={"staff_id": other_staff.id})
    assert r.json()["status"] == "assigned" and r.json()["staff_id"] == other_staff.id
    assert anon.post(f"/api/deliveries/{d['id']}/arrive", headers=staff_headers(staff_member)).status_code == 404
    assert anon.post(f"/api/deliveries/{d['id']}/start", headers=staff_headers(other_staff)).status_code == 200


# ---------------------------------------------------------------- pings
def test_pings_only_while_on_the_way(anon, owner, db, staff_member):
    d = make_delivery(owner, staff_member.id)
    h = staff_headers(staff_member)
    url = f"/api/deliveries/{d['id']}/ping"
    pos = {"lat": 13.05, "lng": 80.24}

    assert anon.post(url, headers=h, json=pos).status_code == 409  # assigned
    anon.post(f"/api/deliveries/{d['id']}/start", headers=h)
    assert anon.post(url, headers=h, json=pos).status_code == 200
    anon.post(f"/api/deliveries/{d['id']}/arrive", headers=h)
    assert anon.post(url, headers=h, json=pos).status_code == 409  # arrived
    anon.post(f"/api/deliveries/{d['id']}/deliver", headers=h)
    assert anon.post(url, headers=h, json=pos).status_code == 409  # delivered
    assert anon.post(url, headers=h, json={"lat": 200, "lng": 0}).status_code == 422


def test_cleanup_removes_old_pings(db, owner, staff_member):
    d = make_delivery(owner, staff_member.id)
    now = utcnow()
    db.add_all([
        LocationPing(delivery_id=d["id"], lat=1, lng=1, recorded_at=now - timedelta(days=8)),
        LocationPing(delivery_id=d["id"], lat=2, lng=2, recorded_at=now - timedelta(days=1)),
    ])
    db.commit()
    assert svc.cleanup_old_pings(db) == 1
    assert [p.lat for p in db.scalars(select(LocationPing))] == [2]


# ---------------------------------------------------------------- customer tracking
def test_track_public_view(anon, owner, db, staff_member):
    d = make_delivery(owner, staff_member.id, dest_lat=13.0418, dest_lng=80.2341)
    token = db.get(Delivery, d["id"]).track_token
    h = staff_headers(staff_member)

    r = anon.get(f"/api/track/{token}").json()
    assert r["status"] == "assigned"
    assert r["staff"] == {"first_name": "Ravi", "phone": "+919800000001"}
    # No internal ids, tokens or other customers' details.
    text = str(r)
    for leaked in ("'id'", "staff_id", "track_token", "access_token", "customer_phone", staff_member.access_token):
        assert leaked not in text

    anon.post(f"/api/deliveries/{d['id']}/start", headers=h)
    # ~4.6 km away -> 11 min at 25 km/h -> rounded up to 15
    anon.post(f"/api/deliveries/{d['id']}/ping", headers=h, json={"lat": 13.0, "lng": 80.2341})
    r = anon.get(f"/api/track/{token}").json()
    assert r["eta_minutes"] == 15
    assert r["status_text"] == "On the way, about 15 min away"
    assert r["route"] is None  # routing is off in tests: straight-line fallback
    assert r["last_position"]["lat"] == 13.0

    anon.post(f"/api/deliveries/{d['id']}/deliver", headers=h)
    r = anon.get(f"/api/track/{token}").json()
    assert r["status"] == "delivered"
    assert r["last_position"] is None and r["staff"] is None and r["route"] is None
    assert len(r["delivered_at_local"]) == 5  # HH:MM


def test_track_unknown_token_and_rate_limit(anon):
    r = anon.get("/api/track/does-not-exist")
    assert r.status_code == 404 and "does-not-exist" not in r.text
    svc.track_limiter.limit = 3
    try:
        codes = [anon.get("/api/track/x").status_code for _ in range(5)]
    finally:
        svc.track_limiter.limit = 60
    assert codes[-1] == 429


# ---------------------------------------------------------------- ETA + flags
def test_eta_rounds_up_to_5():
    assert svc.estimate_eta_minutes(13.0, 80.0, 13.0, 80.0) == 5
    # 10 km at 25 km/h = 24 min -> 25
    lat2 = 13.0 + 10 / 111.195
    assert svc.estimate_eta_minutes(13.0, 80.0, lat2, 80.0) == 25


def _d(status, started_ago=None, arrived_ago=None, now=None):
    d = Delivery(status=status)
    d.started_at = now - timedelta(minutes=started_ago) if started_ago is not None else None
    d.arrived_at = now - timedelta(minutes=arrived_ago) if arrived_ago is not None else None
    return d


def test_delivery_flags_rules():
    now = utcnow()
    fresh = now - timedelta(seconds=30)
    assert svc.delivery_flags(_d("on_the_way", 10, now=now), fresh, now) == []
    assert svc.delivery_flags(_d("on_the_way", 50, now=now), fresh, now) == ["delayed"]
    assert svc.delivery_flags(_d("on_the_way", 10, now=now), now - timedelta(minutes=3), now) == ["signal_lost"]
    # no ping at all for 2+ minutes after start counts as signal lost
    assert svc.delivery_flags(_d("on_the_way", 3, now=now), None, now) == ["signal_lost"]
    assert svc.delivery_flags(_d("on_the_way", 1, now=now), None, now) == []
    assert svc.delivery_flags(_d("arrived", 90, 30, now=now), None, now) == []
    assert svc.delivery_flags(_d("arrived", 120, 61, now=now), None, now) == ["delayed"]
    assert svc.delivery_flags(_d("assigned", now=now), None, now) == []
    assert svc.delivery_flags(_d("delivered", 500, 400, now=now), None, now) == []


def test_dashboard_flags(anon, owner, db, staff_member, other_staff):
    d = make_delivery(owner, staff_member.id)
    make_delivery(owner, other_staff.id)
    anon.post(f"/api/deliveries/{d['id']}/start", headers=staff_headers(staff_member))

    # Pretend the trip started 50 min ago and the last ping was 5 min ago.
    row = db.get(Delivery, d["id"])
    row.started_at = utcnow() - timedelta(minutes=50)
    db.add(LocationPing(delivery_id=row.id, lat=13.0, lng=80.2, recorded_at=utcnow() - timedelta(minutes=5)))
    db.commit()

    dash = owner.get("/api/dashboard").json()
    by_name = {s["name"]: s for s in dash["staff"]}
    ravi, suresh = by_name["Ravi Kumar"], by_name["Suresh Babu"]
    assert ravi["status"] == "on_the_way"
    assert set(ravi["flags"]) == {"delayed", "signal_lost"}
    assert ravi["active_delivery"]["id"] == d["id"] and ravi["last_lat"] == 13.0
    assert suresh["status"] == "idle" and suresh["flags"] == ["idle"] and suresh["pending_count"] == 1
    assert dash["counts"]["on_the_way"] == 1 and dash["counts"]["assigned"] == 1


def test_redact_tokens_in_logs():
    line = 'GET /s/abcDEF_123-x HTTP/1.1 and /track/zzz and /api/track/yyy?x=1'
    out = svc.redact(line)
    assert "abcDEF_123-x" not in out and "zzz" not in out and "yyy" not in out


# ---------------------------------------------------------------- road routing
def test_road_route_used_for_eta_and_cached_per_ping(anon, owner, db, staff_member, monkeypatch):
    from app import routing

    calls = []

    def fake_fetch(from_lat, from_lng, to_lat, to_lng):
        calls.append((from_lat, from_lng))
        # 9 km by road -> 21.6 min at 25 km/h -> 25 min
        return routing.Route(coords=[[from_lat, from_lng], [13.02, 80.22], [to_lat, to_lng]], distance_km=9.0)

    monkeypatch.setattr(routing, "fetch_route", fake_fetch)
    d = make_delivery(owner, staff_member.id)
    token = db.get(Delivery, d["id"]).track_token
    h = staff_headers(staff_member)
    anon.post(f"/api/deliveries/{d['id']}/start", headers=h)
    anon.post(f"/api/deliveries/{d['id']}/ping", headers=h, json={"lat": 13.0, "lng": 80.2341})

    r = anon.get(f"/api/track/{token}").json()
    assert r["eta_minutes"] == 25
    assert r["route"][0] == [13.0, 80.2341] and len(r["route"]) == 3
    owner.get("/api/dashboard")
    anon.get(f"/api/track/{token}")
    assert len(calls) == 1  # polling again without a new ping reuses the route

    anon.post(f"/api/deliveries/{d['id']}/ping", headers=h, json={"lat": 13.01, "lng": 80.2341})
    anon.get(f"/api/track/{token}")
    assert len(calls) == 2


def test_routing_server_failure_falls_back_to_straight_line(monkeypatch):
    import httpx

    from app import routing

    monkeypatch.setattr(routing.settings, "ROUTING_URL", "http://routing.invalid")

    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(routing.httpx, "get", boom)
    assert routing.fetch_route(13.0, 80.0, 13.1, 80.1) is None
