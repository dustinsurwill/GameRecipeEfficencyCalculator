from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # ── App ───────────────────────────────────────────────────────────────────
    app_name: str = "RecipeBook"
    debug: bool = False
    frontend_url: str = "http://localhost:5173"  # SvelteKit dev server

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://recipebook:recipebook@localhost/recipebook"
    # Sync URL only used by Alembic migrations
    database_url_sync: str = "postgresql+psycopg2://recipebook:recipebook@localhost/recipebook"

    # ── JWT ───────────────────────────────────────────────────────────────────
    jwt_secret: str = "change-me-in-production"   # override via env
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 30

    # ── OAuth: GitHub ─────────────────────────────────────────────────────────
    github_client_id: str = ""
    github_client_secret: str = ""

    # ── OAuth: Google ─────────────────────────────────────────────────────────
    google_client_id: str = ""
    google_client_secret: str = ""

    # ── OAuth: Authentik (optional homelab) ───────────────────────────────────
    authentik_client_id: str = ""
    authentik_client_secret: str = ""
    authentik_base_url: str = ""          # e.g. https://auth.yourdomain.com

    # ── Email ─────────────────────────────────────────────────────────────────
    mail_username: str = ""
    mail_password: str = ""
    mail_from: str = "noreply@recipebook.app"
    mail_server: str = "smtp.gmail.com"
    mail_port: int = 587


@lru_cache
def get_settings() -> Settings:
    return Settings()
