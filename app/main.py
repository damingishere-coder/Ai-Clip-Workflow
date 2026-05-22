from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.db.database import init_db
from app.routers import ai_prompts, files, media, pages, settings as settings_router, tasks


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
app.include_router(ai_prompts.router)
app.include_router(tasks.router)
app.include_router(files.router)
app.include_router(media.router)
app.include_router(settings_router.router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}
