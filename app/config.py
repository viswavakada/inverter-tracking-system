import os

from dotenv import load_dotenv

load_dotenv()


def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "sqlite:///./delivery.db")
    # Hosts such as Render and Heroku hand out "postgres://", which SQLAlchemy does not accept.
    if url.startswith("postgres://"):
        url = "postgresql+psycopg2://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


def _base_url() -> str:
    # On Render, RENDER_EXTERNAL_URL is the service's public https address.
    url = os.getenv("BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "http://localhost:8000"
    return url.rstrip("/")


class Settings:
    OWNER_PASSWORD: str = os.getenv("OWNER_PASSWORD", "change-me")
    DATABASE_URL: str = _database_url()
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-change-me")
    BASE_URL: str = _base_url()
    TIMEZONE: str = "Asia/Kolkata"
    # OSRM server for road routes. Empty string turns routing off (straight lines only).
    ROUTING_URL: str = os.getenv("ROUTING_URL", "https://router.project-osrm.org").rstrip("/")
    ROUTING_TIMEOUT_SEC: float = 4.0

    # Alert thresholds (minutes)
    DELAY_ON_THE_WAY_MIN: int = 45
    DELAY_ARRIVED_MIN: int = 60
    SIGNAL_LOST_MIN: int = 2
    PING_RETENTION_DAYS: int = 7

    # Public tracking rate limit: requests per window per client IP
    TRACK_RATE_LIMIT: int = 60
    TRACK_RATE_WINDOW_SEC: int = 60


settings = Settings()

# A public https server must not run with the sample password or signing key.
if settings.BASE_URL.startswith("https://"):
    if settings.OWNER_PASSWORD in ("", "change-me") or len(settings.OWNER_PASSWORD) < 8:
        raise RuntimeError("Set OWNER_PASSWORD to a password of at least 8 characters before deploying.")
    if settings.SECRET_KEY in ("", "dev-secret-change-me", "replace-with-a-long-random-string"):
        raise RuntimeError("Set SECRET_KEY to a long random string before deploying.")
