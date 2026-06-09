from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.db.database import init_db
from app.routers import ai_prompts, files, media, pages, publish, settings as settings_router, tasks


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


@app.middleware("http")
async def disable_static_cache(request: Request, call_next):
    is_local_asset = request.url.path.startswith(("/media/", "/static/"))
    request_origin = request.headers.get("Origin", "")
    allowed_origins = {
        "https://creator.douyin.com",
        "https://member.bilibili.com",
    }
    if is_local_asset and request.method == "OPTIONS":
        response = Response()
    else:
        response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    if is_local_asset:
        response.headers["Access-Control-Allow-Origin"] = (
            request_origin if request_origin in allowed_origins else "*"
        )
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "*"
        response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


app.mount("/static", StaticFiles(directory=settings.project_root / "app" / "static"), name="static")
app.include_router(pages.router)
app.include_router(ai_prompts.router)
app.include_router(tasks.router)
app.include_router(files.router)
app.include_router(media.router)
app.include_router(publish.router)
app.include_router(settings_router.router)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(
        settings.project_root / "app" / "static" / "img" / "brand" / "niuma-studio-favicon.ico",
        media_type="image/x-icon",
    )


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}
