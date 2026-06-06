from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.routes.auth import router as auth_router
from app.api.v1.routes.users import router as users_router
from app.api.v1.routes.admin import router as admin_router
from app.api.v1.routes.catalog import router as catalog_router
from app.api.v1.routes.chains import router as chains_router
from app.api.v1.routes.items import router as items_router
from app.api.v1.routes.recipes import router as recipes_router
from app.config import get_settings

settings = get_settings()


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,   # needed for the refresh token cookie
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(catalog_router, prefix="/api/v1")
app.include_router(chains_router, prefix="/api/v1")
app.include_router(items_router, prefix="/api/v1")
app.include_router(recipes_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
