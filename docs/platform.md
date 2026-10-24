# Python AI 面试平台

工程化治理与恢复设计见 [AI-Meeting 工程化实施方案](engineering-plan.md)，启动、队列、监控、发布及故障处理见 [工程化运行手册](engineering-operations.md)。

本次扩展保留 FastAPI、LangGraph、React/Zustand、PostgreSQL、Redis。登录后首页 `/` 自动跳转至简历诊断 `/resumes`，不再提供工作台汇总页。简历诊断与模拟面试提供独立入口：`/resumes` 上传简历查看评分和建议；`/interviews/new` 可直接进入面试室，再在室内上传或选择简历，简历在面试对话区收集，准备完成后点击开始即直接出题，最终生成报告。进入面试室即请求摄像头权限，右侧显示本机预览；未授权或手动关闭不阻塞面试。摄像头在简历收集与问答之间保持开启，离开面试室或结束面试后释放。诊断页保留携带简历进入面试室的快捷入口。后端简历准备任务仅解析、评分并生成改进建议，诊断页可携带简历与诊断结果创建 AI 对话继续讨论；五道主问题在模拟面试首次开始命令中生成，未选择或未准备好的简历不会创建面试记录。旧页面、状态、匿名 API、Agent 与旧 Celery 任务已移除。公共文本提取、脱敏及演示解析位于 `backend/app/core/documents.py`；保留简历字段查询和代码验证工具，已移除 RAG 与 Elasticsearch。

## 启动

复制 `backend/.env.example` 为 `backend/.env`。设置随机 `SECRET_KEY`，登录后在 `/settings/ai` 为当前账户配置个人模型连接；或显式设置 `PLATFORM_DEMO_MODE=true` 体验规则生成的演示。默认真实模式不会把模型失败伪装成成功评分。

```powershell
docker compose up -d --build
docker compose exec backend python -m app.platform.cli create-admin --username admin
```

访问 `http://localhost:8000`。注册账号始终为普通用户，管理员单独初始化。`migrate` 服务运行版本迁移后，后端和 Worker 才会启动。

本地开发需 PostgreSQL、Redis，以及分别运行以下进程：

```powershell
cd backend
python -m pip install --require-hashes -r requirements-dev.txt
python -m app.platform.cli migrate
python -m app.platform.cli create-admin --username admin
uvicorn app.main:app --reload --port 8000
# 另一个终端，仍在 backend
celery -A app.tasks.celery_app worker --loglevel=info --pool=solo -Q prepare,report,default
# 第三个终端
python -m app.platform.worker
# 第四个终端，仍在 backend
python -m app.platform.operations.recovery
# 第五个终端，仍在 backend：仓库统计定时调度，只运行一个实例
celery -A app.tasks.celery_app beat --loglevel=info
# 第六个终端，在 frontend
# 使用 Node.js 24 LTS，与 Docker / CI 一致
npm ci
npm run dev
```

以上单 Worker 命令适合本地开发，会消费全部任务队列；准备/报告资源隔离的生产启动方式见[工程化运行手册](engineering-operations.md)。

本地数据库连接使用 `localhost`，容器环境使用 `postgres`、`redis` 服务名。`CORS_ORIGINS` 必须列出实际浏览器地址（默认示例包含 8000、5173），WebSocket 同样检查来源。HTTPS 部署设置 `AUTH_COOKIE_SECURE=true`。密钥加密依赖 `SECRET_KEY`，修改该值后需要重新保存供应商 API Key，旧面试中的冻结凭据也将不可解密。

Compose 中后端仅通过容器内网接收请求，设置 `FORWARDED_ALLOW_IPS=*` 信任代理头；公开入口 Nginx 覆盖 `X-Forwarded-For` 为连接的客户端 IP，避免登录限流把所有用户算作同一个代理地址，也避免客户端伪造该头绕过限流。不要为此后端直接发布公网端口。若 Nginx 前另有负载均衡器，须先配置 Nginx 的可信代理与真实 IP；本地直接运行 Uvicorn 仍使用其默认代理信任范围。

开发重建必须显式执行，且仅允许 `ENVIRONMENT=development`：

```powershell
python -m app.platform.cli reset-dev --confirm RESET-PLATFORM
```

只删除新平台 `platform_` 表，不触碰旧版表，不删除原始文件。启动不会自动建表或重建数据。旧开发数据不做迁移。

## 个人模型连接

所有登录账户均可使用侧栏「AI 设置」。页面复用 Model Connection Kit 0.1.0，前端包随仓库保存在 `frontend/vendor/`，宿主适配器通过鉴权 API 访问后端。

- 「连接」将 API Key 单向提交并加密保存，仅更新当前账户的认证；返回值不包含原文或加密密钥。「保存」单独持久化不含秘密的模型选择；刷新页面可恢复。
- 「测试连接」发送固定提示词、最多生成 1 Token，15 秒截止，不自动重试、不写入用户聊天，也不自动保存模型选择。测试可能计费，并使用现有调用频率、Token 预留和审计控制。
- 内置 DeepSeek 提供预设模型 ID；自定义接口可填写实际模型 ID、输入能力和输出上限。当前支持 API Key 和自定义接口，订阅/OAuth 登录尚未接入。
- 用户配置按账户隔离。聊天每次回复读取该用户当前选择；新简历诊断和新面试冻结该用户的模型及加密凭据，后续修改不重写历史事实。未配置时明确提示，不回退到管理员模型或环境变量中的平台 Key。旧版平台凭据快照只允许读取历史，需重新创建诊断或面试才能继续 AI 调用。
- 断开认证删除对应账户的凭据，并移除使用该认证的当前选择；已冻结的凭据也会在下一次调用准入时被拒绝。已发送给服务商的请求无法由本地撤回。
- `USER_LLM_ALLOWED_BASE_URLS` 由服务端配置完整可信 Base URL，默认包含常见 OpenAI 兼容服务。仅 HTTPS/443，校验所有 DNS 结果为公网地址，固定请求 IP 并保留原域名 TLS 验证，不跟随重定向。局域网模型不能通过此公网代理接入。

ASR/TTS 仍为单独的讯飞服务配置；个人 LLM Key 不等于讯飞凭据。管理员仍管理场景提示词、工具及停用策略。`SECRET_KEY` 重加密工具覆盖个人凭据和冻结快照。

## GitHub 仓库统计

导航栏显示 `Katrina55553/InterviewLab` 的 star / fork 数。Compose 的 `celery-beat` 服务每天北京时间 **03:00** 投递同步任务，由现有 `default` 队列 Worker 消费；Beat 启动时还会投递一次初始化任务，仅在缓存缺失或损坏时请求 GitHub。每个部署只运行一个 Beat，调度文件保存在独立 `celerybeat` 卷。

后端请求 GitHub 的超时为10秒，可在 `backend/.env` 配置可选 `GITHUB_TOKEN` 提高额度，公开仓库默认匿名访问。Token 只用于后端请求，不返回浏览器。网络故障、限流、GitHub 服务器错误或 Redis 故障最多重试3次，依次等待1、2、4分钟；失败日志为 `repository_stats_retrying` / `repository_stats_failed`，成功日志为 `repository_stats_updated`。

成功快照包含 star、fork 与 UTC 更新时间，整体写入独立 Redis 键且不设 TTL。失败不会覆盖旧值；Redis 数据丢失后使用默认值 **4 / 0**，重启 Beat 可触发补取，或等待下次每日同步。统计不属于面试事实，不新增 PostgreSQL 表或迁移。

登录后的 `GET /api/repository/stats` 仅读取缓存，返回 `{ stars, forks, updated_at, source }`；真实快照的 `source` 为 `github`，无可用缓存时为 `fallback` 且时间为 `null`。前端挂载时读取，页面可见时每小时重读；请求失败或缓存降级不会覆盖页面已显示的真实数字。仅更新数字无需重新构建前端。

已有部署发布本次代码后，需同时更新后端、准备 Worker 与前端，并启动新的 Beat 服务：

```powershell
docker compose up -d --build backend celery celery-report frontend celery-beat
docker compose logs celery-beat celery --tail 50
```

需要手动立即刷新时（从仓库根目录执行）：

```powershell
docker compose exec backend celery -A app.tasks.celery_app call app.tasks.repository_stats.refresh_repository_stats --queue default
```

## 架构对照与阶段验收

| 阶段 | AI-Meeting 概念 | Python 实现 | 学习与验证 |
| --- | --- | --- | --- |
| 1 平台基础 | Controller / Facade、Sa-Token | 认证路由、HttpOnly 会话、应用服务、Alembic | 注册两个用户，验证越权拒绝；同一简历创建两次面试 |
| 2 简历准备 | 简历评分、出题云工作流 | 本地 LangGraph 诊断节点，Celery 准备任务 | TXT/PDF/DOCX 上传后获得解析、评分和建议；开始模拟面试时生成五道题 |
| 3 面试闭环 | 多 Agent、LiteFlow、报告 | 每命令一轮 LangGraph，确定性追问规则，异步报告 | 低分最多两次追问；报告逐轮回放，追问不重复计分 |
| 4 可靠性 | Single-flight、快照、CAS | PG 命令账本与 JSONB 快照、短事务、租约、Redis 协调、outbox | 并发提交、重启、过期执行者回写、重复任务与 Redis 丢失实验 |
| 5 对话配置 | Spring AI、Agent 配置 | AsyncOpenAI 网关、SSE、管理员供应商与角色配置 | 配置版本变化不影响已有面试，密钥不出现在响应中 |
| 6 语音 | AST WebSocket、长文本 TTS | 音频代理、增量归并、有界队列、持久合成任务 | 识别修订不重复；转写可编辑；关闭页面释放麦克风 |
| 7 视觉交付 | 神态工作流、完整部署 | 可选视觉模型、独立反馈、5 秒节流、私有媒体存储 | 进入面试室请求开启摄像头，关闭即停止；视觉不影响答题分数 |

PostgreSQL 是权威数据源。Redis 保存协调信息和通知，丢失后可从数据库恢复。简历、独立面试、命令、事件、聊天及媒体任务有清晰归属；文件不公开挂载。

每轮面试图在生成下一问题后结束。等待用户的状态保存在业务快照中，不再维护另一套 LangGraph checkpoint，从而避免双重提交。恢复、历史及报告读取不执行图、不调用模型。

面试命令先在短事务中认领，事务外调用 AI，再以 owner token、租约和版本校验提交。重复请求重放已提交结果；同一请求 ID 不得更换内容。执行者过期后不能覆盖新结果。AI 超时可能是结果未知，此时保留答案并要求用户显式重试；不承诺供应商绝不重复计费。

每个已完成节点另将进度写入同一命令记录。评分完成、追问失败时，显式重试相同答案使用新请求 ID，但从已保存的追问阶段继续，不重新评分。简历诊断使用准备时的配置，面试出题使用该次面试冻结的配置；每次新面试重新冻结当前面试与视觉配置，管理员改动不回写已有面试。

Outbox 与业务状态同事务提交，独立 dispatcher 发布 Celery 任务后标记发送。发布与确认间退出可能重复投递，Worker 通过数据库任务状态和租约拒绝重复执行。终结或结果未知的任务不会被自动再次调用；准备和报告提供显式重试入口。

## 接口与配置

### 面经社区页面

登录后通过侧栏进入 `/community`。页面提供关键词与公司搜索、岗位方向和精确话题筛选、推荐/最新排序、分页、全文阅读、点赞、评论，以及支持 Markdown 预览的面经编辑器；移动端使用单列布局。点赞同时承担标记稍后回看的作用，统一使用「点赞」和「我的点赞」，没有独立收藏功能。

社区数据由 PostgreSQL 持久化，迁移版本为 `20261024_0006`，不使用浏览器 localStorage/sessionStorage，不自动上传旧预览版的本机内容，也不预置模拟帖子或点赞数。发布后的面经与评论对所有登录用户可见，作者由服务端会话确定。点赞关系以 `(post_id, user_id)` 为唯一键，重复点赞/取消点赞不会累计错误计数；发布与评论使用客户端 UUID 支持相同内容重试。

关闭编辑弹窗或点击「暂存关闭」会把未发布草稿保存到账号，草稿仅本人可见，其他设备登录后可继续编辑。草稿带版本校验，过期页面不能覆盖更新的草稿；保存失败会保留编辑窗口与内容。发布成功会清理匹配版本的草稿。所有写接口沿用 Cookie 会话和 CSRF 校验；读取失败不会回退到演示内容。

接口为 `GET/POST /api/community/posts`、`GET /api/community/posts/{id}`、`PUT/DELETE /api/community/posts/{id}/like`、`GET/POST /api/community/posts/{id}/comments`、`GET/PUT /api/community/draft` 与 `GET /api/community/meta`。列表搜索、排序和分页在数据库执行，评论每页 30 条，统计来自实际帖子与点赞关系。

部署时先重建后端镜像，运行 `docker compose run --rm migrate`，再更新 backend、frontend 和共用该后端镜像的 Worker/巡检服务，防止迁移版本检查不一致。

### 现有平台接口

- `/api/auth`：注册、登录、当前用户、退出。可撤销会话令牌只以摘要保存；修改请求携带 `X-CSRF-Token`，值来自 `ra_csrf` Cookie。
- `/api/resumes`：上传、列表、准备结果、私有文件和失败重试。
- `/api/interviews`：创建独立面试、列表、快照、命令、报告、事件及报告重试。
- `/ws/interviews/{id}?after=N`：递增事件补取，也接受相同的面试命令。
- `/api/chats`：会话管理、附件及 SSE 消息；同一请求 ID 完成后可重放，断流保存部分内容。
- `/api/admin/providers`、`/api/admin/agents`：供应商及版本化角色管理。内置场景为 resume/questions/evaluate/followup/report/chat/vision；无场景的 Agent 可用于自定义聊天。
- `/api/media`、`/ws/asr/{interview_id}`：能力查询、语音合成和实时转写。
- `/api/interviews/{id}/vision`：视觉能力启用时上传截图取得独立建议，不保存截图或原始视频。

命令格式：

```json
{"request_id":"unique-request-id","type":"answer","expected_question_id":"question-id","version":1,"content":"我的回答"}
```

动作支持 start/answer/skip/rephrase/end。默认五道主问题，每题最多两次追问；主动跳过主问题记零分，未提问的问题不计入均分。没有可评分回答时得分为空。雷达五维来自真实逐题评分，不从综合分反推。演示数据始终带 `demo` 标记。

讯飞 ASR 需要开通实时转写大模型，配置 `XFYUN_APP_ID/API_KEY/API_SECRET`；浏览器发送 16kHz、单声道、16bit PCM，约 40ms 一帧。TTS 使用长文本任务创建/查询接口，音频下载后保存到私有 uploads 卷。仪态建议使用独立 vision 供应商及可选 vision Agent；配置在面试创建时冻结。

TTS 同一面试和文本复用进行中或成功的任务。供应商明确拒绝或任务明确失败后，用户再次点击朗读会原子认领一次新尝试；并发点击不会重复合成。结果未知的请求不会自动重发，继续保留原状态。

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
