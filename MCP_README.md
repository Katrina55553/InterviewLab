# Resume Agent — MCP Server

将简历智诊 Agent 封装为标准 MCP (Model Context Protocol) 技能包。

**无需后端服务，开箱即用。** 直接调用内部模块，不依赖 FastAPI。

## 可用工具（7 个）

| 工具 | 功能 | 参数 |
|------|------|------|
| `diagnose_resume` | 上传简历 → AI 解析 → 诊断 → 返回存疑点 | `file_path` |
| `start_interview` | 开始模拟面试，返回第一个问题 | `session_id`, `selected_point_ids`(可选) |
| `answer_interview` | 提交回答，返回下一个问题或报告 | `session_id`, `answer` |
| `skip_question` | 跳过当前存疑点 | `session_id` |
| `end_interview` | 提前结束面试并生成报告 | `session_id` |
| `get_report` | 获取评估报告 | `session_id` |
| `list_sessions` | 列出所有会话 | 无 |

## 快速开始

### 1. 安装依赖

```bash
cd backend
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY 等配置
```

### 3. 启动 MCP Server

```bash
python -m app.mcp.server
```

### 4. 集成到 AI 工具

#### Claude Code

在项目根目录创建 `.claude/settings.json`：

```json
{
  "mcpServers": {
    "resume-agent": {
      "command": "python",
      "args": ["-m", "app.mcp.server"],
      "cwd": "/path/to/resume-agent/backend"
    }
  }
}
```

#### Cursor

在 `.cursor/mcp.json` 中添加同样配置。

#### 其他 MCP 客户端

任何支持 MCP 协议的客户端都可以通过 stdio 方式连接。

## 使用示例

```
用户：帮我诊断一下 /Users/me/resume.pdf

AI：调用 diagnose_resume(file_path="/Users/me/resume.pdf")
    → 返回：session_id=xxx, 3 个存疑点（高优 1 个，中优 2 个）

用户：选前两个存疑点开始面试

AI：调用 start_interview(session_id="xxx", selected_point_ids=["dp1", "dp2"])
    → 返回第一个问题

用户：我用了 Redis 缓存热点数据...

AI：调用 answer_interview(session_id="xxx", answer="...")
    → 返回下一个问题或面试报告
```

## 架构

```
MCP Client (Claude Code / Cursor / 任意 MCP 客户端)
    │
    │ MCP Protocol (stdio)
    │
    ▼
MCP Server (app/mcp/server.py)  ← 独立进程，无需 FastAPI
    │
    │ 直接调用内部模块
    │
    ├── app/platform/service.py   → 鉴权资源归属与统一面试命令
    ├── app/platform/graph.py     → 简历准备、面试与报告
    ├── app/platform/runtime.py   → 冻结配置与 LLM 调用
    └── app/core/database.py      → PostgreSQL
```

## 支持的 AI 工具

| 工具 | 集成方式 |
|------|---------|
| Claude Code | `.claude/settings.json` |
| Cursor | `.cursor/mcp.json` |
| Windsurf | MCP 设置 |
| Continue | MCP 配置 |
| Cline | MCP 设置 |
| Roo Code | MCP 配置 |
| Aider | MCP 支持 |
| Zed | MCP 集成 |
| Sourcegraph Cody | MCP 支持 |
| GitHub Copilot (Agent) | MCP 支持 |
