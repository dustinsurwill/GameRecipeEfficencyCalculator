from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.routes.auth import router as auth_router
from app.api.v1.routes.users import router as users_router
from app.api.v1.routes.admin import router as admin_router
from app.api.v1.routes.catalog import router as catalog_router
from app.api.v1.routes.chains import router as chains_router
from app.api.v1.routes.discover import router as discover_router
from app.api.v1.routes.items import router as items_router
from app.api.v1.routes.recipes import router as recipes_router
from app import memory
from app.config import get_settings
from app.web.account import router as account_router
from app.web.admin import router as web_admin_router
from app.web.contribute import router as contribute_router
from app.web.deps import LoginRequired
from app.web.pages import router as pages_router
from app.web.templating import render

settings = get_settings()

_INSECURE_SECRETS = {"change-me-in-production", "change-me-before-deploying", ""}
if not settings.debug and settings.jwt_secret in _INSECURE_SECRETS:
    raise RuntimeError("JWT_SECRET must be set to a private value when DEBUG is off")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — nothing needed here since Alembic handles migrations
    # If you want auto-create for dev: import SQLModel; await create_tables()
    yield
    # Shutdown


app = FastAPI(
    title="RecipeBook API",
    version="0.1.0",
    lifespan=lifespan,
    # Hide docs in production
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

if settings.frontend_url:   # the site is same-origin; CORS is only for external API clients
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_url],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(auth_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(catalog_router, prefix="/api/v1")
app.include_router(chains_router, prefix="/api/v1")
app.include_router(discover_router, prefix="/api/v1")
app.include_router(items_router, prefix="/api/v1")
app.include_router(recipes_router, prefix="/api/v1")

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/healthz")
async def healthz() -> dict:
    from app.api.v1.routes.chains import _graph_cache
    from app.discovery.index import _indexes
    return {"status": "ok", "rss_mb": round(memory.rss_mb()), "indexes": len(_indexes), "graphs": len(_graph_cache)}


# ── Server-rendered site ──────────────────────────────────────────────────────
# Order matters: the discovery pages use catch-all patterns (/{game}/{pack}/...), so the
# fixed routes (static, login, admin, ...) must be registered before them.
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "web" / "static"), name="static")
app.include_router(account_router)
app.include_router(web_admin_router)
app.include_router(contribute_router)
app.include_router(pages_router)

_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; "
    "img-src 'self' data: https:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if not request.url.path.startswith(("/docs", "/redoc", "/openapi")):
        response.headers.setdefault("Content-Security-Policy", _CSP)
    return response


@app.exception_handler(LoginRequired)
async def login_required(request: Request, exc: LoginRequired):
    from urllib.parse import quote
    return RedirectResponse(f"/login?next={quote(exc.next_url)}", status_code=303)


_TITLES = {403: "Not allowed", 404: "We couldn’t find that", 429: "Slow down a little"}


@app.exception_handler(StarletteHTTPException)
async def html_errors(request: Request, exc: StarletteHTTPException):
    wants_html = "text/html" in request.headers.get("accept", "")
    if request.url.path.startswith("/api") or not wants_html:
        return await http_exception_handler(request, exc)
    return render(
        request, "error.html", status_code=exc.status_code, status=exc.status_code,
        title=_TITLES.get(exc.status_code, "Something went wrong"), detail=exc.detail,
        scope=getattr(request.state, "scope", None),
    )
