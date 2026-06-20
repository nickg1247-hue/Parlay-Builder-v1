import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    database_url: str = os.getenv(
        "DATABASE_URL", "sqlite:///./data/parlay_builder.db"
    )
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8000"))


settings = Settings()


def prop_slip_public_enabled() -> bool:
    """Prop slip UI + sportsbook export. Off in production unless PROP_SLIP_PUBLIC=true."""
    explicit = os.getenv("PROP_SLIP_PUBLIC", "").strip().lower()
    if explicit in ("1", "true", "yes"):
        return True
    if explicit in ("0", "false", "no"):
        return False
    app_env = os.getenv("APP_ENV", settings.app_env).strip().lower()
    return app_env != "production"
