"""Tool Calling 工具模块

定义面试 Agent 可调用的工具，以及工具执行逻辑。
使用 OpenAI Function Calling 格式，兼容 DeepSeek。
"""

import json
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

# ============================================================
# 工具定义（OpenAI Function Calling 格式）
# ============================================================

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "lookup_resume_field",
            "description": "查看候选人简历中的具体字段信息。当需要引用候选人简历中的具体内容、验证信息一致性时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "field": {
                        "type": "string",
                        "enum": [
                            "work_experience",
                            "education",
                            "projects",
                            "skills",
                            "summary",
                        ],
                        "description": "要查看的简历字段",
                    },
                },
                "required": ["field"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_code_snippet",
            "description": "验证候选人提供的代码片段。检查语法正确性、最佳实践、潜在问题。当候选人声称会某段代码或给出技术方案时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "候选人提供的代码片段",
                    },
                    "language": {
                        "type": "string",
                        "description": "编程语言，如 Python、Java、SQL",
                    },
                    "context": {
                        "type": "string",
                        "description": "代码的业务背景，如 'Redis 缓存更新策略'",
                    },
                },
                "required": ["code", "language"],
            },
        },
    },
]


# ============================================================
# 工具执行函数
# ============================================================


def lookup_resume_field(field: str, resume_data: dict | None = None) -> str:
    """查看简历字段"""
    if not resume_data:
        return "简历数据不可用"

    value = resume_data.get(field)
    if value is None:
        return f"简历中没有 {field} 字段"

    if isinstance(value, list):
        if not value:
            return f"简历中 {field} 为空"
        return json.dumps(value, ensure_ascii=False, indent=2)

    return str(value)


def verify_code_snippet(code: str, language: str, context: str = "") -> str:
    """验证代码片段（调用 LLM 分析）"""
    from app.core.llm import call_llm

    prompt = f"""请分析以下代码片段，指出其中的问题或亮点：

语言：{language}
{f'背景：{context}' if context else ''}

代码：
```
{code}
```

请从以下角度分析（简洁，不超过 100 字）：
1. 语法是否正确
2. 是否有明显 bug 或安全隐患
3. 是否符合最佳实践
4. 如果有问题，给出改进建议"""

    result = call_llm(
        system_prompt="你是代码审查专家。简洁分析代码，不超过 100 字。",
        user_prompt=prompt,
        temperature=0.3,
        max_tokens=300,
    )
    return result or "无法分析代码片段"


# ============================================================
# 工具调度器
# ============================================================

# 工具名 → 执行函数的映射
_TOOL_REGISTRY: dict[str, Callable] = {
    "lookup_resume_field": lambda args: lookup_resume_field(
        field=args.get("field", ""),
        resume_data=args.get("_resume_data"),
    ),
    "verify_code_snippet": lambda args: verify_code_snippet(
        code=args.get("code", ""),
        language=args.get("language", ""),
        context=args.get("context", ""),
    ),
}


def execute_tool(
    tool_name: str,
    tool_args: dict,
    resume_data: dict | None = None,
) -> str:
    """执行工具调用

    Args:
        tool_name: 工具名称
        tool_args: 工具参数
        resume_data: 简历数据（lookup_resume_field 需要）

    Returns:
        工具执行结果文本
    """
    executor = _TOOL_REGISTRY.get(tool_name)
    if not executor:
        return f"未知工具: {tool_name}"

    # 注入简历数据
    if resume_data:
        tool_args["_resume_data"] = resume_data

    try:
        result = executor(tool_args)
        return result
    except Exception as e:
        logger.error(f"工具 {tool_name} 执行失败: {e}")
        return f"工具执行失败: {str(e)}"


def get_tools() -> list[dict]:
    """获取工具定义列表（供 LLM function calling 使用）"""
    return TOOLS
