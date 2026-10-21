import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

router = APIRouter(include_in_schema=False)

STATIC = Path(__file__).resolve().parent.parent / "static"
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "strict-origin"}


@router.get("/")
def root():
    return FileResponse(STATIC / "owner" / "index.html", headers=NO_STORE)


@router.get("/owner")
def owner_page():
    return FileResponse(STATIC / "owner" / "index.html", headers=NO_STORE)


# Must be declared before /s/{token} so "sw.js" is not treated as a token.
@router.get("/s/sw.js")
def staff_service_worker():
    return FileResponse(
        STATIC / "staff" / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/s/"},
    )


@router.get("/s/{token}/manifest.json")
def staff_manifest(token: str):
    """Per-staff manifest so the installed home-screen app opens that person's own link."""
    manifest = {
        "name": "Delivery Staff",
        "short_name": "Deliveries",
        "start_url": f"/s/{token}",
        "scope": "/s/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#1d4ed8",
        "icons": [
            {"src": "/static/staff/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/staff/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }
    return Response(json.dumps(manifest), media_type="application/manifest+json", headers=NO_STORE)


@router.get("/s/{token}")
def staff_page(token: str):
    return FileResponse(STATIC / "staff" / "index.html", headers=NO_STORE)


@router.get("/track/{token}")
def track_page(token: str):
    return FileResponse(STATIC / "track" / "index.html", headers=NO_STORE)
