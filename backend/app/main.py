"""FastAPI 应用入口

注册路由、中间件、异常处理，管理应用生命周期。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import close_db, init_db
from app.core.redis import close_redis, init_redis
from app.platform import admin, api, auth, chat, media


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # startup
    await init_db()
    await init_redis()
    yield
    # shutdown
    await close_redis()
    await close_db()


# 创建 FastAPI 应用
app = FastAPI(
    title="ResumeAgent Interview Platform API",
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

# Only authenticated platform adapters are exposed; legacy anonymous routes are retired.
app.include_router(auth.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(api.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(media.router, prefix="/api")
app.include_router(api.ws_router)
app.include_router(media.ws_router)


# 统一异常处理
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理器"""
    return JSONResponse(
        status_code=500,
        content={
            "detail": "服务器内部错误",
            "message": str(exc) if not settings.is_production else "请联系管理员",
        },
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """参数错误处理器"""
    return JSONResponse(
        status_code=400,
        content={"detail": "参数错误", "message": str(exc)},
    )


# 健康检查
@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {"status": "ok", "version": "1.0.0"}
