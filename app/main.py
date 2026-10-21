import logging
import traceback
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import services as svc
from app.config import settings
from app.db import SessionLocal, init_db
from app.routers import owner, pages, staff, track

log = logging.getLogger("delivery")


class RedactTokensFilter(logging.Filter):
    """Removes staff/track tokens from URL paths in log lines (e.g. uvicorn access logs)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(svc.redact(a) if isinstance(a, str) else a for a in record.args)
        if isinstance(record.msg, str):
            record.msg = svc.redact(record.msg)
        return True


def install_log_redaction() -> None:
    f = RedactTokensFilter()
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn", "delivery"):
        logging.getLogger(name).addFilter(f)


def startup_cleanup() -> None:
    db = SessionLocal()
    try:
        removed = svc.cleanup_old_pings(db)
        log.info("Startup cleanup removed %d location pings older than %d days",
                 removed, settings.PING_RETENTION_DAYS)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    install_log_redaction()
    init_db()
    startup_cleanup()
    yield


app = FastAPI(title="Delivery Tracking", lifespan=lifespan, docs_url=None, redoc_url=None)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="owner_session",
    max_age=14 * 24 * 3600,
    same_site="lax",
    https_only=settings.BASE_URL.startswith("https"),
)


@app.middleware("http")
async def catch_unhandled(request: Request, call_next):
    """Turns unexpected errors into a plain 500 and logs them with tokens redacted."""
    try:
        return await call_next(request)
    except Exception as exc:  # noqa: BLE001
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        log.error("Unhandled error on %s %s\n%s", request.method, svc.redact(request.url.path), svc.redact(tb))
        return JSONResponse({"detail": "Internal server error"}, status_code=500)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Report which fields are wrong without echoing the submitted values back.
    errors = [{"field": ".".join(str(p) for p in e["loc"][1:]), "msg": e["msg"]} for e in exc.errors()]
    return JSONResponse({"detail": "Invalid input", "errors": errors}, status_code=422)


app.include_router(owner.router)
app.include_router(staff.router)
app.include_router(track.router)
app.include_router(pages.router)
app.mount("/static", StaticFiles(directory=pages.STATIC), name="static")
