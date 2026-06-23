from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.db.database import init_db
from app.routers import ai_prompts, files, media, pages, publish, settings as settings_router, tasks
from app.services.publish_scheduler import start_scheduler_background


# /media 和 /static 的 Origin 白名单
_ALLOWED_CORS_ORIGINS = {
    "https://creator.douyin.com",
    "https://members.bilibili.com",
    "http://localhost:8001",
    "http://127.0.0.1:8001",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
}

# /api 写方法（需要 token 校验）
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _is_origin_allowed(origin: str) -> bool:
    if not origin:
        return False
    return origin in _ALLOWED_CORS_ORIGINS


def _build_allow_origin_header(origin: str) -> str:
    if _is_origin_allowed(origin):
        return origin
    # 对于 localhost / 127.0.0.1 的任意端口也放行（本地开发）
    if origin.startswith("http://localhost:") or origin.startswith("http://127.0.0.1:"):
        return origin
    return "null"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler = await start_scheduler_background()
    app.state.publish_scheduler = scheduler
    try:
        yield
    finally:
        if scheduler:
            scheduler.stop()


app = FastAPI(
    title=settings.app_name,
    description=settings.app_description,
    version="1.4.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    request_path = request.url.path
    request_origin = request.headers.get("Origin", "")

    # ── 1. API 写保护 ──
    if request_path.startswith("/api/") and request.method in _WRITE_METHODS:
        token = settings.local_admin_token
        if token:
            auth_header = request.headers.get("Authorization", "")
            expected = f"Bearer {token}"
            if auth_header != expected:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "API 写操作需要有效的 LOCAL_ADMIN_TOKEN"},
                )

    # ── 2. OPTIONS 预检请求处理 ──
    is_static_asset = request_path.startswith(("/media/", "/static/"))
    if is_static_asset and request.method == "OPTIONS":
        response = Response()
    else:
        response = await call_next(request)

    # ── 3. 静态资源缓存 ──
    if request_path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    # ── 4. CORS / Private Network Access ──
    if is_static_asset:
        allow_origin = _build_allow_origin_header(request_origin)
        response.headers["Access-Control-Allow-Origin"] = allow_origin
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "*"
        # 仅白名单 Origin 返回 Private Network 预检头
        if _is_origin_allowed(request_origin) or request_origin.startswith("http://localhost:") or request_origin.startswith("http://127.0.0.1:"):
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
