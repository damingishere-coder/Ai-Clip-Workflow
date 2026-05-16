from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.db.database import init_db
from app.routers import pages, tasks


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    description=settings.app_description,
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=settings.project_root / "app" / "static"), name="static")
app.include_router(pages.router)
app.include_router(tasks.router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}
