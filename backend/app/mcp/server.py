"""Authenticated stdio adapter for the shared platform application service.

RESUME_AGENT_MCP_TOKEN is a browser session token supplied by trusted process
configuration, never a tool argument. Revocation applies to every invocation.
"""

import asyncio
import hashlib
import json
import os

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
from sqlalchemy import select

from app.core.database import async_session_maker
from app.platform.api import InterviewCommand
from app.platform.models import LoginSession, User, utcnow
from app.platform.service import create_interview, execute_command, get_interview

server = Server("resume-agent-platform")


async def _user_id() -> str:
    token = os.environ.get("RESUME_AGENT_MCP_TOKEN", "")
    if not token:
        raise ValueError("MCP 未配置可信会话，请设置 RESUME_AGENT_MCP_TOKEN")
    async with async_session_maker() as db:
        user_id = await db.scalar(select(User.id).join(LoginSession, LoginSession.user_id == User.id).where(
            LoginSession.id == hashlib.sha256(token.encode()).hexdigest(), LoginSession.expires_at > utcnow(),
        ))
    if not user_id:
        raise ValueError("MCP 会话无效或已撤销，请重新配置登录会话")
    return user_id


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="create_interview", description="为准备完成的简历创建独立面试，不覆盖历史。", inputSchema={
            "type": "object", "properties": {"resume_id": {"type": "string"}}, "required": ["resume_id"],
        }),
        Tool(name="interview_snapshot", description="读取当前面试状态或报告，不调用 AI。", inputSchema={
            "type": "object", "properties": {"interview_id": {"type": "string"}}, "required": ["interview_id"],
        }),
        Tool(name="interview_command", description="执行开始、回答、跳过、换问法或结束；需最新版本、题目ID和唯一请求ID。", inputSchema={
            "type": "object", "properties": {"interview_id": {"type": "string"}, "command": InterviewCommand.model_json_schema()},
            "required": ["interview_id", "command"],
        }),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        user_id = await _user_id()
        if name == "create_interview":
            result = await create_interview(user_id, arguments["resume_id"])
        elif name == "interview_snapshot":
            result = await get_interview(user_id, arguments["interview_id"])
        elif name == "interview_command":
            command = InterviewCommand.model_validate(arguments["command"])
            result = await execute_command(user_id, arguments["interview_id"], command.model_dump())
        else:
            raise ValueError("未知工具")
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
    except Exception as exc:
        return [TextContent(type="text", text=json.dumps({"error": str(getattr(exc, "detail", str(exc)))}, ensure_ascii=False))]


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
