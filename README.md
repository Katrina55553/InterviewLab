# ResumeAgent · AI 面试平台

基于 FastAPI、LangGraph 和 React 的 AI 面试平台，支持账号登录、简历解析与评分、多轮模拟面试和量化报告，并提供 AI 聊天、管理员模型配置及可选语音与视觉建议。

> 当前运行方式和架构以 [平台运行与架构说明](docs/platform.md) 为准。旧匿名 `/api/sessions`、旧诊断页面和旧 WebSocket 协议已退出应用入口，相关代码仅保留作参考与工具复用。

## 功能与使用流程

1. **注册与登录**：普通用户注册，登录后管理自己的简历、面试与聊天记录；管理员通过命令行单独创建。
2. **简历诊断**：在 `/resumes` 上传 PDF、DOCX 或 TXT，异步解析、评分、生成建议及五道主问题。
3. **模拟面试**：从 `/interviews/new` 进入面试室，上传或选择简历，准备完成后开始；同一简历可创建多次独立面试。
4. **多轮问答**：LangGraph 根据回答评估并追问，每道主问题最多两次追问，支持跳过、换个问法和结束面试。
5. **报告与历史**：查看逐轮回答、真实评分、雷达维度与改进建议；恢复面试和读取报告不会重新调用 AI。

其他能力：

- **AI 聊天**：独立聊天会话、附件与 SSE 响应。
- **管理员配置**：管理 OpenAI-compatible 供应商及版本化 Agent，已有面试保留创建时冻结的配置。
- **语音与视觉**：可选讯飞实时转写、长文本 TTS 和独立视觉建议。进入面试室请求摄像头权限并显示本机预览，拒绝或关闭摄像头不阻塞面试；视觉建议不参与答题评分。
- **访问控制**：HttpOnly Cookie 会话、CSRF 校验、资源归属检查与私有文件访问。

默认使用真实模型服务。只有显式设置 `PLATFORM_DEMO_MODE=true` 才启用规则演示，结果带 `demo` 标记；缺少凭据或模型失败不会自动生成成功评分。

## 技术与架构

| 层 | 技术与职责 |
| --- | --- |
| 前端 | React 19、TypeScript、Vite、Tailwind CSS 4、Zustand、React Router 7 |
| 后端 | Python 3.12、FastAPI、SQLAlchemy 2、Alembic |
| AI 编排 | LangGraph、OpenAI-compatible API，支持 DeepSeek 等供应商 |
| 异步任务 | PostgreSQL Outbox、独立 dispatcher、Celery `default` 队列 |
| 数据 | PostgreSQL 16 保存业务事实；Redis 7 用于协调、通知及 Celery |
| 检索 | 复用本地知识库与 RAG 工具；Elasticsearch 8 可选 |
| 部署 | Docker Compose、Nginx |

```text
浏览器 → Nginx → FastAPI 鉴权适配器 → 平台应用服务 → LangGraph / 模型网关
                         │                 │
                         │                 └→ PostgreSQL：快照、命令、评分、事件、Outbox
                         └→ Redis：协调与通知                 │
                                                            ↓
                                              Outbox dispatcher → Celery → 准备 / 报告 / 媒体任务
```

每条面试命令运行一轮图，等待用户的状态持久化到 PostgreSQL。命令通过请求 ID、版本、租约和 owner token 控制重复提交及并发回写。Redis 丢失不改变已有面试事实；失败重试复用已保存的节点进度，不补造评分。详细恢复语义见 [平台文档](docs/platform.md)。

## Docker 快速开始

在仓库根目录执行，以下命令使用 PowerShell：

```powershell
Copy-Item backend/.env.example backend/.env
# 编辑 backend/.env：设置随机 SECRET_KEY，并配置 LLM_*。
# 仅体验演示流程时，显式设置 PLATFORM_DEMO_MODE=true。

docker compose up -d --build
docker compose exec backend python -m app.platform.cli create-admin --username admin
```

打开 [http://localhost:8000](http://localhost:8000)，注册普通用户或使用初始化的管理员登录。管理员命令会交互式要求输入密码。

Compose 会先由 `migrate` 服务执行 Alembic 迁移，再启动后端与异步进程。正常运行需要 `frontend`、`backend`、`postgres`、`redis`、`celery` 和 `outbox`；只启动 Web 服务无法完成简历准备等异步任务。

可选启用 Elasticsearch：

```powershell
docker compose --profile rag up -d
```

未启用 Elasticsearch 时保留本地知识库降级检索。

## 本地开发

准备 Python 3.12、兼容 Vite 8 的 Node.js（建议 22.12+）、可连接的 PostgreSQL 16 与 Redis 7。Python 文件校验还依赖系统 `libmagic`，Docker 镜像已安装该依赖。

仓库的 Compose 默认不向宿主机映射 PostgreSQL、Redis 端口。本地运行后端时，请使用本机数据库与 Redis，或先为容器配置端口映射。

### 1. 配置与初始化后端

从仓库根目录执行：

```powershell
Copy-Item backend/.env.example backend/.env
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

将 `backend/.env` 中连接地址改为本机实际地址，例如：

```dotenv
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/resume_agent
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/2
CORS_ORIGINS=http://localhost:8000,http://localhost:5173
```

同时配置随机 `SECRET_KEY` 和 `LLM_*`，或显式启用演示模式。确认数据库已创建后执行：

```powershell
python -m app.platform.cli migrate
python -m app.platform.cli create-admin --username admin
uvicorn app.main:app --reload --port 8000
```

启动时只验证迁移，不自动建表。

### 2. 启动异步进程

再打开两个终端，均进入 `backend` 并激活同一虚拟环境，分别执行：

```powershell
# 终端二：Celery 消费平台任务，solo 池适用于 Windows 开发
celery -A app.tasks.celery_app worker --loglevel=info --pool=solo -Q default
```

```powershell
# 终端三：发布已提交的 Outbox 任务
python -m app.platform.worker
```

### 3. 启动前端

在仓库根目录打开第四个终端：

```powershell
cd frontend
npm ci
npm run dev
```

打开 [http://localhost:5173](http://localhost:5173)。Vite 将 `/api` 和 `/ws` 代理到本地后端。后端直接访问时提供 [Swagger 文档](http://localhost:8000/docs) 和 [健康检查](http://localhost:8000/health)；Compose 的 Nginx 未代理 `/docs`。

## 主要配置

配置模板见 [backend/.env.example](backend/.env.example)，加载逻辑见 [config.py](backend/app/core/config.py)。后端命令需在 `backend` 目录运行，以读取该目录的 `.env`。

| 配置 | 用途 |
| --- | --- |
| `DATABASE_URL` | PostgreSQL 异步连接；容器用 `postgres`，本地用实际主机地址 |
| `REDIS_URL` | Redis 协调与通知连接 |
| `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | Celery Broker 与结果连接，本地开发也需修改主机地址 |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 默认 OpenAI-compatible 模型服务 |
| `SECRET_KEY` | 会话及供应商密钥保护，部署前替换为随机密钥 |
| `PLATFORM_DEMO_MODE` | 默认 `false`；显式开启规则演示 |
| `CORS_ORIGINS` | 实际浏览器来源列表，以逗号分隔；WebSocket 同样检查来源 |
| `AUTH_COOKIE_SECURE` | HTTPS 部署设为 `true` |
| `AI_TIMEOUT_SECONDS` / `AI_MAX_CONCURRENCY` / `AI_COMMAND_LEASE_SECONDS` | 模型超时、并发与命令租约 |
| `XFYUN_APP_ID` / `XFYUN_API_KEY` / `XFYUN_API_SECRET` | 可选讯飞 ASR / TTS，需分别开通产品 |
| `MAX_UPLOAD_SIZE_MB` / `ALLOWED_EXTENSIONS` | 上传限制；模板为 10 MB、`pdf,docx,txt` |

修改 `SECRET_KEY` 后需要重新保存供应商 API Key，已有面试中冻结的旧凭据也将不可解密。视觉建议需配置独立 vision 供应商及可选 Agent，配置在面试创建时冻结。

## 接口入口

| 入口 | 用途 |
| --- | --- |
| `/api/auth` | 注册、登录、当前用户、退出 |
| `/api/resumes` | 简历上传、列表、准备结果、私有文件与失败重试 |
| `/api/interviews` | 独立面试、快照、命令、事件、报告及报告重试 |
| `/ws/interviews/{id}?after=N` | 面试事件补取与命令提交 |
| `/api/chats` | 聊天会话、附件与 SSE 消息 |
| `/api/admin/providers` / `/api/admin/agents` | 管理员供应商及版本化角色管理 |
| `/api/media` / `/ws/asr/{interview_id}` | 媒体能力、语音合成与实时转写 |
| `/api/interviews/{id}/vision` | 独立视觉建议 |
| `/health` | 后端健康检查 |

修改请求需携带 `X-CSRF-Token`，值来自 `ra_csrf` Cookie。面试命令支持 `start`、`answer`、`skip`、`rephrase`、`end`，例如：

```json
{"request_id":"unique-request-id","type":"answer","expected_question_id":"question-id","version":1,"content":"我的回答"}
```

MCP 入口为 `python -m app.mcp.server`，通过私有环境变量 `RESUME_AGENT_MCP_TOKEN` 绑定有效登录会话，使用相同的权限与应用服务。当前鉴权与接入约束见 [平台文档](docs/platform.md)；[旧 MCP 文档](MCP_README.md) 中的工具清单与流程仅供历史参考。

## 项目结构

```text
ResumeAgent/
├── backend/
│   ├── app/
│   │   ├── platform/          # 当前业务：认证、简历、面试、聊天、管理、媒体
│   │   │   ├── service.py     # 统一面试命令服务
│   │   │   ├── graph.py       # 每条命令运行一轮 LangGraph
│   │   │   ├── worker.py      # Outbox dispatcher 与平台 Celery 任务
│   │   │   └── cli.py         # 迁移、管理员初始化、开发重置
│   │   ├── core/              # 配置、数据库、LLM、RAG 与工具
│   │   ├── mcp/               # 鉴权 MCP 接入
│   │   └── main.py            # 当前路由注册与生命周期
│   ├── migrations/            # 平台数据库版本迁移
│   ├── knowledge/             # 本地题库
│   └── tests/
├── frontend/
│   ├── src/platform/          # 当前平台页面、状态与样式
│   ├── src/App.tsx            # 当前应用路由
│   ├── dist/                  # 前端构建产物，按仓库约定提交
│   └── nginx.conf             # 静态资源、API 与 WebSocket 代理
├── docs/
│   ├── platform.md            # 运行、架构、恢复语义与验收说明
│   └── verification.md        # 部署与测试记录
└── docker-compose.yml
```

旧 `frontend/src/pages/`、stores、`backend/app/api/` 与旧 Agent 代码仍保留；新增平台业务应在 `platform/` 下维护。

## 检查与测试

后端，在 `backend` 目录执行（Ruff 需另行安装）：

```powershell
python -m pip install ruff
python -m ruff check .
python -m pytest tests -q -p no:cacheprovider
```

前端，在 `frontend` 目录执行：

```powershell
npm run lint
npm test
npm run build
```

真实数据库测试需指向隔离测试环境：

```powershell
# 在 backend 目录执行，勿使用个人数据或生产数据库
$env:PLATFORM_TEST_DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/resume_agent_test"
$env:PLATFORM_TEST_REDIS_URL="redis://localhost:6379/15"
python -m pytest tests/test_platform_service.py tests/test_platform_media.py -q -p no:cacheprovider
```

模型替身测试验证契约与故障行为。真实 LLM、ASR、TTS 和视觉能力需分别配置凭据并实联验收，详见 [验证记录](docs/verification.md)。

## 常用运维命令

在仓库根目录执行：

```powershell
docker compose ps
docker compose logs backend celery outbox --tail 100
docker compose up -d --build backend celery outbox
docker compose up -d --build frontend
docker compose down
```

仅在 `ENVIRONMENT=development` 下，可在 `backend` 目录显式重置平台数据：

```powershell
python -m app.platform.cli reset-dev --confirm RESET-PLATFORM
```

该命令删除并重建 `platform_` 表，保留旧版表及原始文件；执行后需重新创建管理员。

## 更多文档

- [平台运行与架构](docs/platform.md)
- [部署与测试记录](docs/verification.md)
- [旧 MCP 文档（历史参考）](MCP_README.md)
- [Agent 开发规范](AGENTS.md)
