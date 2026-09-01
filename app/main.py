from contextlib import asynccontextmanager
import hmac
import ipaddress
import os
import tempfile
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.db.database import init_db
from app.routers import (
    ai_prompts,
    content_review,
    media,
    pages,
    publish,
    settings as settings_router,
    subtitles,
    system,
    tasks,
)
from app.services.publish_scheduler import start_scheduler_background
from app.services.storage_service import configure_runtime_media_storage
from app.services.job_worker import WorkflowJobRunner


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

# 静态媒体可读 Origin 与管理 API 可写 Origin 必须分离。平台创作者中心
# 只需要读取受控媒体，不能因此获得本地管理写权限。
_TRUSTED_WRITE_ORIGINS = {
    "http://localhost:8001",
    "http://127.0.0.1:8001",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
}

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_PUBLIC_PATHS = {"/health", "/favicon.ico"}


def _is_origin_allowed(origin: str) -> bool:
    if not origin:
        return False
    return origin in _ALLOWED_CORS_ORIGINS


def _build_allow_origin_header(origin: str) -> str:
    if _is_origin_allowed(origin):
        return origin
    return "null"


def _is_local_origin(origin: str) -> bool:
    return origin in _TRUSTED_WRITE_ORIGINS


def _is_same_origin(request: Request, origin: str) -> bool:
    try:
        parsed = urlsplit(origin)
        return (
            parsed.scheme.lower() == request.url.scheme.lower()
            and (parsed.hostname or "").lower().rstrip(".")
            == str(request.url.hostname or "").lower().rstrip(".")
            and parsed.port == request.url.port
        )
    except ValueError:
        return False


def _is_loopback_address(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _is_local_request(request: Request) -> bool:
    hostname = str(request.url.hostname or "").lower().rstrip(".")
    client_host = str(request.client.host if request.client else "").lower()
    if hostname == "testserver" and client_host == "testclient":
        return True
    if hostname not in _LOOPBACK_HOSTS:
        return False
    if _is_loopback_address(client_host):
        return True
    # Docker NAT 无法把宿主机回环连接保留成 loopback client IP。该兼容仅在
    # Compose 显式开启，并与 127.0.0.1 host 端口绑定共同构成本地边界。
    return os.environ.get("NIUMA_TRUST_DOCKER_LOOPBACK_PROXY", "").strip().lower() == "true"


def _has_valid_admin_token(request: Request) -> bool:
    configured = str(settings.local_admin_token or "")
    if not configured:
        return False
    scheme, separator, credential = request.headers.get("Authorization", "").partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not credential:
        return False
    return hmac.compare_digest(credential.encode("utf-8"), configured.encode("utf-8"))


def _is_public_path(path: str) -> bool:
    return path in _PUBLIC_PATHS or path.startswith("/static/")


@asynccontextmanager
async def lifespan(app: FastAPI):
    previous_temp = tempfile.tempdir
    previous_temp_env = {name: os.environ.get(name) for name in ("TEMP", "TMP")}
    workflow_job_runner = None
    scheduler = None
    try:
        app.state.media_storage = configure_runtime_media_storage()
        init_db()
        workflow_job_runner = WorkflowJobRunner()
        workflow_job_runner.start()
        app.state.workflow_job_runner = workflow_job_runner
        scheduler = await start_scheduler_background()
        app.state.publish_scheduler = scheduler
        yield
    finally:
        try:
            if workflow_job_runner:
                workflow_job_runner.stop()
        finally:
            try:
                if scheduler:
                    await scheduler.shutdown()
            finally:
                tempfile.tempdir = previous_temp
                for name, value in previous_temp_env.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value


app = FastAPI(
    title=settings.app_name,
    description=settings.app_description,
    version="2.2.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    for error in exc.errors():
        location = tuple(error.get("loc") or ())
        if "selection_profile" in location:
            return JSONResponse(status_code=422, content={"detail": "请选择有效的选片模式"})
    safe_errors = [
        {
            "type": str(error.get("type") or "validation_error"),
            "loc": list(error.get("loc") or ()),
            "msg": str(error.get("msg") or "输入校验失败"),
        }
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": safe_errors})


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    request_path = request.url.path
    request_origin = request.headers.get("Origin", "")

    # ── 1. 本地单用户门禁 ──
    # 正式启动和 Docker 端口均只绑定 loopback；若用户主动改为远程暴露，
    # 页面、API 和媒体必须携带独立 Bearer Token，且 Token 永不下发到浏览器。
    if not _is_public_path(request_path) and not _is_local_request(request):
        if not settings.local_admin_token:
            return JSONResponse(
                status_code=403,
                content={"detail": "NiuMa Studio 管理界面仅允许从本机访问"},
            )
        if not _has_valid_admin_token(request):
            return JSONResponse(
                status_code=401,
                content={"detail": "远程访问需要有效的 LOCAL_ADMIN_TOKEN"},
            )

    # ── 2. 浏览器跨站写保护 ──
    if (
        request_path.startswith("/api/")
        and request.method in _WRITE_METHODS
        and request_origin
        and not _is_same_origin(request, request_origin)
        and not _is_local_origin(request_origin)
    ):
        return JSONResponse(
            status_code=403,
            content={"detail": "已拒绝非本机网页发起的写操作"},
        )

    # ── 3. OPTIONS 预检请求处理 ──
    is_static_asset = request_path.startswith(("/media/", "/static/"))
    if is_static_asset and request.method == "OPTIONS":
        response = Response()
    else:
        response = await call_next(request)

    # ── 4. 静态资源缓存 ──
    if request_path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    # ── 5. CORS / Private Network Access ──
    if is_static_asset:
        allow_origin = _build_allow_origin_header(request_origin)
        response.headers["Access-Control-Allow-Origin"] = allow_origin
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "*"
        # 仅白名单 Origin 返回 Private Network 预检头
        if _is_origin_allowed(request_origin) or _is_local_origin(request_origin):
            response.headers["Access-Control-Allow-Private-Network"] = "true"

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"

    return response


app.mount("/static", StaticFiles(directory=settings.project_root / "app" / "static"), name="static")
app.include_router(pages.router)
app.include_router(ai_prompts.router)
app.include_router(tasks.router)
app.include_router(subtitles.router)
app.include_router(media.router)
app.include_router(publish.router)
app.include_router(content_review.router)
app.include_router(settings_router.router)
app.include_router(system.router)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(
        settings.project_root / "app" / "static" / "img" / "brand" / "niuma-studio-favicon.ico",
        media_type="image/x-icon",
    )


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok", "app": settings.app_name}
