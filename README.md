# 知面 InterviewLab · AI 面试平台

基于 FastAPI、LangGraph 和 React 的 AI 面试平台，支持账号登录、简历解析与评分、多轮模拟面试和量化报告，并提供 AI 聊天、管理员模型配置及可选语音与视觉建议。

运行方式、架构与恢复语义详见 [平台运行与架构说明](docs/platform.md)。

## 功能与使用流程

1. **注册与登录**：普通用户注册，登录后管理自己的简历、面试与聊天记录；管理员通过命令行单独创建。
2. **简历诊断**：在 `/resumes` 上传 PDF、DOCX 或 TXT，异步解析、评分并生成改进建议；可携带简历与诊断结果创建 AI 对话继续讨论。
3. **模拟面试**：从 `/interviews/new` 进入面试室，上传或选择简历，准备完成后开始；首次开始时生成五道主问题，同一简历可创建多次独立面试。
4. **多轮问答**：LangGraph 根据回答评估并追问，每道主问题最多两次追问，支持跳过、换个问法和结束面试。
5. **报告与历史**：查看逐轮回答、真实评分、雷达维度与改进建议；恢复面试和读取报告不会重新调用 AI。

其他能力：

- **AI 聊天**：独立聊天会话、附件与 SSE 响应。
- **管理员配置**：管理 OpenAI-compatible 供应商及版本化 Agent，已有面试保留创建时冻结的配置。
- **Agent 工具**：查询简历字段、分析代码片段，按场景配置工具权限。
- **语音与视觉**：可选讯飞实时转写、长文本 TTS 和独立视觉建议。进入面试室请求摄像头权限并显示本机预览，拒绝或关闭摄像头不阻塞面试；视觉建议不参与答题评分。
- **访问控制**：HttpOnly Cookie 会话、CSRF 校验、资源归属检查与私有文件访问。
- **仓库统计**：导航栏展示 GitHub star / fork 数，后端每日北京时间 03:00 同步，失败时保留上次成功快照。

默认使用真实模型服务。只有显式设置 `PLATFORM_DEMO_MODE=true` 才启用规则演示，结果带 `demo` 标记；缺少凭据或模型失败不会自动生成成功评分。

## 技术与架构

| 层 | 技术与职责 |
| --- | --- |
| 前端 | React 19.2、TypeScript 6.0、Vite 8.3、Tailwind CSS 4.3、Zustand 5、React Router 7.18 |
| 后端 | Python 3.12、FastAPI 0.159、Pydantic 2.14、SQLAlchemy 2.0、Alembic 1.20 |
| AI 编排 | LangGraph 1.2、OpenAI Python SDK 3.36，通过 OpenAI-compatible API 接入 DeepSeek 等供应商 |
| 异步任务 | PostgreSQL Outbox、独立 dispatcher、Celery 5.6 `prepare` / `report` / `default` 队列、Beat 定时调度 |
| 数据 | PostgreSQL 16 保存业务事实；Redis 7 用于协调、通知及 Celery |
| 构建与部署 | Node.js 24 LTS、Docker Compose、Nginx |
| 测试与依赖检查 | pytest、Ruff、Vitest 5、Playwright、pip-audit、npm audit |

精确依赖版本以 `frontend/package-lock.json` 和后端完整哈希锁文件为准；维护方式见[依赖维护](#依赖维护)。

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

Compose 由 `migrate` 服务执行 Alembic 迁移，后端与业务 Worker 等进程等待迁移完成后启动。默认服务包括：

| 服务 | 职责 |
| --- | --- |
| `frontend` / `backend` | Nginx 静态页面与代理 / FastAPI API |
| `postgres` / `redis` | 权威业务数据 / 协调、通知与任务 Broker |
| `celery` | 消费 `prepare,default` 队列 |
| `celery-report` | 独立消费 `report` 队列 |
| `outbox` / `inspector` | 发布持久任务 / 巡检过期执行状态、回收许可 |
| `celery-beat` | 仓库统计定时调度，每个部署只运行一个实例 |
| `metrics` | 内网指标导出 |

只启动 Web 服务无法完成简历准备和报告等异步任务。Prometheus 通过可选 `monitoring` profile 启动，部署与故障处理见 [工程化运行手册](docs/engineering-operations.md)。

## 本地开发

准备 Python 3.12、Node.js 24 LTS、可连接的 PostgreSQL 16 与 Redis 7。前端 Docker 构建与 CI 同样使用 Node.js 24。Python 文件校验还依赖系统 `libmagic`，Docker 镜像已安装该依赖。

仓库的 Compose 默认不向宿主机映射 PostgreSQL、Redis 端口。本地运行后端时，请使用本机数据库与 Redis，或先为容器配置端口映射。

### 1. 配置与初始化后端

从仓库根目录执行：

```powershell
Copy-Item backend/.env.example backend/.env
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --require-hashes -r requirements-dev.txt
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

再打开四个终端，均进入 `backend` 并激活同一虚拟环境，分别执行：

```powershell
# 终端二：Celery 消费平台任务，solo 池适用于 Windows 开发
celery -A app.tasks.celery_app worker --loglevel=info --pool=solo -Q prepare,report,default
```

```powershell
# 终端三：发布已提交的 Outbox 任务
python -m app.platform.worker
```

```powershell
# 终端四：巡检过期命令与任务、回收许可，不调用 AI
python -m app.platform.operations.recovery
```

```powershell
# 终端五：仓库统计定时调度，每个部署只运行一个实例
celery -A app.tasks.celery_app beat --loglevel=info
```

本地单 Worker 消费全部队列；生产 Compose 将准备与报告 Worker 分开，避免报告任务占用准备任务的执行资源。

### 3. 启动前端

在仓库根目录打开第六个终端：

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
| `ENVIRONMENT` | 生产设为 `production`，要求至少 32 字符的非示例 `SECRET_KEY` |
| `PLATFORM_DEMO_MODE` | 默认 `false`；显式开启规则演示 |
| `CORS_ORIGINS` | 实际浏览器来源列表，以逗号分隔；WebSocket 同样检查来源 |
| `AUTH_COOKIE_SECURE` | HTTPS 部署设为 `true` |
| `AI_TIMEOUT_SECONDS` / `AI_MAX_CONCURRENCY` / `AI_COMMAND_LEASE_SECONDS` | 模型超时、并发与命令租约 |
| `AI_PROVIDER_CONCURRENCY` / `AI_INTERACTIVE_RESERVED_PERMITS` | 同一实际 API Key 的共享并发上限与交互保留额度 |
| `GITHUB_TOKEN` | 可选，仅后端用于 GitHub 仓库统计请求，提高访问额度 |
| `XFYUN_APP_ID` / `XFYUN_API_KEY` / `XFYUN_API_SECRET` | 可选讯飞 ASR / TTS，需分别开通产品 |
| `MAX_UPLOAD_SIZE_MB` / `ALLOWED_EXTENSIONS` | 上传限制；模板为 10 MB、`pdf,docx,txt` |

已有数据的环境不能直接替换 `SECRET_KEY`，否则供应商密钥和冻结凭据将无法解密。轮换需停止写入、备份并通过 `python -m app.platform.operations.rekey` 重加密，具体步骤见 [密钥轮换说明](docs/engineering-operations.md#更换-secret_key)。视觉建议需配置独立 vision 供应商及可选 Agent，配置在面试创建时冻结。完整容量、配额与恢复参数见 [运行策略](docs/engineering-operations.md#运行策略)。

## 接口入口

| 入口 | 用途 |
| --- | --- |
| `/api/auth` | 注册、登录、当前用户、退出 |
| `/api/resumes` | 简历上传、列表、准备结果、私有文件与失败重试 |
| `/api/interviews` | 独立面试、快照、命令、事件、报告及报告重试 |
| `/ws/interviews/{id}?after=N` | 面试事件补取与命令提交 |
| `/api/chats` | 聊天会话、附件与 SSE 消息 |
| `/api/admin/providers` / `/api/admin/agents` | 管理员供应商及版本化角色管理 |
| `/api/admin/diagnostics` | 管理员聚合诊断 |
| `/api/repository/stats` | 登录后读取仓库统计缓存 |
| `/api/media` / `/ws/asr/{interview_id}` | 媒体能力、语音合成与实时转写 |
| `/api/interviews/{id}/vision` | 独立视觉建议 |
| `/health` / `/health/live` / `/health/ready` | 存活检查 / 存活检查 / 就绪与依赖检查 |

修改请求需携带 `X-CSRF-Token`，值来自 `ra_csrf` Cookie。面试命令支持 `start`、`answer`、`skip`、`rephrase`、`end`，例如：

```json
{"request_id":"unique-request-id","type":"answer","expected_question_id":"question-id","version":1,"content":"我的回答"}
```

## 项目结构

```text
InterviewLab/
├── backend/
│   ├── app/
│   │   ├── platform/          # 认证、简历、面试、聊天、管理、媒体
│   │   │   ├── service.py     # 统一面试命令服务
│   │   │   ├── graph.py       # 每条命令运行一轮 LangGraph
│   │   │   ├── worker.py      # Outbox dispatcher 与平台 Celery 任务
│   │   │   ├── ai/            # 调用准入、共享许可与结果复用
│   │   │   ├── operations/    # 恢复巡检、健康、指标与密钥轮换
│   │   │   └── cli.py         # 迁移、管理员初始化、开发重置
│   │   ├── core/              # 配置、数据库、LLM、公共文档解析与工具
│   │   │   └── documents.py   # 文本提取、脱敏与演示解析
│   │   ├── tasks/celery_app.py # Celery 配置与平台任务加载
│   │   └── main.py            # 路由注册与生命周期
│   ├── migrations/            # 平台数据库版本迁移
│   ├── requirements.in        # 运行依赖输入，requirements.txt 为完整哈希锁
│   ├── requirements-dev.in    # 开发依赖输入，requirements-dev.txt 约束到运行锁
│   ├── scripts/lock_dependencies.py # 统一重生成运行/开发锁文件
│   └── tests/
├── frontend/
│   ├── src/platform/          # 平台页面、状态与样式
│   ├── src/App.tsx            # 应用路由
│   ├── dist/                  # 前端构建产物，按仓库约定提交
│   └── nginx.conf             # 静态资源、API 与 WebSocket 代理
├── docs/
│   ├── platform.md            # 运行、架构、恢复语义与验收说明
│   ├── engineering-operations.md # 队列、监控、发布与故障处理
│   └── verification.md        # 部署与测试记录
├── operations/                # Prometheus 配置、告警与仪表盘
├── scripts/                   # 发布与回退脚本
└── docker-compose.yml
```

前后端业务分别在各自的 `platform/` 目录维护。后端公共解析与脱敏由 `core/documents.py` 提供，模型网关与工具权限由 `platform/runtime.py` 管理。

## 依赖维护

后端 `requirements.in` 声明运行依赖，`requirements-dev.in` 声明测试和开发工具；对应的 `.txt` 文件锁定全部传递依赖及分发包 SHA-256。开发锁通过运行锁约束公共包，确保本地测试、CI 与 Docker 使用相同的运行依赖。Docker 只安装 `requirements.txt`。

在 `backend` 目录、已安装开发依赖的虚拟环境中执行：

```powershell
# 修改 .in 后同步锁文件；保留仍满足约束的已有版本
python scripts/lock_dependencies.py
# 主动更新约束范围内的版本，然后运行全部检查
python scripts/lock_dependencies.py --upgrade
python -m pip install --require-hashes -r requirements-dev.txt
```

前端使用 Node.js 24 LTS 和 `npm ci`，依赖修改时同步 `package-lock.json`，构建后同步提交 `frontend/dist/`。CI 对 Python 完整开发锁和 npm 依赖执行安全审计。

## 检查与测试

后端，在 `backend` 目录执行；开发锁文件包含 Ruff、pytest 和依赖审计工具：

```powershell
python -m pip install --require-hashes -r requirements-dev.txt
python -m pip check
python -m pip_audit --disable-pip --require-hashes -r requirements-dev.txt
python -m ruff check .
python -m pytest tests -q -p no:cacheprovider
```

前端，在 `frontend` 目录执行：

```powershell
npm run lint
npm test
npm run test:coverage
npm audit --audit-level=high
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

2026-10-23 依赖升级验收：后端 **267 passed / 8 skipped**，前端 **44 passed**；lint、类型检查、覆盖率执行、生产构建，以及普通用户和管理员两套浏览器流程均通过。Python/npm 依赖审计未发现已知漏洞。后端实测使用隔离 PostgreSQL 18.3 / Redis 7.4.2，生产与 CI 仍配置 PostgreSQL 16 / Redis 7；本轮尚未验证升级后的 Docker 镜像构建、远端 CI 和真实供应商联调。完整环境及测试边界见[验收记录](docs/verification.md#2026-10-23-依赖与构建环境升级)。

2026-10-24 已补齐升级后的 Docker 全栈复测：PostgreSQL **16.15** / Redis **7.4.11** 下，镜像构建、9 项故障恢复、两项容量目标、普通用户和管理员浏览器流程均通过。修正了容量探针在本机因反复创建 HTTP 客户端而漏过并发窗口的问题，保留原有门槛。首次 npm 审计发现的 `zod` 间接开发依赖 **1 项 High 已修复**：升级至 **4.6.7** 后，官方审计恢复 **0 漏洞**，前端 lint、44 项测试、覆盖率及生产构建均通过；真实供应商和远端发布仍需验收。详见[全栈记录](docs/verification.md#2026-10-24-docker-全栈复测)及[安全修复记录](docs/verification.md#2026-10-24-zod-安全修复)。

## 常用运维命令

在仓库根目录执行：

```powershell
docker compose ps
docker compose logs backend celery celery-report outbox inspector celery-beat --tail 100
docker compose up -d --build migrate backend celery celery-report outbox inspector metrics celery-beat
docker compose up -d --build frontend
docker compose exec backend python -m app.platform.cli probe-jobs
docker compose down
```

上述重建命令适用于开发环境；生产发布、备份与回退请按 [工程化运行手册](docs/engineering-operations.md#备份发布和回退) 执行。

启用可选监控：

```powershell
docker compose --profile monitoring up -d prometheus
```

`metrics` 仅在容器内网的 9090 端口提供 `/metrics`，不通过 Nginx 公开。仓库统计的缓存行为和手动刷新命令见 [仓库统计说明](docs/platform.md#github-仓库统计)。

仅在 `ENVIRONMENT=development` 下，可在 `backend` 目录显式重置平台数据：

```powershell
python -m app.platform.cli reset-dev --confirm RESET-PLATFORM
```

该命令删除并重建 `platform_` 表，不删除原始文件；执行后需重新创建管理员。

## 更多文档

- [平台运行与架构](docs/platform.md)
- [工程化运行手册](docs/engineering-operations.md)
- [工程化实施方案](docs/engineering-plan.md)
- [部署与测试记录](docs/verification.md)
- [Agent 开发规范](AGENTS.md)
