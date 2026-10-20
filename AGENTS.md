# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 项目概述

**AI 面试平台** — 用户登录 → 上传简历 → 解析、评分与五题生成 → LangGraph 多轮模拟面试 → 量化报告；另含 SSE 聊天、管理员模型与 Agent 配置、可选讯飞 ASR/TTS 和视觉建议。当前运行与架构文档以 `docs/platform.md` 为准。

## 当前平台入口

- 后端新业务集中在 `backend/app/platform/`；`api.py`、`chat.py`、`media.py` 为鉴权适配器，`service.py` 为统一面试命令服务，`graph.py` 为每次命令运行一轮的 LangGraph。
- `models.py` 与 `media_models.py` 的 `platform_` 表由 Alembic 管理；先运行 `python -m app.platform.cli migrate`。应用启动只验证迁移，不自动建表。开发重建需显式 `reset-dev --confirm RESET-PLATFORM`。
- `python -m app.platform.cli create-admin --username admin` 初始化管理员；注册接口始终创建普通用户。Cookie 会话、CSRF 和资源归属检查覆盖 REST、WebSocket 与私有文件。
- `python -m app.platform.worker` 是必须运行的 outbox dispatcher，Celery 消费 `default` 队列；不能只启动 Web 服务而省略异步进程。
- PostgreSQL 保存权威快照、真实评估、命令节点进度及事件；Redis 丢失不能改变已有面试事实。恢复读取不能调用 AI，失败重试不能补造评分。
- 前端新页面与状态在 `frontend/src/platform/`。旧 `src/pages/`、stores 和后端匿名路由保留作参考，但已从应用路由移除。修改新平台不要继续扩展旧协议。
- `PLATFORM_DEMO_MODE=false` 为默认；只有明确开启后才能生成带演示标记的规则结果。无供应商凭据不代表真实服务已验收。
- 测试：后端 `ruff check .`、`pytest tests -q -p no:cacheprovider`；前端 `npm run lint`、`npm test`、`npm run build`。真实数据库测试通过 `PLATFORM_TEST_DATABASE_URL` 与 `PLATFORM_TEST_REDIS_URL` 指向隔离环境。
- 以下旧版模块说明用于理解被复用的工具、解析和 RAG，不代表当前页面或 API 流程。

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | React 18 + TypeScript + Vite + Tailwind CSS 4 + Zustand + React Router v6 + Axios |
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

### 用户流程

1. **简历上传** — PDF/Word/TXT，四重校验（扩展名+MIME+魔数+大小），Celery 异步解析
2. **解析确认** — AI 结构化提取（DeepSeek LLM 或规则降级），用户内联编辑修正
3. **诊断报告** — AI 识别存疑点（高/中/低优先级），用户勾选面试范围
4. **模拟面试** — WebSocket 实时对话，LangGraph 状态机驱动动态追问
5. **评估报告** — 可信度评分 + 逐点反馈 + 改进建议

### 面试 Agent 架构

核心循环在 `backend/app/agent/nodes/`：

```
question.py → collect.py → evaluate.py → (条件分支)
    │                              │
    │                              ├─ follow_up → 回到 question
    │                              ├─ next_point → 切换存疑点，回到 question
    │                              └─ report → report.py 生成报告
```

**Tool Calling**：`backend/app/core/tools.py` 定义 3 个工具，面试 Agent 可自主决定调用：
- `search_knowledge_base` — 从 RAG 知识库检索面试题
- `lookup_resume_field` — 查看简历具体字段
- `verify_code_snippet` — 验证代码片段正确性

**RAG 知识库**：`backend/knowledge/` 目录下的 JSON 文件，`backend/app/core/rag.py` 提供向量检索 + 关键词降级的混合检索。

**追问防死循环**：单点最多 3 轮（`agent/rules.py`），用户可跳过，连续异常 3 次熔断。

### WebSocket 面试通信

- 前端：`frontend/src/stores/interviewStore.ts` 管理 WS 连接，支持断线自动重连（指数退避，最多 5 次）和消息队列缓存
- 后端：`backend/app/api/ws.py` 处理 WS 消息，Redis 缓存未消费消息（`backend/app/core/msg_cache.py`），重连时自动补推
- 消息协议：客户端发 `{type: "answer|skip|rephrase|start", content}`，服务端推 `{type: "question|status|complete|error"}`

### LLM 调用层

`backend/app/core/llm.py` 提供统一接口：
- `call_llm(system, user)` → 文本响应
- `call_llm_json(system, user)` → JSON 响应（自动解析）
- `call_llm_with_tools(system, user, tools)` → 支持 Tool Calling（兼容 DeepSeek 的 DSML 文本格式）
- 所有 LLM 节点都有降级方案：LLM 不可用时切换到规则/模板

### 敏感信息脱敏

三层脱敏（`backend/app/utils/security/masking.py`）：
1. LLM prompt 发送前：正则替换手机号/邮箱/身份证
2. API 响应返回前：mask_phone/mask_email/mask_name
3. DB 存储前：raw_text 字段脱敏后存储

## 关键配置

`backend/.env` 环境变量：

```
LLM_BASE_URL=https://api.deepseek.com   # OpenAI 兼容 API 地址
LLM_API_KEY=sk-xxx                       # API Key（空则用规则降级）
LLM_MODEL=deepseek-chat                  # 模型名称
DATABASE_URL=postgresql+asyncpg://...     # PostgreSQL 连接
REDIS_URL=redis://redis:6379/0           # Redis 连接
```

`Settings` 类在 `backend/app/core/config.py`，使用 pydantic-settings，`extra="ignore"` 兼容旧字段。

## 前端状态管理

Zustand stores 在 `frontend/src/stores/`：
- `sessionStore` — 上传/解析轮询
- `diagnoseStore` — 诊断结果 + 存疑点选择（带轮询）
- `interviewStore` — WebSocket 面试（连接/重连/消息队列/Tool Calling）
- `reportStore` — 评估报告

## 注意事项

- `frontend/dist/` 需要提交到 Git（Docker 直接 serve 静态文件）
- Dockerfile 基础镜像用 `python:3-slim`，阿里云服务器需要配置 Docker 镜像源
- Git 推送需要开代理（GGDD 端口 9674），或配置 `git config --global http.proxy http://127.0.0.1:9674`
- DeepSeek 不完全支持 OpenAI 结构化 tool_calls，`llm.py` 有 DSML 文本格式兼容解析
