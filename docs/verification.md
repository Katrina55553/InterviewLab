# 实施与验收记录

## 2026-10-26 独立视觉模型配置与面试接入

「AI 设置」新增独立视觉模型区域，复用账户已有 API Key 或 ChatGPT 订阅认证，图片测试通过后再保存启用；不切换文字模型。新面试分别冻结文字与视觉配置。API Key 变更需重新测试，订阅正常刷新保持有效；断开认证清理匹配的视觉选择。摄像头预览默认仅在本机显示，用户主动开启视觉建议后才发送画面，可暂停或关闭。

新增迁移 `20261026_0011` 只创建 `platform_user_vision_settings`，不改写历史面试或简历事实。浏览器联调实际复现了原视觉反馈写入 `provider_id=NULL` 导致数据库拒绝的问题，现改为在该历史命名列记录实际个人连接 ID，并补充 SQLite / PostgreSQL 的接口持久化回归。图片内容未保存，建议不参与评分。

| 本次检查 | 结果 |
| --- | --- |
| 后端完整 Pytest | **468 passed、12 skipped**，232.87 秒；真实 PostgreSQL / Redis 分支已执行，跳过项均为 SQLite 缺少 PostgreSQL 锁语义的重复分支 |
| PostgreSQL 16 增量迁移 / Alembic check | 通过，升级到 `20261026_0011`，无模型差异 |
| 前端 Vitest / coverage | **107 passed / 13 files**；视觉设置行覆盖率 **95.31%**，摄像头行覆盖率 **98.61%** |
| Ruff / ESLint / TypeScript / Vite build | 通过，`frontend/dist/` 已更新 |
| 真实 Chromium + FastAPI + PostgreSQL 验收 | 通过：文字与视觉模型独立、服务端图片测试与保存、刷新恢复、桌面/390px 移动端、摄像头主动开启及暂停、反馈真实写入、新旧面试配置隔离 |

后端回归包含图片识别失败不生成凭证、无测试/过期/篡改/跨账号或模型的凭证拒绝、测试时凭据轮换、并发首次保存、独立停用、断开认证、管理员策略、旧快照保持、个人连接反馈持久化，以及真实 SDK 的订阅图片 Responses 协议和刷新语义。

本轮只使用专门创建的 `resumeagent-vision-pg-f4c7` / `resumeagent-vision-redis-f4c7` 临时容器，端口为 55435 / 56382，测试 schema 随机隔离；浏览器验收使用本机 8018 临时服务、实际生产前端产物和虚拟摄像头。模型调用、模型目录和订阅上游使用明确的协议替身，未请求真实供应商，不代表实际模型质量或计费验收。浏览器截图位于 Git 忽略的 `frontend/test-results/vision-feature-20261026/`；临时服务和浏览器脚本位于 `.test-work/vision-feature-20261026/`，最终 JUnit 为 `backend/.test-work/vision-feature-20261026/final-v2.junit.xml`。本轮没有迁移或替换现有业务容器。

验收结束后，临时服务已退出，两只测试容器及其临时数据已按核验后的精确 ID 删除；现有 `resumeagent` 业务容器继续运行。

## 2026-10-26 聊天管理自动验收补齐

`frontend/tests/chat-management.e2e.mjs` 保留原有 PATCH 路由、Cookie/CSRF、重命名、附件保持、置顶排序、空名称拒绝与删除 API 断言，并增加真实 Chromium 页面操作。浏览器通过表单登录、上传附件创建两条对话、发送 SSE 消息，再使用菜单和原生弹窗完成重命名、置顶/取消置顶、取消及删除；刷新后检查名称、排序、消息与附件仍一致。另覆盖菜单键盘导航和 Escape 焦点恢复、移动端菜单边界、删除非当前对话保持路由、删除当前对话回到新对话，以及刷新后的空历史。

CI 的 Docker 作业已加入 `npm run test:e2e:chat`，在显式演示栈启动后、发布制品保存前执行。沿用 `if: always()` 的 `engineering-evidence` 上传，保留聊天 JSON 结果、桌面/移动端截图、失败截图与 Playwright trace；测试或清理失败会以非零状态退出。

| 本次检查 | 结果 |
| --- | --- |
| 聊天管理 API + Chromium E2E | **通过**，包含真实附件上传与 SSE、桌面/390px 移动端管理操作和刷新持久化 |
| 现有普通用户 / 管理员 Chromium E2E | **两套均通过**，退出码 0 |
| 前端 ESLint / 脚本语法 | 通过 |
| 前端 Vitest / coverage | **100 passed / 11 files**；平台源码行覆盖率 **89.05%** |
| 当前源码 Docker 构建 | 前后端均通过，前端镜像内 TypeScript 与 Vite build 通过 |
| CI YAML / diff 空白检查 | 通过，确认聊天步骤位于演示初始化与发布制品保存之间，证据始终上传 |

本机使用独立 Compose 项目 `resumeagent-chat-acceptance`，从当前工作区构建专用测试镜像，运行 PostgreSQL 16、Redis 7、双 Web、Celery、outbox dispatcher、recovery 巡检与 Nginx；显式 `TEST_DEMO_MODE=true`，没有用浏览器路由替身代替业务接口。Node.js 为 24.15.0，Playwright 为项目锁定的 1.58.2。截图、结果与 trace 位于 Git 忽略的 `frontend/test-results/chat-acceptance-20261026/`，现有两套截图分别位于其 `platform/`、`admin/` 子目录。结束后清理本次测试容器、网络和数据卷，现有 `resumeagent` 业务项目保持运行。

本次只修改验收脚本、CI 与相关说明，不重复执行后端完整 Pytest、故障矩阵或容量探测。远端 GitHub Actions 尚未运行；显式演示的 SSE 通过不代表真实模型、讯飞或视觉供应商联调通过。

## 2026-10-26 个人 AI 配置文档同步与回归复查

根据当前 `runtime.freeze_configs`、个人连接接口和前端「AI 设置」核对使用方式：真实模式下，简历解析、独立诊断、模拟面试及聊天使用当前账户保存的个人模型连接，不回退到管理员供应商或平台 `LLM_*`。连接凭据后仍须选择模型并点击「保存」；连接测试不代替保存。管理员账户同样需要个人连接，规则演示仍需显式开启 `PLATFORM_DEMO_MODE=true`。

README 已同步 Docker、本地开发、配置表和接口入口，并说明解析与诊断独立。下方 10 月 24 日的 **15 failed、226 passed、64 skipped** 保留为当时的历史结果，不再作为当前源码的回归结论。

本次复测对象为 `3f6513b` 基础上的当前工作区，包含已有未提交的个人连接、聊天管理及删除功能等业务改动；本轮仅修改 README 和本验收文档。后端使用已有开发虚拟环境 `backend/.test-runtime/dependency-upgrade/Scripts/python.exe`（Python **3.12.8**、pytest **9.1.1**、FastAPI **0.159.1**、OpenAI SDK **3.36.1**）；前端为 Node.js **24.15.0**、Vitest **5.0.5**。

| 检查 | 最新结果 |
| --- | --- |
| 后端完整 Pytest | **335 passed、128 skipped、0 failed / 463 项**，193.75 秒；另有 1 条 SQLite 日期适配器弃用警告 |
| 个人 AI 相关回归 | 可运行分支通过，覆盖账户隔离、无平台凭据回退、配置冻结与断开、账户模型目录及模拟 OAuth/Responses 协议 |
| 后端 Ruff / 前端 ESLint | `python -m ruff check .` 与 `npm run lint` 均通过 |
| 前端 Vitest | `npm test -- --reporter=dot`：**100 passed / 11 files** |

后端执行 `python -m pytest tests -q -p no:cacheprovider --basetemp .test-work/docs-sync-20261026-8c47/pytest-completed --junitxml .test-work/docs-sync-20261026-8c47/pytest.xml --tb=short`。本机沙箱最初阻塞 Windows asyncio 创建本地 socketpair，放行后使用原测试完成上述回归，未修改应用或测试代码。JUnit 证据保存在 Git 忽略的 `backend/.test-work/docs-sync-20261026-8c47/pytest.xml`，前端输出在 `frontend/.test-work/docs-sync-20261026-8c47/vitest.log`。

旧记录中的 15 项失败在本次可运行分支中未再出现。本次没有设置 `PLATFORM_TEST_DATABASE_URL` 或 `PLATFORM_TEST_REDIS_URL`：128 项跳过中，115 项需要 PostgreSQL 测试地址，11 项依赖真实 PostgreSQL 锁语义，2 项需要 Redis 测试地址；这些分支仍需在隔离环境执行。本次也未重建或发布镜像、运行浏览器 E2E、复测容量与故障矩阵，未验证真实 API Key、ChatGPT 订阅、讯飞或视觉供应商；源码回归通过不代表运行中的旧镜像或真实服务已验收。

## 2026-10-24 面经社区持久化与统一点赞

面经、评论、点赞和私有草稿已接入 PostgreSQL；社区源码不再读写浏览器 localStorage/sessionStorage，移除内置示例帖子与独立收藏功能。「我的点赞」读取同一份点赞关系。迁移 `20261024_0006` 新增五张社区表，依赖 `0005` 的顺序迁移。

| 检查 | 结果 |
| --- | --- |
| 社区后端专项 | **10 passed**：SQLite 与临时 PostgreSQL 16 各五项；覆盖 Cookie/CSRF、跨账号阅读、私有草稿版本、发布/评论重放、并发重复点赞、搜索与分页 |
| 前端 Vitest / coverage | **51 passed / 7 files**；社区数据请求模块行覆盖率 100%，社区页面行覆盖率 92.85%；包含社区不访问浏览器存储的断言 |
| Ruff / ESLint / TypeScript / Vite build | 通过，`frontend/dist/` 已更新 |
| 本次发布基线完整后端回归 | **241 passed、64 skipped**；使用当前运行的后端代码加社区增量，未配置的其他真实数据库/Redis 分支按现有约定跳过；社区的 PostgreSQL 分支另已执行 |
| 实际 Compose 联调 | 两个临时账号验证发布对其他账号可见、草稿互不可见、评论共享、重复点赞只记一次、取消后从「我的点赞」移除；浏览器刷新后点赞保持 |
| 运行状态 | 数据库迁移完成；frontend/backend、准备/报告 Worker、outbox、inspector 均健康，`/health/ready` 返回 200；Beat 与 metrics 运行中 |

当时工作区中未提交的个人 AI 配置业务改动使完整回归出现 **15 failed、226 passed、64 skipped**。该次社区发布没有回退或修补这些改动，而是从已运行的 `resumeagent-backend:community-base-20261024` 构建隔离的社区增量镜像 `resumeagent-backend:community-20261024`，验证后更新本机 `resumeagent-backend:local`。该次发布包含社区接口与完整迁移元数据，个人 AI 配置业务逻辑未一并启用。本段保留 10 月 24 日的失败与发布历史；当前工作区的复测结论及验收边界见[2026-10-26 个人 AI 配置文档同步与回归复查](#2026-10-26-个人-ai-配置文档同步与回归复查)。

临时 PostgreSQL 测试容器及两个 QA 账号和关联帖子、评论、点赞已清理；原平台账号和内容未删除。验收截图 `frontend/test-results/community-backend-verified.jpg` 展示的是清理前的临时联调记录。增量构建上下文和联调脚本保存在 Git 忽略的 `.test-work/community-release/` 与 `.test-work/community-*.py`，不包含真实用户密码或供应商凭据。

## 2026-10-24 文档解析、登录限流与语音重试修复

本轮修复四项已复现缺陷：身份证脱敏覆盖末位 X/x；DOCX 按正文顺序提取表格、嵌套表格并避免合并单元格重复；Compose 内网后端信任经过 Nginx 覆盖的客户端 IP，避免所有用户共享代理的登录限流桶；明确失败的 TTS 任务可由新一次朗读请求原子认领重试，进行中、成功及未知结果仍复用原任务。无需数据库迁移。

新增20项回归，先在未修复实现上复现9项失败，再验证修复。TTS 测试覆盖供应商明确拒绝后恢复、每次尝试使用不同供应商请求 ID、三个并发请求仅一次合成，以及未知结果不重发；文档测试覆盖纯表格、混合正文、嵌套表格及合并单元格；代理测试直接使用两份 Compose 的信任配置验证不同 IP 的独立限流，并约束 Nginx 覆盖客户端转发头和生产后端不发布端口。

| 检查 | 结果 |
| --- | --- |
| 后端完整 Pytest | **287 passed、8 skipped**，65.06秒；使用已运行的隔离 PostgreSQL 16 / Redis 测试端口，数据库测试创建随机临时 schema |
| 后端 Ruff | 通过 |
| 前端 Vitest / coverage | **44 passed / 6 files**，平台源码行覆盖率78.24% |
| 前端 ESLint / TypeScript / Vite build | 通过，`frontend/dist/` 已重新生成 |
| 两份 Compose 配置 | `docker compose config --quiet` 通过 |
| 新 Nginx 配置 | 使用现有前端测试镜像、只读挂载本轮配置的一次性容器运行 `nginx -t`，通过 |

8项跳过为 SQLite 不支持的 PostgreSQL 锁语义分支，对应真实 PostgreSQL 分支已执行。本轮未重启现有服务，未验证真实讯飞或模型供应商；TTS 使用协议替身，不能代替供应商实联验收。

## 2026-10-24 zod 安全修复

针对下方全栈复测首次审计发现的 `GHSA-mrhx-c7xc-m3w5`，使用官方 npm registry 定向将间接开发依赖 `zod` 从 **4.4.3** 更新至 **4.6.7**。锁文件仅改变 zod 的版本、下载地址和完整性校验；`package.json`、上游依赖版本与 override 保持原状，`eslint-plugin-react-hooks` 和 `zod-validation-error` 的现有范围均兼容修复版本。

| 检查 | 结果 |
| --- | --- |
| 官方 registry `npm audit` | **0 漏洞**，所有严重级别均为 0 |
| clean `npm ci` / `npm ls zod` | 通过；实际安装并去重为 `zod@4.6.7` |
| ESLint | 通过 |
| Vitest / coverage | 两次执行均为 6 文件、44 项通过；平台源码行覆盖率 78.24% |
| TypeScript / Vite build | 通过，仓库 `frontend/dist/` 已重新生成 |
| 前端 Docker Linux 构建 | 通过；镜像构建内 `npm ci` 同样报告 0 漏洞 |
| 本机隔离前端健康 | 页面返回 200，经 Nginx 的 `/health/ready` 为 `ok` |

前端 Docker 上下文同时排除 `.test-work/`、`test-results/` 和 `coverage/`，避免本地缓存与验收产物进入构建输入。宿主新旧生产 JS 字节一致；本轮没有修改前端业务源码。验证日志、修复后审计 JSON、原锁文件和镜像 ID 保存在 Git 忽略的 `frontend/.test-work/zod-fix-20261024/`。下方全栈复测的原始审计文件保留为修复前历史；本节未重新运行后端故障或容量测试。

## 2026-10-24 Docker 全栈复测

补齐 10 月 23 日依赖升级后的容器验收。前后端镜像已从 `fd135c4` 对应源码重新构建，使用固定隔离项目 `resumeagent-engineering-test`；实际数据库为 PostgreSQL **16.15**，Redis **7.4.11**，后端容器 Python **3.12.15**。前端镜像使用 Node.js 24 构建；宿主浏览器测试使用 Node.js **24.15.0**、Playwright **1.58.2**、Chromium **145.0.7632.6**。Docker **29.8.2** 可见 24 CPU、约 7.61 GiB 内存。

### 本次结果

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| 前后端镜像构建 | 通过 | 后端完整哈希运行锁实际安装到 Linux 镜像；前端 `npm ci`、TypeScript 与 Vite build 完成 |
| 迁移与模型一致性 | 通过 | `migrate` 退出 0；容器内 `alembic check` 无新升级操作 |
| Nginx 与双 Web 就绪 | 通过 | 页面及 JS/CSS 返回 200；代理和两个实例的就绪状态均为 `ok` |
| 独立进程故障矩阵 | **9 项通过** | 调用前/返回后、Flight 保存前/后、Redis 丢失、并发同命令、Worker 退出、重复投递、旧 owner 迟到回写 |
| 双实例容量探针 | 通过 | 共享并发、限流、熔断恢复和总超时断言通过；两项性能目标均为 `true`，数值见下表 |
| 普通用户 Chromium E2E | 通过，退出 0 | 注册、上传、五轮评分、刷新恢复、追问/换问法/跳过、异步报告、SSE 持久化、移动端、退出重登 |
| 管理员 Chromium E2E | 通过，退出 0 | 私有诊断、供应商/Agent 编辑、密钥保护、修订恢复、工具与 API 绑定、浏览器 SSE、自建配置清理 |
| 告警规则 | 通过 | 固定 `prom/prometheus:v3.2.1` 镜像的 promtool 输出 `SUCCESS` |
| 测试结束后的健康与任务链路 | 通过 | 9 个常驻服务全部 healthy；`default`、`prepare`、`report` 持久探针全部完成 |
| npm 依赖安全审计 | **首次审计：1 High，后续已修复** | `zod@4.4.3` 为间接开发依赖；修复与 0 漏洞复查见上方安全修复记录 |

### 本次容量数值与探针修正

沿用 10 个测试用户、突发 10 请求、共享许可上限 2、每阶段 30 次快照读取，以及 3 秒慢响应的隔离协议供应商。

| 指标 | 实测 |
| --- | --- |
| 基线读取 P95 | 9.71 ms |
| 慢供应商下读取 P95 | 13.45 ms |
| 失败供应商下读取 P95 / 基线比值 | 11.26 ms / **1.16**，满足不超过 1.2 的目标 |
| 无响应供应商下读取 P95 | 9.79 ms |
| 超载拒绝 P95 | **265.27 ms**，满足小于 500 ms 的目标 |
| 突发结果 | 10 请求中 8 个限流；供应商在途峰值 2 |
| 无响应总预算 | `AI_TIMEOUT`，20.04 秒；测试预算 20 秒 |
| 资源点采样 | 两个 Web 124.3 / 147.8 MiB，Celery 259.4 MiB；DB 连接 15，排队任务 0 |

首次容量运行未观察到 `inflight == 2`，而供应商高水位已记录 `peak == 2`。本机 5 次对比测量发现，每次新建 HTTP 客户端的控制面读取耗时约 1.9–3.3 秒，复用客户端后约 2–26 毫秒，足以解释错过 3 秒窗口。因此仅调整宿主 `capacity_probe.py`：在突发前创建并复用控制面客户端。并发检查点、共享峰值、拒绝延迟、读取比值及超时门槛均保留；原容量探针复测退出 0，两项目标通过，Ruff 与 diff 空白检查通过。业务代码及依赖锁未改动。

容量结果只反映上述局部测试规模，未验证大历史数据、持续负载峰值或真实供应商容量。供应商 `inflight` 计数覆盖延迟/请求处理阶段，不代表完整 SSE 消费周期。

### 证据与边界

环境与镜像 ID、9 项故障 JSON、容量 JSON、首次失败及修正后日志、轮询耗时、浏览器输出、告警、迁移检查、任务探针、最终健康状态及 npm 审计 JSON 保存在 Git 忽略的 `backend/.test-work/docker-retest-20261024/`。浏览器截图分别保存在 `frontend/test-results/docker-retest-20261024/user/` 和 `admin/`，共 10 张。

首次官方 npm registry 审计确认 `zod@4.4.3` 的 High 漏洞 `GHSA-mrhx-c7xc-m3w5`，受影响范围 `<4.6.6`。该包属于 ESLint 工具链的间接开发依赖，当时会导致 CI 的 npm 审计门禁失败。全栈复测后已更新至 4.6.7 并复查为 0 漏洞，详见上方安全修复记录；原始 1 High 审计 JSON 保留。

故障与容量测试显式关闭演示模式并调用隔离协议替身；浏览器测试显式开启 `TEST_DEMO_MODE=true`，实际经过容器 Nginx。测试栈保留运行，当前为演示模式。本轮不代替真实 LLM、ASR/TTS、视觉供应商联调、远端 GitHub Actions、生产发布/备份恢复和回退演练；完整单元测试仍引用下方 10 月 23 日记录。

## 2026-10-23 依赖与构建环境升级

保留 Python 3.12、PostgreSQL 16 / Redis 7 的部署主线，以及现有 PostgreSQL 事实、Outbox、Celery 和逐命令 LangGraph 架构。本次未增加数据库迁移。

| 组件 | 升级前 | 升级后 |
| --- | --- | --- |
| Node.js 构建 / CI | 20 | 24 LTS；前端 `.nvmrc` / engines 同步 |
| FastAPI / Starlette | 0.115.0 / 0.38.6 | 0.159.1 / 1.9.0 |
| python-multipart | 0.0.12 | 0.0.32 |
| LangGraph / OpenAI SDK | 0.2.0 / 1.51.0 | 1.2.16 / 3.36.1 |
| Pydantic / Celery | 2.9.0 / 5.4.0 | 2.14.1 / 5.6.3 |
| SQLAlchemy | 2.0.35 | 2.0.55 |
| Vite / React Router | 8.0.14 / 7.15.1 | 8.3.5 / 7.18.4 |
| Vitest / coverage-v8 | 2.1.9 / 未声明 | 同为 5.0.5 |

后端运行/开发依赖分离为 `.in` 输入和完整 SHA-256 锁文件，开发锁受运行锁约束。`python scripts/lock_dependencies.py` 默认保留满足约束的版本，`--upgrade` 主动更新；重生成两份锁字节一致。分别按 Windows / Linux 的 Python 3.12 环境求值，所有运行依赖均与开发锁一致，运行锁不包含 pytest、Ruff、pip-audit、uv 或 aiosqlite。Docker 强制哈希校验并仅安装运行依赖 wheel。

前端删除未使用的 Axios / React Query，修复 coverage 指向已删除目录的问题，并纳入当前平台源码及 Vitest 配置的检查。Typography 0.5.20 仍引用存在安全问题的 selector-parser 6，局部 override 到 7.1.7；分别注入官方 6.0.10 与 7.1.7 构建，CSS 均为 46,972 字节、SHA-256 `AFD74E5DF25E801EAA6C0D36F730E020953B40F018B58A6386A963AF7E27E7F4`，逐字节一致。两处浏览器脚本的旧登录按钮文案也已对齐当前界面。

CI 使用 Node 24 运行的 GitHub 官方 Actions v7，增加后端锁同步检查、Python/npm 漏洞审计与前端覆盖率执行；Docker 故障与浏览器门禁继续保留。运行、开发和维护命令见 [README](../README.md#依赖维护)。

### 本次检查

| 检查 | 结果 |
| --- | --- |
| 后端 Ruff / pip check | 通过，无依赖冲突 |
| 后端完整 Pytest | 最终复测 267 passed、8 skipped，83.14 秒；包含真实 PostgreSQL / Redis 分支 |
| 后端完整开发锁 pip-audit | 无已知漏洞 |
| Linux Python 3.12 运行锁 | 92 个包的 manylinux x86_64 wheel 安装计划解析通过，未实际安装到目标平台 |
| 前端 clean npm ci / lint / TypeScript / build | 通过，`dist/` 已重建 |
| 前端 Vitest / coverage | 6 个文件、44 项测试通过；平台源码行覆盖率 78.24% |
| 前端 npm audit | 所有级别均 0 漏洞 |
| 普通用户浏览器 E2E | 通过：注册、上传、五轮面试、恢复、追问/换题/跳过、报告、SSE、移动端及重新登录 |
| 管理员浏览器 E2E | 通过：供应商/Agent 创建更新、配置历史恢复、绑定、聊天及测试数据清理 |
| 新 OpenAI SDK 本机协议验收 | 真实 SDK 的 JSON 与 SSE 调用成功；两条 completed 审计、许可释放；503 仅发一次请求，无 SDK 自动重试 |
| Alembic check | 无模型差异 |
| CI YAML / 测试 Compose 静态配置 | 通过 |

浏览器测试使用新依赖虚拟环境、独立 demo 数据库、Celery / outbox / recovery 与本机 Vite；显式开启演示模式。临时 Vite 为 `/metrics` 转发后端的 404，以满足浏览器脚本的路由边界断言；这不代表实际运行了生产 Nginx。原脚本两处登录按钮文案更新后，普通用户和管理员两套均最终 exit 0。SDK 检查使用独立本机 `tests.fake_provider`，请求未发送到外部供应商。日志及截图保存在 Git 忽略的 `.test-work/dependency-upgrade-20261022/`。

10 月 23 日最终后端复测退出码为 0，JUnit 与完整日志分别保存在 `backend/.test-work/final-upgrade-20261023.junit.xml`、`backend/.test-work/final-upgrade-20261023.log`。8 个跳过项为 SQLite 无法验证 PostgreSQL 锁语义的分支，对应 PostgreSQL 分支已执行；唯一 warning 是 Python 3.12 的 SQLite datetime adapter 弃用提醒。

验收后本次隔离进程均已退出，最后启动的 PostgreSQL / Redis 已按精确数据目录、配置文件及端口归属停止，日志、截图与测试数据目录保留。本机原有 5432 数据库未被本次测试操作。

本机数据库测试为隔离的 PostgreSQL 18.3 / Redis 7.4.2，生产与 CI 仍配置 PostgreSQL 16 / Redis 7。本机 Docker Linux 引擎未运行，因此本轮没有实际构建/运行升级后的容器，也未重跑绑定 Compose 的独立进程故障矩阵或容量探测。模型协议替身和显式演示均不代表真实 LLM、ASR/TTS 或视觉供应商验收；远端 GitHub Actions 执行仍需在提交后验证。

## 2026-10-21 工程化复查修复

复查确认的4项 P2 已修复：

- `docs/platform.md` 本地 Worker 改为消费 `prepare,report,default`，补上独立 inspector 启动命令；同步 `AGENTS.md` 的队列说明。本地单 Worker 不提供生产准备/报告资源隔离。
- 超时分类使用物理尝试的发送状态：发送前保留 `not_sent`，已收到部分流式回复后耗尽总预算则返回并审计为 `unknown`。新用例验证 SDK 流关闭、单次请求不重发、未知指标计数及未知调用限制，避免将部分回复算成已知失败。
- 熔断准入返回代次，完成回调携带该代次；打开、进入半开和完成探测都会推进代次。旧请求的成功、失败、取消，以及上一探测的迟到回调，不能结束当前探测或清除其占用；探测失败仍重新进入冷却。
- inspector 在同一恢复事务内将过期且确定未发送的调用置为 `not_sent` 并清零 `reserved_tokens`。已发送的未知调用继续保守占用配额；重复巡检不影响新建、未过期的预留与许可。

先添加失败回归，实际复现部分流超时状态错误、旧请求干扰半开探测及发送前退出未释放900 Token预留，再应用修复。新增12个参数化回归用例，覆盖发送前/发送后、并发交错、探测失败/取消和重复巡检边界。

最终后端 Ruff、Git diff 空白检查通过；完整 Pytest **230 passed、8 skipped**，耗时约74秒。使用固定隔离项目的真实 PostgreSQL 16、Redis 7及随机测试 schema；8个跳过项均为 SQLite 无法提供 PostgreSQL 行锁语义的分支，对应 PostgreSQL 分支实际执行。唯一 warning 仍是 SQLite datetime adapter 弃用提醒。本轮未改动前端或重跑浏览器、独立进程故障矩阵及真实供应商联调；2026-10-20 的这些记录保留为历史证据。

## 2026-10-20 工程化方案实施验收

对应 [engineering-plan.md](engineering-plan.md)，运行与故障处置见 [engineering-operations.md](engineering-operations.md)。本节为当时工程化实现的验收记录；最新复查修复见上文，后文保留实施前历史。

已实现统一错误与服务端追踪、动态迁移检查、有限准入与总时间预算、实际凭据共享许可及配额、进程熔断、持久 Flight/物理调用账本、已知结果复用、未知结果人工重试、任务重新派发与巡检、准备/报告队列隔离、配置修订与恢复、聚合指标与私有诊断、不可变制品 CI 和发布/回退门禁。新增迁移为 `20261020_0002` 至 `0004`，保留已有平台事实。前端同步更新 `dist/`，并修正首次进入面试时仍判断旧 `draft` 状态的问题，统一使用后端的 `ready`。

### 本次实际执行

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| 后端完整 Pytest | **218 passed、8 skipped** | 真实隔离 PostgreSQL 16、Redis 7，加 SQLite 补充；最终完整运行约64秒 |
| 后端 Ruff | 通过 | 包含新增迁移、运行模块与故障/容量脚本 |
| 前端 Vitest | **34 passed / 5 files** | 用户流程、恢复、协议、媒体与聊天回归；测试快照使用实际 `ready` 状态 |
| 前端 ESLint、TypeScript、Vite build | 通过 | `frontend/dist/` 已重新生成 |
| 容器构建与迁移 | 通过 | 隔离项目从源码构建；增量 Alembic upgrade 完成；`alembic check` 无模型差异 |
| 普通用户 Chromium E2E | 通过 | 注册、上传、完整五题、刷新同题、追问/换问法、跳过零分、提前结束、异步报告、SSE 持久化、移动端、退出重登；显式演示模式 |
| 管理员 Chromium E2E | 通过 | 诊断鉴权、公开 metrics 返回404、配置编辑、密钥不回传/空值保留、修订历史、恢复为新版本、独立 Agent API 绑定后浏览器 SSE、清理自建配置 |
| 独立进程故障矩阵 | **9 项通过** | 双 Web 实例、真实 dispatcher/Celery、明确文件检查点后 kill，详见下表 |
| 限流/熔断/预算容量探针 | 通过 | 双实例并发、慢/失败/无响应供应商、事实读取隔离；规模和数值见下文 |
| 告警规则 | 通过 | promtool 校验8条规则；验证全部告警触发、Flight 未知增长及健康时静默 |
| 三类持久任务探针 | 通过 | `default`、`prepare`、`report` 均完成无 AI 的持久探针 |
| 发布配置静态检查 | 通过 | 两份 Compose、Nginx、两份发布脚本 `bash -n`、Git diff 空白检查 |

8 个跳过项全部是 SQLite 不具备 PostgreSQL 并发锁语义的分支（Flight 1项、面试命令7项）；对应 PostgreSQL 分支实际执行。唯一 warning 为迁移保留旧数据测试使用的 SQLite datetime adapter 弃用提醒。指标聚合测试与100ms尽力写入超时分别验证，避免把宿主 TCP 建连抖动当作聚合错误；观测写入超时不会替换业务结果。日志回归同时覆盖应用旧模块和第三方日志中的敏感文本不会进入统一日志出口。

测试项目固定为 `resumeagent-engineering-test`：PG 使用临时文件系统，Redis 与上传卷独立，测试端口为18080–18083、55433和56380。Pytest 为每个 PostgreSQL 用例建立随机 schema 并清理。本轮没有迁移、重建或替换现有 `resumeagent` 业务项目，也未操作其数据库和上传数据。

验收结束后已删除本次隔离测试容器、网络和卷，宿主脱敏证据与截图保留；测试端口不再提供服务。

### 故障矩阵证据

| 明确检查点/故障 | 实际断言 |
| --- | --- |
| 发送供应商前 kill | 退出前无新供应商调用；显式重试后新增1次，只接受1个结果 |
| 供应商返回后、Flight 保存前 kill | 记录结果未知；不自动重发；人工重试新增1次，只接受1个结果 |
| Flight 写入前 kill | 与未知边界一致，未把响应伪装为已保存成功 |
| Flight 保存后、业务提交前 kill | 人工继续复用已知结果，新增供应商调用为0 |
| Redis 停机、冷启动和清空 | PG 事实不变；Web degraded 可读；Broker 恢复后 queued 任务重新投递 |
| 两实例并发同一命令 | 物理调用1次、接受结果1次；同 ID 不同内容和竞争新命令均拒绝 |
| Celery 认领任务后 kill | 过期标未知且不补调 AI；人工重试后继续 |
| dispatcher 发布后确认前 kill | 真实 Celery 重复投递2次，已完成结果复用，无额外 AI 调用 |
| 旧 owner 暂停、租约过期、新 owner 完成后恢复 | 旧提交被409拒绝；只接受1个结果、物理调用1次 |

脱敏调用计数、进程及断言保存在被 Git 忽略的 `backend/.test-work/faults/evidence.json`；容量原始数据在同目录 `capacity.json`，浏览器截图在 `frontend/test-results/`。CI 在失败时也保留这些指定路径的制品，不上传私有 `.env`。

### 本次容量边界

宿主为 Windows 11 AMD64、24逻辑 CPU；Docker 可见24 CPU、约7.61GiB内存。数据规模为10个测试用户、1份事实读取面试，每阶段30次已登录快照读取；突发10个请求，双 Web 实例共享供应商许可上限2。供应商为隔离可控协议服务，慢响应设为3秒。

| 指标 | 实测 |
| --- | --- |
| 正常事实读取 P50 / P95 / P99 | 5.85 / 10.95 / 11.12ms |
| 慢供应商下读取 P95 | 10.52ms |
| 失败供应商下读取 P95 / P99 | 10.66 / 11.63ms |
| 无响应供应商下读取 P95 | 9.04ms |
| 超载拒绝 P95 | **153.29ms**，低于500ms目标 |
| 故障/基线读取 P95 比值 | **0.974**，满足不劣化超过20%的目标 |
| 突发结果 | 10请求中8拒绝，在途峰值2，观测吞吐3.17请求/秒（含拒绝） |
| 供应商无响应 | `AI_TIMEOUT`，总耗时20.05秒；测试总预算20秒 |
| 熔断恢复 | 持续失败打开，冷却后探测成功 |
| 资源点采样 | 两 Web 为103.9/124MiB（各限1GiB），Celery 206.1MiB（限2GiB），DB连接14、排队任务0 |

以上为局部协议和隔离验证，不表示生产容量，也没有验证大历史数据规模或持续负载的资源峰值。真实供应商延迟、费用和输出质量不包含在这些数值中。

### 仍需对应环境验收

- 真实 LLM、讯飞 ASR/TTS、视觉模型凭据联调及质量/计费；显式演示和测试供应商不算实联通过。
- GitHub Actions 的远端执行、生产配置/SSH权限、提交 SHA 制品上传和实际部署；本地已验证相应脚本与门禁，未推送或发布。
- 预生产上一兼容镜像的回退演练、生产备份恢复、告警通知渠道及实际规模容量。旧硬编码 Alembic head 镜像会被回退兼容检查拒绝，不能通过数据库降级绕过。
- 实施前业务环境仍使用示例 `SECRET_KEY` 的问题未由本次测试修改。升级前须按手册设置稳定生产密钥；已有加密数据应停写并执行离线重加密。

## 历史记录：2026-10-16

验收日期：2026-10-16（Asia/Shanghai）。实现范围包括平台基础、简历评分出题、多 Agent 面试与报告、可靠执行、聊天配置、语音代理及可选视觉反馈。目标 AI-Meeting 项目仅作参考，未修改其文件。

### 当时已验证

| 检查 | 结果 | 说明 |
| --- | --- | --- |
| 后端完整 Pytest | 268 passed、11 skipped | Python 3.12.14；真实 PostgreSQL 18.3、Redis 7.4.2，加 SQLite 补充测试 |
| 配置冻结专项 | 4 passed | 新面试使用创建时的当前配置；管理员后续修改不改写已有面试；原面试历史不被覆盖 |
| 后端 Ruff | 通过 | 包含新平台、迁移与测试 |
| 前端 Vitest | 77 passed | 原有 51 项及新平台协议、用户流程、媒体采样和异步竞态回归 |
| 前端 lint、TypeScript、生产构建 | 通过 | lint 零警告，`frontend/dist/` 已更新 |
| 真实浏览器普通用户 E2E | 通过 | Chromium、真实 FastAPI/Celery/outbox/PostgreSQL/Redis，模型使用显式演示模式 |
| 真实浏览器管理员 E2E | 通过 | 账号权限、供应商与 Agent 管理、密钥保留且不回传、独立聊天 Agent、启停与测试配置清理 |
| 迁移 | 通过 | PostgreSQL upgrade、Alembic check 无模型差异；独立版本表，不修改旧版表 |
| Compose 静态结构 | 通过 | 迁移先于应用/Worker，异步进程和私有 uploads 卷依赖正确 |
| Git diff 空白检查 | 通过 | 未提交工作区代码检查，无冲突标记或尾部空白错误 |

完整后端套件的 11 个跳过项是 4 个原版占位 API 测试，以及 7 个不能用 SQLite 验证 PostgreSQL 行锁的重复参数分支。对应 PostgreSQL 并发分支实际执行，并非跳过了新平台数据库集成。

数据库集成使用本机专门建立的 55432 测试实例和随机独立 schema，测试后清理 schema；Redis 使用 56379 的独立测试实例。未操作原有 5432 数据库。Compose 仍使用计划中的 PostgreSQL 16 / Redis 7，测试 CI 也配置 PostgreSQL 16 / Redis 7；2026-10-16 验收时本机没有可用 Docker 命令，当时仅完成 Compose 静态检查。

### 当时的关键行为

- 同一请求并发提交或通过 REST/WebSocket 重放，只提交一个面试结果；不同请求回答同一题产生冲突。
- 原问题、真实评分、消息、报告均由 PostgreSQL 恢复；恢复读取不调用 AI，Redis 不可用时仍保有业务事实。
- 评分成功而追问失败或超时后，已知评分保存在命令节点进度中，显式重试只继续追问。
- 取消请求立即保留答案并显示未知状态；新请求可以由用户确认重试，过期执行者不能回写成功或失败覆盖新状态。
- 准备和报告任务租约过期后可由读取接口发现，展示错误并显式重试；Celery 重复投递不重复接受结果。
- 浏览器 E2E 覆盖完整五题、刷新后同一道题、追问、换问法、跳过零分、提前结束、异步报告、SSE 持久化、移动端无横向溢出、退出重登。
- 媒体测试覆盖 PCM 重采样跨块相位、增量转写去重/替换、队列背压、会话撤销、TTS 任务未知结果、私有音频归属、视觉并发控制和不留存截图。
- 网关测试覆盖真实分块时序、工具参数分片、工具白名单及预算、嵌套代码分析不死锁、Token 用量与取消审计。

浏览器截图位于被 Git 忽略的 `frontend/test-results/`。E2E 用脚本自建测试用户和配置，不包含真实业务数据。管理员测试凭据没有写入源代码或文档。

### 当时的外部验收边界

| 能力 | 当前边界 |
| --- | --- |
| 真实 LLM | 已实现 OpenAI 兼容网关、场景配置、工具与异常处理；需有效供应商凭据验证真实结果质量和计费 |
| 讯飞实时 ASR | 已实现签名、PCM WebSocket 转发、修订归并及清理；需开通对应产品，使用真实麦克风联调 |
| 讯飞长文本 TTS | 已实现创建、查询、持久任务和私有音频下载；需真实产品凭据验证合成与播放 |
| 视觉模型 | 已接入统一配额/并发/审计网关，反馈独立于评分；需支持图片的供应商模型实联 |
| Docker 部署 | 2026-10-20 已核实本机容器运行及 HTTP 健康，详见下文；完整容器业务 E2E 与干净环境重新构建仍待验证 |

规则演示、测试替身和静态协议验证均未计作真实供应商验收。

## 历史记录：2026-10-20 实施前启动检查与剩余工作

以下是工程化实施前的检查原文；配置修订、浏览器 CI、独立故障脚本的当前结果以上方本次验收为准。原业务环境的配置提醒仍适用。

- 本机 Docker Compose 的 backend、celery、frontend、postgres、redis 均为 running/healthy，outbox 为 running。首页 `http://localhost:8000/` 返回 200，`/health` 返回 `status=ok`；未执行数据重建。
- 修复 frontend 健康检查使用 `localhost` 时优先连接 IPv6、而 Nginx 只监听 IPv4 导致的误报。探针改为 `127.0.0.1`，仅重新创建 frontend 容器后健康检查通过；未重新构建镜像。
- 当前容器保持真实模式，但环境 LLM 密钥为空，后台也没有启用且含密钥的供应商；讯飞凭据未配置。管理员已存在。`SECRET_KEY` 仍为示例值，需要在保存真实供应商凭据前设置稳定随机值；更换后已有加密凭据需重新保存。
- 后台配置只有当前版本号及面试冻结快照，尚缺独立、可查询的配置历史修订记录；二者不能等同。
- 浏览器 E2E 脚本已存在，但 Playwright 依赖尚未随项目声明，CI 尚未启动全栈执行这些脚本。2026-10-16 的通过记录不能代表后续页面改动已重新通过全套验收。
- 已有真实 PostgreSQL 并发和租约测试主要使用同一进程中的异步任务。独立双实例、进程实际退出/重启、Redis 实际丢失和经 Broker 重复投递的故障演练仍需补充独立自动脚本与运行证据。
- 相对最初方案，当前范围已移除 RAG，摄像头改为进入面试室请求权限，出题改为首次开始面试时执行；这些行为以当前 `AGENTS.md` 和 `docs/platform.md` 为准。
