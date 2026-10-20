<div align="center">

> 当前版本已扩展为 Python AI 面试平台：账号、简历评分出题、独立面试历史、LangGraph、多轮报告、聊天、管理员配置及可选语音/视觉。请优先阅读 [平台运行与架构说明](docs/platform.md)；下方为原版项目介绍。

# 简历智诊 Agent

**AI 驱动的简历深度诊断与多轮模拟面试系统**

上传简历 → AI 结构化解析 → 智能诊断存疑点 → LangGraph 状态机驱动动态追问 → 量化评估报告

[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2-FF6B6B?logo=langchain&logoColor=white)](https://langchain-ai.github.io/langgraph/)
[![Tailwind CSS](https://img.shields.io/badge/Tailwind-4-38B2AC?logo=tailwind-css&logoColor=white)](https://tailwindcss.com/)
[![DeepSeek](https://img.shields.io/badge/LLM-DeepSeek-4765FF)](https://platform.deepseek.com/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

</div>

---

## 产品特性

### 智能简历诊断
- **多格式支持**：PDF / Word / TXT，四重校验（扩展名 + MIME + 魔数 + 大小）
- **AI 结构化解析**：DeepSeek LLM 提取个人信息、工作经历、项目经历、教育经历、技能
- **三级存疑点标注**：高 / 中 / 低优先级，自动检测技能夸大、项目描述模糊、时间线异常
- **用户可修正**：解析结果内联编辑，修正内容反馈给后续面试流程

### LangGraph 多轮模拟面试
- **状态机驱动**：`question → collect → evaluate → (follow_up | next_point | report)`
- **动态追问**：根据回答内容自动判断追问深度，单点最多 3 轮
- **智能工具调用**：Agent 可自主调用 RAG 知识库 / 查看简历字段 / 验证代码片段
- **防死循环**：单点 3 轮强制切换 + 用户主动跳过 + 连续异常 3 次熔断

### WebSocket 实时对话
- **流式消息**：LLM 响应分段推送到前端，消除长等待
- **断线自动重连**：指数退避 + 消息队列缓存，保证会话不中断
- **Redis 消息缓冲**：重连时自动推送未消费消息
- **双向协议**：客户端 → `answer | skip | rephrase | start`，服务端 → `question | status | complete | error`

### RAG 三级检索
- **Elasticsearch 全文检索**：存储技术题库，精确匹配关键词
- **向量相似度检索**：语义匹配，发现潜在相关问题
- **关键词降级**：ES 与向量均无结果时，基于标签兜底推荐
- **内置知识库**：Python / MySQL / Redis / Docker / 系统设计

### 安全与隐私
- **文件上传安全**：内容嗅探 + 魔数校验 + 大小限制 + 白名单扩展名
- **敏感信息脱敏**：手机号 / 邮箱 / 身份证号三级脱敏（Prompt 层 + API 响应层 + DB 存储层）
- **Prompt 防护**：关键词黑名单 + 长度限制 + 注入检测
- **速率限制**：每分钟请求限流 + 每用户 Token 配额

### 精致 UI 体验
- **Refined Editorial 设计风格**：暖纸色背景 + 墨黑排版 + 翡翠绿点缀
- **Fraunces 衬线展示字体** × **Plus Jakarta Sans 正文**：优雅排版层次
- **错峰入场动画**：页面区块依次动效呈现，提升感知品质
- **玻璃质感卡片** + **纸质容器**：组件层次分明，视觉焦点清晰

---

## 技术架构

```
┌─────────────────────────────────────────────────────────────┐
│                        前端 (React SPA)                      │
│  pages: HomePage → ParsePage → DiagnosePage → InterviewPage → ReportPage │
│  stores: sessionStore → diagnoseStore → interviewStore → reportStore      │
│  hooks: useWebSocket, useVoiceChat                           │
└─────────────────────────────┬───────────────────────────────┘
                              │ HTTPS + WebSocket
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Nginx 反向代理 :8000                      │
│  静态文件 serve + /api, /ws 代理到 backend                   │
└─────────────────────────────┬───────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                 FastAPI 应用服务 :8000                       │
│  REST: sessions.py, diagnose.py, interview.py, report.py     │
│  WebSocket: ws.py (实时对话 + 消息缓存)                        │
│  MCP Server: server.py (外部 Agent 接入)                      │
└────┬──────────────┬──────────────┬──────────────┬───────────┘
     │              │              │              │
     ▼              ▼              ▼              ▼
┌─────────┐   ┌─────────┐   ┌──────────────┐   ┌──────────────┐
│ LangGraph│  │ OpenAI SDK│  │ Elasticsearch │   │  Celery Worker│
│ 状态机   │   │ DeepSeek  │   │ RAG 检索     │   │ 异步任务    │
│ nodes/   │   │ 多模型路由 │   │ 技术题库     │   │ 解析/诊断  │
└─────────┘   └─────────┘   └──────────────┘   └──────┬───────┘
                                                       │
┌──────────────────────────────────────────────────────┼───────┐
│                      数据层                           ▼       │
│  PostgreSQL 16  (结构化数据)     Redis 7 (缓存 + 消息队列)    │
└───────────────────────────────────────────────────────────────┘
```

### 技术栈详情

| 层 | 技术 |
|----|------|
| 前端 | React 19 + TypeScript + Vite + Tailwind CSS 4 + Zustand + React Router v7 + React Query |
| 后端 | FastAPI 0.115 + LangGraph + Celery + SQLAlchemy 2.0 |
| LLM | DeepSeek API（OpenAI 兼容格式），支持多模型路由（轻量 / 标准 / 推理） |
| 数据库 | PostgreSQL 16 + Redis 7 + Elasticsearch 8 |
| 部署 | Docker Compose + Nginx |
| 安全 | 敏感信息脱敏 + 文件内容校验 + 速率限制 + Prompt 防护 |
| 监控 | OpenTelemetry（API / 任务 / 对话） |

---

## 四步使用流程

```
┌────────────┐     ┌────────────┐     ┌────────────┐     ┌────────────┐
│  Step 01   │     │  Step 02   │     │  Step 03   │     │  Step 04   │
│  简历上传  │─→  │  解析确认  │─→  │  诊断报告  │─→  │  模拟面试  │
└────────────┘     └────────────┘     └────────────┘     └────────────┘
                                                              ↓
                                                    ┌────────────┐
                                                    │  Step 05   │
                                                    │  评估报告  │
                                                    └────────────┘
```

| 步骤 | 页面 | 核心交互 |
|------|------|----------|
| 01 · 上传 | [HomePage.tsx](frontend/src/pages/HomePage.tsx) | 拖拽 / 点击上传，实时解析进度 |
| 02 · 解析 | [ParsePage.tsx](frontend/src/pages/ParsePage.tsx) | 内联编辑 AI 提取的结构化字段 |
| 03 · 诊断 | [DiagnosePage.tsx](frontend/src/pages/DiagnosePage.tsx) | 查看存疑点，勾选面试范围 |
| 04 · 面试 | [InterviewPage.tsx](frontend/src/pages/InterviewPage.tsx) | WebSocket 实时对话，多轮追问 |
| 05 · 报告 | [ReportPage.tsx](frontend/src/pages/ReportPage.tsx) | 可信度评分 + 逐点反馈 + 改进建议 |

---

## 快速开始

### 方式一：Docker Compose 一键部署（推荐）

```bash
# 1. 克隆项目
git clone https://github.com/Katrina55553/resume-agent.git
cd resume-agent

# 2. 配置环境变量
cp backend/.env.example backend/.env
# 编辑 backend/.env，填入你的 DeepSeek API Key：
#   LLM_API_KEY=sk-your-deepseek-key-here

# 3. 启动所有服务
docker compose up -d --build

# 4. 访问应用
# 浏览器打开 http://localhost:8000
```

> 💡 **LLM 可选**：若不填 `LLM_API_KEY`，系统会切换到规则降级模式，不影响完整流程体验。

### 方式二：本地开发

```bash
# ─── 1. 启动数据库与缓存 ────────────────────────────────
docker compose up -d postgres redis elasticsearch

# ─── 2. 后端服务 ────────────────────────────────────────
cd backend
pip install -r requirements.txt
cp .env.example .env
# 修改 .env：
#   DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/resume_agent
#   REDIS_URL=redis://localhost:6379/0
#   ELASTICSEARCH_URL=http://localhost:9200
#   LLM_API_KEY=sk-your-key-here

# 启动 FastAPI (端口 8000)
uvicorn app.main:app --reload --port 8000

# 另起终端启动 Celery Worker
celery -A app.tasks.celery_app worker --loglevel=info

# ─── 3. 前端服务 ────────────────────────────────────────
cd frontend
npm install
npm run dev
# 浏览器打开 http://localhost:5173
```

### 常用命令

```bash
# 查看日志
docker compose logs backend --tail 100      # 后端
docker compose logs celery --tail 50        # 异步任务
docker compose logs frontend --tail 50      # 前端 Nginx

# 单独重建某服务
docker compose up -d --build backend        # 仅重建后端
docker compose up -d --build frontend       # 仅重建前端

# 停止所有服务
docker compose down
```

---

## API 端点

### REST API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/sessions` | 上传简历（multipart/form-data） |
| `GET` | `/api/sessions/{id}/status` | 获取解析进度（轮询） |
| `GET` | `/api/sessions/{id}/parse` | 获取 AI 解析结果 |
| `PUT` | `/api/sessions/{id}/parse` | 用户修正解析结果 |
| `POST` | `/api/sessions/{id}/diagnose` | 触发诊断（异步任务） |
| `GET` | `/api/sessions/{id}/diagnose` | 获取诊断报告（存疑点列表） |
| `POST` | `/api/sessions/{id}/interview/start` | 开始面试（REST 模式） |
| `POST` | `/api/sessions/{id}/interview/respond` | 提交回答（REST 模式） |
| `POST` | `/api/sessions/{id}/interview/skip` | 跳过当前问题 |
| `POST` | `/api/sessions/{id}/interview/rephrase` | 换个问法 |
| `GET` | `/api/sessions/{id}/interview/resume` | 恢复面试（消息列表 + 当前状态） |
| `GET` | `/api/sessions/{id}/report` | 获取量化评估报告 |
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/docs` | Swagger 文档（FastAPI 自动生成） |
| `GET` | `/openapi.json` | OpenAPI 规范 JSON |

### WebSocket 协议

- **连接**：`ws://{host}/ws/interview/{session_id}`
- **客户端消息**（JSON）：
  ```jsonc
  // 提交回答
  { "type": "answer", "content": "我在该项目中负责..." }

  // 跳过问题
  { "type": "skip" }

  // 换个问法
  { "type": "rephrase" }

  // 开始面试（可选，通常 REST 先调用）
  { "type": "start" }
  ```
- **服务端消息**（JSON）：
  ```jsonc
  // 面试官问题（流式 chunk）
  { "type": "chunk", "content": "请详细说说..." }
  { "type": "question", "content": "完整的问题内容...", "point_index": 2 }

  // 状态更新
  { "type": "status", "message": "正在调用知识库检索..." }

  // 面试完成
  { "type": "complete", "redirect_to": "/session/{id}/report" }

  // 错误
  { "type": "error", "code": 2001, "message": "LLM 超时" }
  ```

---

## 项目结构

```
resume-agent/
├── frontend/                              # React SPA
│   ├── src/
│   │   ├── pages/                         # 5 个页面组件
│   │   │   ├── HomePage.tsx              # 首页（上传）
│   │   │   ├── ParsePage.tsx             # 解析确认
│   │   │   ├── DiagnosePage.tsx          # 诊断报告
│   │   │   ├── InterviewPage.tsx         # 模拟面试（WebSocket）
│   │   │   └── ReportPage.tsx            # 评估报告
│   │   ├── components/Upload/            # FileDropzone, UploadProgress
│   │   ├── stores/                        # Zustand 状态管理
│   │   │   ├── sessionStore.ts           # 上传 / 解析
│   │   │   ├── diagnoseStore.ts          # 诊断 + 存疑点
│   │   │   ├── interviewStore.ts         # WebSocket 面试
│   │   │   └── reportStore.ts            # 评估报告
│   │   ├── hooks/                         # useWebSocket, useVoiceChat
│   │   ├── utils/api.ts                   # Axios 封装
│   │   └── index.css                      # 全局设计系统 + Tailwind
│   ├── Dockerfile                          # 多阶段构建 + Nginx
│   └── nginx.conf                          # 反代配置
│
├── backend/                               # FastAPI 服务
│   ├── app/
│   │   ├── api/                            # REST + WebSocket
│   │   │   ├── sessions.py                # 上传 / 解析
│   │   │   ├── diagnose.py                # 诊断
│   │   │   ├── interview.py               # REST 面试
│   │   │   ├── report.py                  # 评估报告
│   │   │   └── ws.py                      # WebSocket 实时对话
│   │   ├── core/                           # 核心模块
│   │   │   ├── config.py                  # pydantic-settings 配置
│   │   │   ├── database.py                # SQLAlchemy + 连接池
│   │   │   ├── redis.py                   # Redis 客户端
│   │   │   ├── llm.py                     # 统一 LLM 调用 + 工具解析
│   │   │   ├── rag.py                     # ES + 向量混合检索
│   │   │   ├── tools.py                   # Agent 可调用工具
│   │   │   ├── es.py                      # ES 初始化
│   │   │   ├── errors.py                  # 统一错误码
│   │   │   └── msg_cache.py               # WebSocket 消息缓存
│   │   ├── agent/                          # LangGraph 状态机
│   │   │   ├── graph.py                   # 图定义 + 入口
│   │   │   ├── state.py                   # 状态定义
│   │   │   ├── rules.py                   # 规则（追问上限等）
│   │   │   └── nodes/                      # 节点实现
│   │   │       ├── question.py            # 生成问题
│   │   │       ├── collect.py             # 收集回答
│   │   │       ├── evaluate.py            # 评估 + 分支决策
│   │   │       └── report.py              # 生成报告
│   │   ├── tasks/                          # Celery 异步任务
│   │   │   ├── celery_app.py              # Celery 应用
│   │   │   ├── parse_task.py              # 简历解析
│   │   │   ├── diagnose_task.py           # 存疑点诊断
│   │   │   └── report_task.py             # 报告生成
│   │   ├── models/                         # ORM + Pydantic 模型
│   │   ├── middleware/                     # auth, rate_limit
│   │   ├── mcp/server.py                   # MCP 协议服务
│   │   ├── utils/security/                 # 安全工具
│   │   │   ├── file_upload.py             # 文件内容嗅探 + 魔数校验
│   │   │   ├── masking.py                 # 敏感信息脱敏（三级）
│   │   │   └── prompt_guard.py            # Prompt 防护
│   │   └── main.py                        # FastAPI 入口
│   ├── knowledge/                          # RAG 知识库 JSON
│   │   ├── python.json
│   │   ├── mysql.json
│   │   ├── redis.json
│   │   ├── docker.json
│   │   └── system_design.json
│   ├── tests/                              # pytest 单元测试
│   ├── requirements.txt                     # Python 依赖
│   └── Dockerfile                           # FastAPI 容器
│

├── docker-compose.yml                       # 6 服务编排
├── AGENTS.md                                # Agent 开发规范（给 AI 协作者）
└── README.md
```

---

## 核心设计

### LangGraph 状态机

```
                          ┌──────────────┐
                          │   question   │  生成当前存疑点问题
                          └──────┬───────┘
                                 │
                          ┌──────▼───────┐
                          │   collect    │  接收并格式化用户回答
                          └──────┬───────┘
                                 │
                          ┌──────▼───────┐
                          │  evaluate    │  评估回答 + 分支决策
                          └──┬───┬───┬───┘
                             │   │   │
              ┌──────────────┘   │   └──────────────┐
              │                  │                  │
     ┌────────▼────────┐  ┌──────▼────────┐  ┌─────▼───────┐
     │   follow_up     │  │  next_point    │  │   report    │
     │  继续追问同一点  │  │  切换下一存疑点 │  │  生成评估报告 │
     └────────┬────────┘  └────────────────┘  └─────────────┘
              │
              └──────────────────────→ 回到 question
```

### 追问防死循环策略

1. **单点上限**：每个存疑点最多追问 3 轮，超限强制切换到下一点
2. **用户主动跳过**：前端提供跳过按钮，Agent 记录跳过状态
3. **异常熔断**：连续异常 3 次，触发安全熔断，结束当前点面试

### 多模型路由

| 模型 | 适用场景 | 配置项 |
|------|----------|--------|
| `deepseek-chat` | 常规对话、生成问题 | `LLM_MODEL` |
| `deepseek-chat` (light) | 文本分类、快速抽取 | `LLM_MODEL_LIGHT` |
| `deepseek-reasoner` | 代码验证、深度推理 | `LLM_MODEL_HEAVY` |
| `deepseek-embedding` | 向量检索 | `LLM_MODEL_EMBEDDING` |

### WebSocket 消息缓存与恢复

- **消息缓存**：Redis Stream 结构存储会话消息，TTL 24 小时
- **重连机制**：前端指数退避（500ms → 1s → 2s → 4s → 最多 5 次）
- **恢复流程**：重连后前端发送 `GET /api/sessions/{id}/interview/resume` 获取历史消息 + 当前状态

---

## 开发指南

### 添加新的 RAG 知识类目

1. 在 `backend/knowledge/` 新建 JSON 文件，格式：
   ```json
   {
     "category": "your-topic",
     "questions": [
       { "id": "q1", "question": "...", "answer": "...", "tags": ["t1"] }
     ]
   }
   ```
2. 重启后端，ES 索引会自动初始化。

### 添加新的 Agent 工具

在 `backend/app/core/tools.py` 定义函数，然后在 `backend/app/core/llm.py` 的 `AVAILABLE_TOOLS` 中注册。

### 扩展 LLM 到其他提供商

修改 `backend/.env` 中的 `LLM_BASE_URL` 为任意 OpenAI 兼容端点：
- **Claude Anthropic**：`https://api.anthropic.com/v1`（需适配兼容层）
- **Ollama**：`http://localhost:11434/v1`
- **SiliconFlow**：`https://api.siliconflow.cn/v1`

### 贡献指南

1. Fork 本仓库
2. 创建特性分支：`git checkout -b feat/amazing-feature`
3. 提交改动：`git commit -m 'feat: 添加某某功能'`
4. 推送分支：`git push origin feat/amazing-feature`
5. 提交 Pull Request

> 请确保提交前通过 `lint` 和测试：
> ```bash
> cd frontend && npm run lint && cd ..
> cd backend && ruff check . && python -m pytest -q && cd ..
> ```

---

## 环境变量配置

`backend/.env` 主要配置项：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@postgres:5432/resume_agent` | PostgreSQL 连接字符串 |
| `REDIS_URL` | `redis://redis:6379/0` | Redis 连接 |
| `ELASTICSEARCH_URL` | `http://elasticsearch:9200` | ES 连接 |
| `LLM_BASE_URL` | `https://api.deepseek.com` | LLM API 端点（OpenAI 兼容） |
| `LLM_API_KEY` | *(空)* | API Key（空则规则降级） |
| `LLM_MODEL` | `deepseek-chat` | 默认模型名 |
| `LLM_MODEL_HEAVY` | `deepseek-reasoner` | 推理任务模型 |
| `SECRET_KEY` | `change-me-in-production` | Token 签名密钥（生产必须修改） |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:8000` | 允许的来源 |
| `MAX_UPLOAD_SIZE_MB` | `10` | 最大上传文件大小（1–100） |
| `RATE_LIMIT_PER_MINUTE` | `60` | 每分钟请求限流 |
| `ENVIRONMENT` | `development` | `development | staging | production` |

详见 [backend/.env.example](backend/.env.example)。

---

## 更多文档

- [系统设计教程（GitHub Wiki）](https://github.com/Katrina55553/Resume-Agent/wiki)
- [Agent 开发规范（AGENTS.md）](AGENTS.md)
- [MCP Server 文档（MCP_README.md）](MCP_README.md)

---

## 许可证

本项目采用 [MIT License](LICENSE) 开源，欢迎商业使用与二次开发。

---

<div align="center">

Made with ❤️ by [Katrina55553](https://github.com/Katrina55553)

</div>
