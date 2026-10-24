"""FastAPI 应用入口

注册路由、中间件、异常处理，管理应用生命周期。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.core.config import settings
from app.core.database import close_db, init_db
from app.core.redis import close_redis, init_redis
from app.platform import admin, api, auth, chat, community, media, repository_stats, user_settings
from app.platform.contracts import PlatformError, TraceMiddleware, error_payload, traced
from app.platform.operations.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # startup
    settings.validate_runtime()
    configure_logging()
    await init_db()
    await init_redis()
    yield
    # shutdown
    await close_redis()
    await close_db()


# 创建 FastAPI 应用
app = FastAPI(
    title="InterviewLab API",
    description="简历评分、LangGraph 面试、AI 对话与语音平台",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(auth.AuthCSRFMiddleware)
app.add_middleware(TraceMiddleware)

# Only authenticated platform adapters are exposed; legacy anonymous routes are retired.
app.include_router(auth.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(api.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(media.router, prefix="/api")
app.include_router(repository_stats.router, prefix="/api")
app.include_router(user_settings.router, prefix="/api")
app.include_router(community.router, prefix="/api")
app.include_router(api.ws_router)
app.include_router(media.ws_router)


# 统一异常处理
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理器"""
    import logging
    # Starlette's outer error handler runs after the tracing middleware unwinds.
    with traced(trace_id=request.state.trace_id):
        logging.getLogger(__name__).error("request_failed", exc_info=True)
        payload = error_payload(exc)
    return JSONResponse(status_code=500, content={**payload, "detail": payload["message"]},
                        headers={"x-trace-id": payload["trace_id"]})


@app.exception_handler(HTTPException)
@app.exception_handler(PlatformError)
async def platform_error_handler(request: Request, exc: Exception):
    payload = error_payload(exc)
    return JSONResponse(status_code=getattr(exc, "status", getattr(exc, "status_code", 503)),
                        content={**payload, "detail": payload["message"]})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    payload = error_payload(HTTPException(422, "请求格式无效"))
    return JSONResponse(status_code=422, content={**payload, "detail": payload["message"]})


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """参数错误处理器"""
    payload = error_payload(HTTPException(400, "参数错误"))
    return JSONResponse(status_code=400, content={**payload, "detail": payload["message"]})


# 健康检查
@app.get("/health")
@app.get("/health/live")
async def health_check():
    """健康检查端点"""
    return {"status": "ok", "version": "1.0.0"}


@app.get("/health/ready")
async def health_ready():
    from app.platform.operations.health import readiness

    body, status = await readiness()
    return JSONResponse(body, status_code=status)
