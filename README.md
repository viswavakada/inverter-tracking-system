# Delivery Tracking (MVP)

Tracking of the staff; the customer sees their product's delivery live.

Same-day delivery tracking for an inverter and battery shop.

- **Customers** get a live tracking link with a map showing the road route, an ETA and a call button. No login.
- **The owner** sees every staff member and delivery on one screen, with delayed, idle and signal-lost alerts.
- **Staff** use a mobile web page that can be installed to the home screen. It shares GPS while on the way.

Stack: FastAPI, SQLAlchemy 2, SQLite or PostgreSQL, plain HTML with vanilla JS, Tailwind CDN, and Leaflet with OpenStreetMap tiles. Pages poll every 5 seconds.

## Run it locally

You need Python 3.10 or newer.

**Windows (PowerShell)**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python seed.py --reset
uvicorn app.main:app --reload --port 8000
```

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python seed.py --reset
uvicorn app.main:app --reload --port 8000
```

`seed.py` prints a link for each of the 3 staff and each of the 5 customers. Then open:

| Page | URL |
| --- | --- |
| Owner dashboard | http://localhost:8000/owner (password from `OWNER_PASSWORD`, default `change-me`) |
| Staff page | `http://localhost:8000/s/<staff token>` from the seed output |
| Customer tracking | `http://localhost:8000/track/<track token>` from the seed output |

Without `--reset`, `seed.py` adds another set of sample data to the existing database.

## Configuration (.env)

| Variable | Meaning |
| --- | --- |
| `OWNER_PASSWORD` | Password for /owner |
| `DATABASE_URL` | `sqlite:///./delivery.db` for dev, or `postgresql+psycopg2://user:pass@host:5432/delivery` |
| `SECRET_KEY` | Long random string that signs the owner session cookie |
| `BASE_URL` | Public address of the server, used in the links. Use `https://...` in production |
| `ROUTING_URL` | OSRM server for road routes. Defaults to the free public demo server. Leave empty to draw straight lines |

Tables are created automatically on startup. Alert thresholds, the 7-day ping retention and the tracking rate limit are in `app/config.py`.

## Test without going outside

Start the server, then in a second terminal run the simulator with a staff link from the seed output:

```bash
python simulate_staff.py http://localhost:8000/s/<staff token>
```

It presses Start on that person's first open delivery, then drives along the roads toward the customer and sends a ping every 3 seconds. Open that customer's tracking link and the owner dashboard to watch the pin move. Useful options:

```bash
# pick delivery 2, run faster, and mark it delivered on arrival
python simulate_staff.py http://localhost:8000/s/<token> --delivery 2 --time-scale 30 --deliver

# stop pinging halfway; the owner sees "Signal lost" after 2 minutes
python simulate_staff.py http://localhost:8000/s/<token> --drop-signal-at 0.5
```

`python simulate_staff.py --help` lists all options.

## Automated tests

```bash
python -m pytest -q
```

The tests use a temporary SQLite file. To run them against PostgreSQL instead, set `TEST_DATABASE_URL` to a throwaway database, because its tables are dropped. They cover status transitions, ping acceptance, road routing with its fallback, the delayed, idle and signal-lost flags, ETA rounding, ping cleanup, the rate limit, token redaction in logs, and what the public tracking endpoint exposes.

## Testing on a real phone

Browsers only allow GPS and the screen wake lock on `https://` pages, or on `localhost`. A phone reaching your PC at `http://192.168.x.x:8000` will get no location. For a phone test, expose the server over HTTPS with a tunnel such as `cloudflared tunnel --url http://localhost:8000` or `ngrok http 8000`. Then set `BASE_URL` in `.env` to the tunnel address and restart, so new links use it.

On the staff page in Chrome on Android, use the menu and choose "Add to Home screen" to install it.

## Deploy to Render (runs 24/7, without your laptop)

Render runs the app and a PostgreSQL database on its servers, with HTTPS included. Phones need HTTPS for GPS. `render.yaml` describes both, in Render's Singapore region, which is the closest to India.

1. **Put the code on GitHub.** Create a free account at github.com and a new **private** repository, for example `delivery-tracking`, with no README. Then run this in the project folder:
   ```bash
   git remote add origin https://github.com/<your-username>/delivery-tracking.git
   git push -u origin main
   ```
2. **Create the services.** Sign up at render.com with your GitHub account. Choose **New**, then **Blueprint**, and pick the repository.
3. **Set the owner password.** Render asks for `OWNER_PASSWORD`. Use at least 8 characters. `SECRET_KEY` and `DATABASE_URL` are filled in automatically.
4. **Wait for the first deploy.** It takes a few minutes. Your app is then at `https://delivery-tracking-xxxx.onrender.com`. Open `/owner`, log in, and add your staff on the Staff tab.

Do not run `seed.py` against the live database. It adds sample customers.

**Plans.** The blueprint starts on the free plans so you can try it at no cost. They have two limits:

- The free web service sleeps after 15 minutes without visitors. The next visitor waits about a minute while it wakes.
- The free database is deleted 30 days after creation unless you upgrade it.

For daily use, upgrade both in the Render dashboard, or change the two `plan:` values in `render.yaml`. Use `starter` for the web service and `basic-256mb` for the database. Check render.com/pricing for current prices.

**Updates.** Every `git push` to `main` redeploys the app automatically. Tables are created on startup. If you later change the structure of an existing table, it needs a migration step, so plan for that before changing models.

**Other hosts.** The `Dockerfile` runs the app on any container host or VPS. Set the same environment variables there. The app needs PostgreSQL and HTTPS in front of it. A public server refuses to start while `OWNER_PASSWORD` or `SECRET_KEY` still has its sample value.

## How staff log in

Staff have no username or password. Each staff member gets a private link, such as `https://your-server/s/<secret token>`. The link is their login.

1. The owner opens the **Staff** tab and adds the person with name and phone.
2. The owner taps **Send link on WhatsApp**, or **Copy staff link**, to send them their link.
3. The staff member opens the link on their phone and installs it with the browser menu: "Add to Home screen".
4. From then on, they open the app from the home screen and see only their own deliveries.

The token is random and cannot be guessed. If a phone is lost or the person leaves, tap **Deactivate** on the Staff tab, and the link stops working immediately.

## How it works

**Delivery statuses.** A delivery moves through these states:

```
assigned -> on_the_way -> arrived -> delivered
assigned / on_the_way / arrived -> cancelled   (owner only)
```

Staff can also press Delivered straight from on_the_way. Reassigning a delivery puts it back to assigned for the new person. Every change is recorded in `delivery_events`.

**Location.** After Start, the staff page watches GPS and sends the latest position every 20 seconds. It keeps the screen awake with the Wake Lock API and re-acquires the lock when the page becomes visible again. The server only accepts pings while the delivery is on_the_way. When the delivery ends, the page stops GPS and the customer page stops showing the location. Pings older than 7 days are deleted at startup.

**Route and ETA.** After each new GPS ping, the server asks the OSRM routing server for the driving route to the customer. The customer page and the owner map draw that route. The ETA is the road distance at 25 km/h, rounded up to the next 5 minutes, with a minimum of 5. If the routing server does not answer, the ETA uses the straight-line distance and the map shows a dashed straight line. The logic is in `app/routing.py` and `route_and_eta` in `app/services.py`.

The public OSRM demo server is free but rate-limited and has no uptime guarantee. For real daily use, run your own OSRM server or use a paid routing service, and set `ROUTING_URL`.

**Alerts.**

| Flag | Rule |
| --- | --- |
| Delayed | on_the_way for more than 45 min, or arrived for more than 60 min without delivery |
| Signal lost | on_the_way and no ping for 2 minutes or more, counted from Start if no ping has arrived |
| Idle | staff member has no delivery that is on_the_way or arrived |

**Marking delivered.** Staff tap Delivered and confirm in a pop-up, so a stray tap does not finish a delivery.

**Times.** Stored in UTC and shown in Asia/Kolkata.

**Privacy.**

- Tracking and staff tokens are random 32-character strings.
- Tokens are replaced with `<redacted>` in access logs and error logs, and SQL parameters are hidden in database errors.
- Staff pages send their token in an `X-Staff-Token` header, not in API URLs.
- The tracking endpoint returns no internal ids, no customer phone number, and only the staff member's first name and phone. It allows 60 requests per minute per IP address.

## API summary

Owner endpoints need the session cookie from login.

| Method | Path | Who |
| --- | --- | --- |
| POST | `/api/owner/login`, `/api/owner/logout` | owner |
| POST | `/api/deliveries` | owner: create and assign |
| GET | `/api/deliveries?date=today` or `?date=YYYY-MM-DD` | owner |
| PATCH | `/api/deliveries/{id}` with `{"staff_id": n}` or `{"status": "cancelled"}` | owner |
| GET | `/api/dashboard` | owner |
| GET, POST | `/api/staff` | owner |
| GET, PATCH, DELETE | `/api/staff/{id}` (DELETE deactivates) | owner |
| GET | `/api/staff/me/deliveries` | staff |
| POST | `/api/deliveries/{id}/start`, `/arrive`, `/deliver`, `/ping` with `{"lat", "lng"}` | staff |
| GET | `/api/track/{track_token}` | public |

"Today" means today in India time. The lists also include older deliveries that are still open.

## Project layout

```
app/
  main.py            app setup, startup cleanup, log redaction
  config.py          settings from .env and alert thresholds
  db.py              engine and session
  models/            SQLAlchemy models
  schemas.py         request validation
  services.py        ETA, flags, status transitions, cleanup, rate limit
  routing.py         road routes from OSRM, cached per GPS ping
  deps.py            owner and staff authentication
  routers/           owner.py, staff.py, track.py, pages.py
  static/            owner/, staff/ (PWA: manifest, service worker), track/, img/ (logo)
tests/               pytest suite
seed.py              sample data
simulate_staff.py    fake staff member for testing
```

## Known limits of the MVP

- The tracking rate limiter keeps counts in memory, so it is per process. With several workers, use a shared store such as Redis.
- Short Google Maps links like `maps.app.goo.gl/...` cannot be read in the browser. Open the short link, then paste the full link from the address bar, or tap the map.
- Most phones pause web pages when the screen is off. The wake lock keeps the screen on during a trip, but if the staff member locks the phone, location updates stop and the owner sees "Signal lost".
- The map on the owner page starts centred on Chennai. Change `DEFAULT_CENTER` in `app/static/owner/owner.js` to your shop's location.
