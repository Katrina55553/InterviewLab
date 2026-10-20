# 工程化运行与故障手册

本轮实现对应 [实施方案](engineering-plan.md)。业务入口仍以 [平台文档](platform.md) 为准；验收记录见 [verification.md](verification.md)。示例命令从仓库根目录执行，Python 本地命令另注明工作目录。

## 启动与迁移

已有环境保留私有 `backend/.env`、数据库和上传卷。新环境才从 `.env.example` 配置；生产发布不会复制示例配置。`SECRET_KEY` 必须是至少32字符的稳定随机值，生产/预生产拒绝示例密钥。真实模式默认开启，供应商未配置或失败时返回错误。

```bash
docker compose build
docker compose up -d --wait --wait-timeout 180
docker compose exec backend python -m app.platform.cli create-admin --username admin
docker compose exec backend python -m app.platform.cli probe-jobs
```

`migrate` 服务执行增量 Alembic 迁移。`0002` 新建许可、Flight、配置修订、实例心跳并补充投递字段；`0003` 新建聚合计数器；`0004` 增加任务时间和进程熔断观测。配置历史只为迁移时的当前版本建立基线，不补造旧历史。启动从发布包读取 Alembic heads 并校验数据库；禁止用 `reset-dev` 升级现有环境。

本地开发需分别运行：

```bash
cd backend
python -m app.platform.cli migrate
uvicorn app.main:app --reload
python -m app.platform.worker
python -m app.platform.operations.recovery
celery -A app.tasks.celery_app worker --concurrency=2 -Q prepare,default
celery -A app.tasks.celery_app worker --concurrency=1 -Q report
```

上述长驻命令各占一个终端；前端另在 `frontend` 执行 `npm run dev`。开发可用一个 Worker 消费 `prepare,report,default`，但不提供报告资源隔离。生产 Compose 已分开准备与报告 Worker，预取为1，任务 late ack。

Compose 为 Web/报告/PG 默认设置1GiB、准备 Worker 2GiB、Redis/监控512MiB、运维进程256MiB、前端128MiB的容器内存上限；这是初始保护值，不是内存预留或容量承诺。可通过 Compose 环境中的 `*_MEMORY_LIMIT` 调整，具体变量见 Compose 文件。Docker 的内存上限不等于 Redis 内部淘汰策略；Broker 持久任务由 PG 修复，不能把 Redis 当权威存储。

## 运行策略

| 配置 | 默认值 | 作用域 |
| --- | --- | --- |
| `AI_TIMEOUT_SECONDS` | 90 | 一次完整模型/工具流程的总时间预算 |
| `AI_MAX_CONCURRENCY` / `AI_MAX_WAITERS` | 4 / 16 | 每进程交互执行与有限等待 |
| `AI_WAIT_SECONDS` | 5 | 准入最长等待，仍受总预算约束 |
| `AI_BACKGROUND_CONCURRENCY` / `AI_VISION_CONCURRENCY` | 2 / 1 | 每进程及共享许可中的分类上限 |
| `AI_PROVIDER_CONCURRENCY` | 8 | 同一实际 API Key 的 PG 共享许可上限 |
| `AI_INTERACTIVE_RESERVED_PERMITS` | 2 | 共享容量中留给交互的额度；小容量部署需同步调低 |
| `AI_TOOL_CONCURRENCY` | 2 | 工具并发；父物理调用先释放许可 |
| `AI_MAX_OUTPUT_TOKENS` | 4096 | 每次物理请求的输出上限与配额预留 |
| `AI_COMMAND_LEASE_SECONDS` | 150 | 命令、任务、Flight、供应商许可租约，执行期间续期 |
| `AI_CIRCUIT_MIN_CALLS` / `AI_CIRCUIT_OPEN_SECONDS` | 10 / 30 | 最近20次、失败比例至少50%，半开仅一次探测 |
| `AI_UNKNOWN_LIMIT` | 3 | 同一凭据最近5分钟未知调用达到上限后停止新增 |
| `PARSER_PROCESSES` / `MEDIA_MAX_CONNECTIONS` | 2 / 8 | Web 解析进程池 / 每进程媒体连接并发 |
| `OUTBOX_REDELIVER_SECONDS` | 60 | queued 且发布后未消费任务的重新投递间隔 |
| `RATE_LIMIT_PER_MINUTE` / `TOKEN_QUOTA_PER_USER` | 60 / 100000 | 每用户物理调用次数 / 最近24小时保守 Token 预留 |

同一 API Key 的多个配置不能绕过共享许可。进程熔断器按供应商、模型、凭据修订区分，不是集群共享熔断器。许可过期不能证明远端停止；集中未知调用会限制新尝试。输入按 UTF-8 字节数加余量保守预留，取得可信 usage 后结算；未知用量不释放 Token 预留。迁移沿用旧调用已经记录的 Token 用量，旧版未记录的未知用量无法追溯重建。

## 结果、重试与恢复

REST、WS、SSE 共用 `code/message/retry_policy/outcome/trace_id`。服务端创建 trace，HTTP 返回 `X-Trace-ID`；浏览器自带追踪头不会成为内部身份。排查时用该 ID 关联命令、outbox、Flight 和物理调用日志。

- `not_sent`：本次未发送；处理容量、配额或配置后可按错误提示重试。
- `known`：模型已返回或明确拒绝；结构无效的结果不能当成功复用。
- `unknown`：请求可能已执行。读取保留原问题、答案和真实评分；用户确认后使用新的请求 ID 创建新尝试，可能重复计费。

Flight 同时保存完整节点结果和工具循环中的每次已知供应商响应。显式关联的重试只有在用户、资源、逻辑操作、输入、冻结配置和快照版本一致时才复用。聊天保持真实分块流，半段内容不进入成功 Flight。follower 通过 Redis 通知和有截止时间的 PG 轮询等待；通知缺失不影响正确性。

dispatcher 短事务认领、事务外发布、条件确认。Broker 清空后，仍为 queued 的 published 任务会重派；消费者用 PG 认领，实际重复投递不会重算 completed 任务。inspector 主动将过期 running 命令/任务/调用标为未知并回收许可，不调用 AI。手动重试入口为简历 `/retry`、报告 `/report/retry` 及面试新请求 ID。

## 健康、日志与监控

`/health`、`/health/live` 为存活检查；`/health/ready` 检查 PG、发布包迁移及角色依赖。Web 在 Redis 故障和冷启动时可以 `degraded`，保留 PG 事实读取；能力字段区分实时 Redis 和 Broker。Worker、dispatcher 还检查 Broker 与本容器实例心跳/最近进度，inspector 依赖 PG 和自身巡检进度。

`/api/admin/diagnostics` 只允许管理员读取聚合诊断。日志统一 JSON，不包含请求体、模型内容、密钥、Cookie 或异常值；异常栈仅保留函数和行号。Worker 父/子进程都进入同一日志出口。

`metrics` 容器仅在内部9090提供 `/metrics`，Nginx 明确拒绝公开 `/metrics`。单个 exporter 从 PG 读取所有实例的调用、许可、Flight、任务和心跳，避免 prefork 计数丢失/重复；同一个数据库只抓取一次，不能对多个 exporter 副本再求和。准入、拒绝、Flight 命中、熔断事件、租约丢失和媒体背压为有限标签聚合计数，观测写入有100ms上限，失败不替换业务结果。

```bash
docker compose --profile monitoring up -d prometheus
```

Prometheus 配置和告警在 `operations/`；`dashboard.json` 可导入已有 Grafana。阈值是初始值，需按实际负载调整。进程熔断观测带有效期并按 scene/state 聚合，不暴露实例或模型标签；观察不改变本进程熔断决策。任务排队和执行耗时另有直方图，旧任务没有时间数据时不补造历史耗时。

`operations/alerts.test.yml` 用 promtool 验证八项告警触发、Flight 未知结果增长及健康时静默；CI 执行 `docker compose -p resumeagent-engineering-test -f docker-compose.test.yml --profile monitoring run --rm --no-deps alerts`。通知渠道和生产阈值仍需由部署环境配置。

## 隔离验收

测试栈固定项目名 `resumeagent-engineering-test`，使用 PG 临时文件系统、独立 Redis、上传卷和 localhost 测试端口。禁止把故障脚本指向业务项目；脚本不接受生产目标。协议供应商只有测试镜像引用，不是真实服务的降级实现。

```bash
docker compose -p resumeagent-engineering-test -f docker-compose.test.yml up -d --build --wait
python backend/tests/fault_matrix.py
python backend/tests/capacity_probe.py
python backend/tests/load_platform.py --requests 100 --concurrency 8
```

故障实验等待明确的文件检查点后 kill，证据位于 `backend/.test-work/faults/`。容量探针记录共享在途峰值、拒绝率/延迟、正常与慢/失败/超时供应商下的事实读取、容器内存/进程和 DB 连接。性能目标逐项写为布尔结果；局部探针不代表真实供应商或生产容量。

浏览器验收需测试栈显式 `TEST_DEMO_MODE=true` 重启、创建一次性管理员，然后在 `frontend` 安装 Chromium 并执行 `npm run test:e2e`、`npm run test:e2e:admin`。提供 `E2E_BASE_URL=http://127.0.0.1:18080` 和一次性管理员环境变量。CI 自动执行这些步骤，保留失败截图和脱敏证据。

## 备份、发布和回退

CI 使用提交 SHA 构建镜像，完整测试通过后保存同一镜像与校验和；部署 workflow 只下载对应成功运行的制品。`deploy-release.sh` 校验制品、检查已有生产配置、停止应用写入、备份 PG、增量迁移并等待 Web/Worker/dispatcher/inspector 健康，最后验证三个队列的持久任务。失败返回非零并保留组件日志。单机维护窗口包含备份与迁移时间，发布期间有停机。

Linux 备份示例：

```bash
docker compose exec -T postgres pg_dump -U postgres -Fc resume_agent > backup.dump
# 在隔离数据库验证恢复后，再制定生产切换窗口。
docker compose exec -T postgres createdb -U postgres resume_agent_restore
docker compose exec -T postgres pg_restore -U postgres -d resume_agent_restore < backup.dump
```

应用回退使用上一份已测试制品：

```bash
bash releases/<previous-sha>/scripts/rollback-release.sh <previous-sha> /root/resume-agent
```

脚本先用旧镜像对当前 DB 执行实际迁移兼容检查，通过后才替换应用；不运行 downgrade，不删除新表。数据库 head 不被旧镜像接受时直接失败，需要先提供兼容镜像，不能靠删除迁移版本行绕过。首次工程化升级之前的硬编码 head 镜像不视为已验证的回退目标。预生产上一镜像回退、生产备份恢复和真实供应商联调仍需在对应环境演练。

## 更换 SECRET_KEY

先停止 backend、两类 Worker、outbox、inspector、metrics 等所有写入者，完成并验证数据库备份。保留当前旧密钥，使用同一运行环境交互执行 `python -m app.platform.operations.rekey`；确认词为 `REENCRYPT-PLATFORM`，新值通过隐藏输入读取。该命令在一个事务内重加密当前 Provider、修订历史、简历/面试冻结配置，不更改语义版本。成功后把私有配置更新为同一个新值，再重启并验证。任何解密失败都回滚，不会输出密钥；禁止直接替换 `.env` 后尝试自动修复旧数据。

## 与参考实现的对应

| 参考边界 | Python 落点 |
| --- | --- |
| WorkflowService / StateMachine | `service.py` 短事务、`domain/state.py`、`domain/results.py`、LangGraph 节点 |
| AiCallGuard / Executor | `ai/guard.py`、`ai/execution.py`、有界解析进程池和独立 Celery 队列 |
| DistributedSingleFlight | `ai/flights.py`、PG 结果与调用账本、owner/租约条件提交 |
| Rehydrate / Repair | 快照 schema_version、`operations/recovery.py`、outbox 重新投递 |
| Runtime Metrics / Error Handler | `contracts.py`、`operations/`、内部 exporter、告警和诊断 |

保留 `service.py` 作为应用/持久化边界门面，未创建逐表空仓储；后续模块提取以实际替换边界为依据。
