# Python AI 面试平台

本次扩展保留 FastAPI、LangGraph、React/Zustand、PostgreSQL、Redis。主流程是上传简历 → 评分出题 → 面试 → 报告。旧匿名 `/api/sessions`、独立诊断页面和旧 WebSocket 协议不再对外注册；原工具、解析及 RAG 模块仍被新平台复用。

## 启动

复制 `backend/.env.example` 为 `backend/.env`。设置随机 `SECRET_KEY`，配置 `LLM_*`，或显式设置 `PLATFORM_DEMO_MODE=true` 体验规则生成的演示。默认真实模式不会把模型失败伪装成成功评分。

```powershell
docker compose up -d --build
docker compose exec backend python -m app.platform.cli create-admin --username admin
```

访问 `http://localhost:8000`。注册账号始终为普通用户，管理员单独初始化。`migrate` 服务运行版本迁移后，后端和 Worker 才会启动。可选 Elasticsearch 用 `docker compose --profile rag up -d` 启用；未启用时保留本地知识库降级。

本地开发需 PostgreSQL、Redis，以及分别运行以下进程：

```powershell
cd backend
python -m pip install -r requirements.txt
python -m app.platform.cli migrate
python -m app.platform.cli create-admin --username admin
uvicorn app.main:app --reload --port 8000
# 另一个终端，仍在 backend
celery -A app.tasks.celery_app worker --loglevel=info --pool=solo -Q default
# 第三个终端
python -m app.platform.worker
# 第四个终端，在 frontend
npm ci
npm run dev
```

本地数据库连接使用 `localhost`，容器环境使用 `postgres`、`redis` 服务名。`CORS_ORIGINS` 必须列出实际浏览器地址（默认示例包含 8000、5173），WebSocket 同样检查来源。HTTPS 部署设置 `AUTH_COOKIE_SECURE=true`。密钥加密依赖 `SECRET_KEY`，修改该值后需要重新保存供应商 API Key，旧面试中的冻结凭据也将不可解密。

开发重建必须显式执行，且仅允许 `ENVIRONMENT=development`：

```powershell
python -m app.platform.cli reset-dev --confirm RESET-PLATFORM
```

只删除新平台 `platform_` 表，不触碰旧版表，不删除原始文件。启动不会自动建表或重建数据。旧开发数据不做迁移。

## 架构对照与阶段验收

| 阶段 | AI-Meeting 概念 | Python 实现 | 学习与验证 |
| --- | --- | --- | --- |
| 1 平台基础 | Controller / Facade、Sa-Token | 认证路由、HttpOnly 会话、应用服务、Alembic | 注册两个用户，验证越权拒绝；同一简历创建两次面试 |
| 2 简历准备 | 简历评分、出题云工作流 | 本地 LangGraph 评分与出题节点，Celery 准备任务 | TXT/PDF/DOCX 上传后获得五道题、评分和建议 |
| 3 面试闭环 | 多 Agent、LiteFlow、报告 | 每命令一轮 LangGraph，确定性追问规则，异步报告 | 低分最多两次追问；报告逐轮回放，追问不重复计分 |
| 4 可靠性 | Single-flight、快照、CAS | PG 命令账本与 JSONB 快照、短事务、租约、Redis 协调、outbox | 并发提交、重启、过期执行者回写、重复任务与 Redis 丢失实验 |
| 5 对话配置 | Spring AI、Agent 配置 | AsyncOpenAI 网关、SSE、管理员供应商与角色配置 | 配置版本变化不影响已有面试，密钥不出现在响应中 |
| 6 语音 | AST WebSocket、长文本 TTS | 音频代理、增量归并、有界队列、持久合成任务 | 识别修订不重复；转写可编辑；关闭页面释放麦克风 |
| 7 视觉交付 | 神态工作流、完整部署 | 可选视觉模型、独立反馈、5 秒节流、私有媒体存储 | 摄像头默认关闭，关闭即停止；视觉不影响答题分数 |

PostgreSQL 是权威数据源。Redis 保存协调信息和通知，丢失后可从数据库恢复。简历、独立面试、命令、事件、聊天及媒体任务有清晰归属；文件不公开挂载。

每轮面试图在生成下一问题后结束。等待用户的状态保存在业务快照中，不再维护另一套 LangGraph checkpoint，从而避免双重提交。恢复、历史及报告读取不执行图、不调用模型。

面试命令先在短事务中认领，事务外调用 AI，再以 owner token、租约和版本校验提交。重复请求重放已提交结果；同一请求 ID 不得更换内容。执行者过期后不能覆盖新结果。AI 超时可能是结果未知，此时保留答案并要求用户显式重试；不承诺供应商绝不重复计费。

每个已完成节点另将进度写入同一命令记录。评分完成、追问失败时，显式重试相同答案使用新请求 ID，但从已保存的追问阶段继续，不重新评分。简历准备使用准备时的配置；每次新面试重新冻结当前面试与视觉配置，管理员改动不回写已有面试。

Outbox 与业务状态同事务提交，独立 dispatcher 发布 Celery 任务后标记发送。发布与确认间退出可能重复投递，Worker 通过数据库任务状态和租约拒绝重复执行。终结或结果未知的任务不会被自动再次调用；准备和报告提供显式重试入口。

## 接口与配置

- `/api/auth`：注册、登录、当前用户、退出。可撤销会话令牌只以摘要保存；修改请求携带 `X-CSRF-Token`，值来自 `ra_csrf` Cookie。
- `/api/resumes`：上传、列表、准备结果、私有文件和失败重试。
- `/api/interviews`：创建独立面试、列表、快照、命令、报告、事件及报告重试。
- `/ws/interviews/{id}?after=N`：递增事件补取，也接受相同的面试命令。
- `/api/chats`：会话管理、附件及 SSE 消息；同一请求 ID 完成后可重放，断流保存部分内容。
- `/api/admin/providers`、`/api/admin/agents`：供应商及版本化角色管理。内置场景为 resume/questions/evaluate/followup/report/chat/vision；无场景的 Agent 可用于自定义聊天。
- `/api/media`、`/ws/asr/{interview_id}`：能力查询、语音合成和实时转写。
- `/api/interviews/{id}/vision`：主动上传截图取得独立建议，不保存截图或原始视频。

命令格式：

```json
{"request_id":"unique-request-id","type":"answer","expected_question_id":"question-id","version":1,"content":"我的回答"}
```

动作支持 start/answer/skip/rephrase/end。默认五道主问题，每题最多两次追问；主动跳过主问题记零分，未提问的问题不计入均分。没有可评分回答时得分为空。雷达五维来自真实逐题评分，不从综合分反推。演示数据始终带 `demo` 标记。

讯飞 ASR 需要开通实时转写大模型，配置 `XFYUN_APP_ID/API_KEY/API_SECRET`；浏览器发送 16kHz、单声道、16bit PCM，约 40ms 一帧。TTS 使用长文本任务创建/查询接口，音频下载后保存到私有 uploads 卷。仪态建议使用独立 vision 供应商及可选 vision Agent；配置在面试创建时冻结。

MCP 通过 `python -m app.mcp.server` 运行，环境变量 `RESUME_AGENT_MCP_TOKEN` 绑定有效登录会话。工具参数不能指定任意用户身份；会话退出或过期后工具调用被拒绝。工具与网页使用相同应用服务及版本规则。令牌只应放在本机私有配置中。

## 检查与故障实验

```powershell
# backend
python -m ruff check .
python -m pytest tests -q -p no:cacheprovider
# frontend
npm run lint
npm test
npm run build
```

真实数据库集成测试使用独立测试库，不要指向个人数据或生产数据：

```powershell
$env:PLATFORM_TEST_DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/resume_agent_test"
$env:PLATFORM_TEST_REDIS_URL="redis://localhost:6379/15"
python -m pytest tests/test_platform_service.py tests/test_platform_media.py -q -p no:cacheprovider
```

故障实验：面试时关闭后端再启动；使用两个客户端同时回答同一题；仅删除测试 Redis 中协调键；中断 Worker 后重新投递任务；让旧租约过期后继续回写。预期数据库只接受一个合法结果，恢复问题、评分与历史保持一致，未知结果显示可重试状态。

自动测试中的模型替身验证契约和故障行为，不代表真实供应商验收。LLM、讯飞 ASR、讯飞 TTS、视觉模型必须分别配置凭据后实联验收；缺少凭据的能力明确禁用。部署与测试记录见 `docs/verification.md`。
