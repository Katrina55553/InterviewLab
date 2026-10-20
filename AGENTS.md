# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 项目概述

**AI 面试平台** — 用户登录 → 上传简历 → 解析、评分与改进建议 → LangGraph 多轮模拟面试 → 量化报告；另含 SSE 聊天、管理员模型与 Agent 配置、可选讯飞 ASR/TTS 和视觉建议。当前运行与架构文档以 `docs/platform.md` 为准。

## 当前平台入口

- 后端新业务集中在 `backend/app/platform/`；`api.py`、`chat.py`、`media.py` 为鉴权适配器，`service.py` 为统一面试命令服务，`graph.py` 为每次命令运行一轮的 LangGraph。
- `models.py` 与 `media_models.py` 的 `platform_` 表由 Alembic 管理；先运行 `python -m app.platform.cli migrate`。应用启动只验证迁移，不自动建表。开发重建需显式 `reset-dev --confirm RESET-PLATFORM`。
- `python -m app.platform.cli create-admin --username admin` 初始化管理员；注册接口始终创建普通用户。Cookie 会话、CSRF 和资源归属检查覆盖 REST、WebSocket 与私有文件。
- `python -m app.platform.worker` 是必须运行的 outbox dispatcher，Celery 消费 `default` 队列；不能只启动 Web 服务而省略异步进程。
- PostgreSQL 保存权威快照、真实评估、命令节点进度及事件；Redis 丢失不能改变已有面试事实。恢复读取不能调用 AI，失败重试不能补造评分。
- 前端页面与状态在 `frontend/src/platform/`。旧页面、stores、匿名 API、Agent 与旧 Celery 任务已删除，不要恢复旧协议。公共解析与脱敏在 `backend/app/core/documents.py`；规则解析仅用于显式演示模式。
- `PLATFORM_DEMO_MODE=false` 为默认；只有明确开启后才能生成带演示标记的规则结果。无供应商凭据不代表真实服务已验收。
- 测试：后端 `ruff check .`、`pytest tests -q -p no:cacheprovider`；前端 `npm run lint`、`npm test`、`npm run build`。真实数据库测试通过 `PLATFORM_TEST_DATABASE_URL` 与 `PLATFORM_TEST_REDIS_URL` 指向隔离环境。
- RAG、知识库与 Elasticsearch 已移除。现有工具为 `lookup_resume_field` 和 `verify_code_snippet`。

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | React 19 + TypeScript + Vite + Tailwind CSS 4 + Zustand + React Router v7 |
| 后端 | FastAPI (Python 3.12) + LangGraph (Agent 编排) + Celery + Redis (异步任务) |
| LLM | DeepSeek API（OpenAI 兼容格式），支持任意 OpenAI-compatible 第三方 API |
| 数据库 | PostgreSQL 16 (结构化数据) + Redis 7 (缓存/Celery Broker/WebSocket 消息队列) |
| 部署 | Docker Compose (Nginx + Backend + Celery + PostgreSQL + Redis) |

## 构建与部署

```bash
# 本地开发
cd frontend && npm install && npm run dev    # 前端 dev server (port 5173)
cd backend && uvicorn app.main:app --reload  # 后端 dev server (port 8000)

# Docker 部署
docker compose build
docker compose up -d

# 单独重建某个服务
docker compose up -d --build backend celery
docker compose up -d --build frontend

# 查看日志
docker compose logs backend --tail 50
docker compose logs celery --tail 50
```

前端生产构建：`cd frontend && npm run build`，产物在 `dist/` 目录，由 Nginx 容器直接 serve。

## 核心架构

### 用户流程与 Agent

登录 → 上传简历 → 异步解析、评分和改进建议 → 创建独立面试 → 逐命令运行 LangGraph → 量化报告。`backend/app/platform/graph.py` 包含准备、面试和报告图，服务层负责命令租约、版本校验、节点进度与事件提交。

`backend/app/core/tools.py` 提供简历字段查询与代码验证；新平台通过 `platform/runtime.py` 调用模型。真实模式失败不会自动替换为规则评分，演示模式必须显式开启。

### WebSocket 面试通信

前端 `frontend/src/platform/Interview.tsx` 与 `hooks.ts` 管理面试命令、快照和事件恢复；后端 `backend/app/platform/api.py` 提供 `/ws/interviews/{id}`。命令携带 `request_id`、`expected_question_id` 和 `version`；PostgreSQL 持久事件为重连提供依据。协议和恢复语义见 `docs/platform.md`。

### 公共解析与脱敏

`backend/app/core/documents.py` 提供 PDF/DOCX/TXT 文本提取、手机号/邮箱/身份证脱敏及演示解析。简历准备、聊天附件和模型调用共用这些函数，不依赖旧任务或旧 ORM。

## 关键配置

`backend/.env` 环境变量：

```
LLM_BASE_URL=https://api.deepseek.com   # OpenAI 兼容 API 地址
LLM_API_KEY=sk-xxx                       # API Key（空时真实模式不可用）
LLM_MODEL=deepseek-chat                  # 模型名称
DATABASE_URL=postgresql+asyncpg://...     # PostgreSQL 连接
REDIS_URL=redis://redis:6379/0           # Redis 连接
```

`Settings` 类在 `backend/app/core/config.py`，使用 pydantic-settings，`extra="ignore"` 兼容旧字段。

## 前端状态管理

登录状态在 `frontend/src/platform/auth.ts`；页面数据与事件订阅在 `platform/hooks.ts`，请求在 `platform/api.ts`，其余状态由对应页面管理。

## 注意事项

- `frontend/dist/` 需要提交到 Git（Docker 直接 serve 静态文件）
- Dockerfile 基础镜像用 `python:3-slim`，阿里云服务器需要配置 Docker 镜像源
- Git 推送需要开代理（GGDD 端口 9674），或配置 `git config --global http.proxy http://127.0.0.1:9674`
- DeepSeek 不完全支持 OpenAI 结构化 tool_calls，`llm.py` 有 DSML 文本格式兼容解析
