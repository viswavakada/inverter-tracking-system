"""Pretend to be a staff member driving to a customer, so you can watch the
customer tracking page and owner dashboard without going outside.

The fake rider follows real roads (from the OSRM routing server) when it can,
otherwise drives in a straight line.

Examples:
    # Use a staff link printed by seed.py; picks that person's first open delivery
    python simulate_staff.py http://localhost:8000/s/<token>

    # Faster run, pick delivery 2, and mark it delivered on arrival
    python simulate_staff.py http://localhost:8000/s/<token> --delivery 2 --time-scale 30 --deliver

    # Stop pinging halfway to trigger the owner's "signal lost" alert
    python simulate_staff.py http://localhost:8000/s/<token> --drop-signal-at 0.5
"""
import argparse
import math
import random
import sys
import time
from urllib.parse import urlparse

import httpx

EARTH_KM_PER_DEG_LAT = 111.195
OSRM_URL = "https://router.project-osrm.org"


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("staff_link", help="Full staff link, e.g. http://localhost:8000/s/<token>")
    p.add_argument("--delivery", type=int, help="Delivery id (default: first open delivery)")
    p.add_argument("--from", dest="start", help="Start position 'lat,lng' (default: about 5 km from the customer)")
    p.add_argument("--speed-kmh", type=float, default=25.0, help="Driving speed (default 25)")
    p.add_argument("--interval", type=float, default=3.0, help="Real seconds between pings (default 3)")
    p.add_argument("--time-scale", type=float, default=10.0,
                   help="Simulated seconds per real second, so the trip finishes sooner (default 10)")
    p.add_argument("--deliver", action="store_true", help="Mark the delivery delivered after arriving")
    p.add_argument("--no-arrive", action="store_true", help="Stop just before the door without pressing Arrived")
    p.add_argument("--drop-signal-at", type=float,
                   help="Fraction of the trip (0-1) after which pings stop, to test 'signal lost'")
    return p.parse_args()


def km_between(a, b):
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def road_path(start, dest):
    """Road route as [(lat, lng), ...], or a straight line if routing is unavailable."""
    url = f"{OSRM_URL}/route/v1/driving/{start[1]:.6f},{start[0]:.6f};{dest[1]:.6f},{dest[0]:.6f}"
    try:
        r = httpx.get(url, params={"overview": "full", "geometries": "geojson"}, timeout=10)
        coords = r.json()["routes"][0]["geometry"]["coordinates"]
        path = [(lat, lng) for lng, lat in coords]
        print(f"Following the road route ({len(path)} points).")
        return path + [dest]
    except Exception:  # noqa: BLE001
        print("Routing server unavailable, driving in a straight line.")
        return [start, dest]


def walk(path, step_km):
    """Points every `step_km` along the path, ending exactly at its last point."""
    points = [path[0]]
    carried = 0.0
    for a, b in zip(path, path[1:]):
        seg = km_between(a, b)
        pos = step_km - carried
        while pos < seg:
            f = pos / seg
            points.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
            pos += step_km
        carried = (carried + seg) % step_km if seg else carried
    if points[-1] != path[-1]:
        points.append(path[-1])
    return points


def main():
    args = parse_args()
    url = urlparse(args.staff_link)
    parts = url.path.strip("/").split("/")
    if len(parts) != 2 or parts[0] != "s":
        sys.exit("The staff link must look like http://host:port/s/<token>")
    base = f"{url.scheme}://{url.netloc}"
    client = httpx.Client(base_url=base, headers={"X-Staff-Token": parts[1]}, timeout=10)

    r = client.get("/api/staff/me/deliveries")
    if r.status_code == 401:
        sys.exit("That staff link is not valid.")
    r.raise_for_status()
    data = r.json()
    open_ones = [d for d in data["deliveries"] if d["status"] in ("assigned", "on_the_way")]
    if args.delivery:
        open_ones = [d for d in open_ones if d["id"] == args.delivery]
    if not open_ones:
        sys.exit("No assigned or on-the-way delivery found for this staff member.")
    d = open_ones[0]
    dest = (d["dest_lat"], d["dest_lng"])
    print(f"{data['staff_name']} -> delivery #{d['id']} for {d['customer_name']} ({d['address']})")

    if args.start:
        start = tuple(float(x) for x in args.start.split(","))
    else:
        bearing = random.uniform(0, 2 * math.pi)
        dlat = 3.5 * math.cos(bearing) / EARTH_KM_PER_DEG_LAT
        dlng = 3.5 * math.sin(bearing) / (EARTH_KM_PER_DEG_LAT * math.cos(math.radians(dest[0])))
        start = (dest[0] + dlat, dest[1] + dlng)

    if d["status"] == "assigned":
        client.post(f"/api/deliveries/{d['id']}/start").raise_for_status()
        print("Pressed Start.")

    path = road_path(start, dest)
    total_km = sum(km_between(a, b) for a, b in zip(path, path[1:]))
    km_per_ping = args.speed_kmh / 3600 * args.interval * args.time_scale
    points = walk(path, km_per_ping)
    steps = len(points) - 1
    print(f"Driving {total_km:.1f} km in {steps} pings, one every {args.interval:g}s. Ctrl+C to stop.")

    for i, (lat, lng) in enumerate(points):
        if args.drop_signal_at is not None and i / max(steps, 1) >= args.drop_signal_at:
            print("Signal dropped: no more pings. The owner should see 'signal lost' after 2 minutes.")
            return
        r = client.post(f"/api/deliveries/{d['id']}/ping", json={"lat": lat, "lng": lng})
        if r.status_code == 409:
            print("Server stopped accepting pings (delivery finished, cancelled or reassigned).")
            return
        r.raise_for_status()
        print(f"  ping {i:>3}/{steps}  {lat:.5f},{lng:.5f}  {km_between((lat, lng), dest):.2f} km to go (straight line)")
        if i < steps:
            time.sleep(args.interval)

    if args.no_arrive:
        return
    client.post(f"/api/deliveries/{d['id']}/arrive").raise_for_status()
    print("Pressed Arrived.")
    if args.deliver:
        time.sleep(args.interval)
        r = client.post(f"/api/deliveries/{d['id']}/deliver")
        print("Delivered." if r.is_success else f"Deliver failed: {r.json().get('detail')}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
