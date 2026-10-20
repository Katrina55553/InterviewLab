"""LLM 调用模块

提供统一的 LLM 调用接口，使用 OpenAI 兼容格式（支持 DeepSeek 等第三方）。
所有节点共用此模块，避免重复初始化客户端。
支持 Tool Calling（Function Calling）。
"""

import json
import logging
import re
from collections.abc import Iterator
from typing import Any

from openai import OpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: OpenAI | None = None

_DSML_TOOL_RE = re.compile(r"<\s*/?\s*[｜|]{0,4}\s*DSML\s*[｜|]{0,4}\s*(tool_calls|invoke|call)?", re.IGNORECASE)
_TOOL_REQUIRED_ARGS = {
    "lookup_resume_field": {"field"},
    "verify_code_snippet": {"code", "language"},
}


def _get_client() -> OpenAI | None:
    """懒加载 OpenAI 客户端"""
    global _client
    if _client is None:
        if not settings.LLM_API_KEY:
            return None
        _client = OpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
        )
    return _client


def call_llm(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> str | None:
    """调用 LLM，返回文本响应。

    Returns:
        LLM 响应文本，失败时返回 None。
    """
    client = _get_client()
    if client is None:
        logger.warning("LLM API key 未配置，跳过 LLM 调用")
        return None

    try:
        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return _clean_llm_text_response(response.choices[0].message.content)
    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        return None


def call_llm_stream(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    task_type: str = "question",
) -> Iterator[str]:
    """流式调用 LLM，逐 chunk yield 文本片段。

    自动过滤 DeepSeek DSML 工具调用标签和标签前的推理文字。
    检测到 DSML 标签后，丢弃该标签及之后的所有内容。

    Yields:
        每个 chunk 的文本内容。最后一个 yield 后，调用结束。

    Usage:
        for chunk in call_llm_stream(sys, usr):
            send_to_client(chunk)
    """
    client = _get_client()
    if client is None:
        return

    level = classify_task(task_type)
    pool = _MODEL_POOL[level]

    try:
        stream = client.chat.completions.create(
            model=pool["model"],
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            stream=True,
        )

        chunk_count = 0
        chunks: list[str] = []

        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                raw = chunk.choices[0].delta.content
                chunk_count += 1
                logger.debug(f"[Stream] chunk {chunk_count}: {repr(raw[:20])}")
                chunks.append(raw)

        full_text = "".join(chunks)
        filtered_text = _strip_dsml_tool_text(full_text)
        dsml_detected = bool(full_text and not filtered_text)
        if dsml_detected:
            logger.info("[Stream] 检测到 DSML 标签，丢弃本次流式模型输出")
        else:
            for chunk in chunks:
                yield chunk

        logger.info(f"[Stream] 完成，共 {chunk_count} 个 chunk, DSML 过滤: {dsml_detected}")

    except Exception as e:
        logger.error(f"LLM 流式调用失败 [{task_type}]: {e}")
        return


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> dict | None:
    """调用 LLM 并解析 JSON 响应。

    自动处理 ```json ... ``` 包裹。

    Returns:
        解析后的 dict，失败时返回 None。
    """
    text = call_llm(system_prompt, user_prompt, temperature, max_tokens)
    if text is None:
        return None

    # 提取 JSON
    json_match = re.search(r"\{[\s\S]*\}", text)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            logger.error(f"LLM 返回的 JSON 解析失败: {text[:200]}")
            return None

    # 尝试直接解析（可能没有包裹）
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.error(f"LLM 返回内容不是有效 JSON: {text[:200]}")
        return None


def is_llm_available() -> bool:
    """检查 LLM 是否可用"""
    return bool(settings.LLM_API_KEY)


# ============================================================
# 多模型路由
# ============================================================

# 模型池：不同复杂度用不同模型
_MODEL_POOL = {
    "light": {
        "model": settings.LLM_MODEL_LIGHT,
        "cost_per_1k": 0.001,    # ¥0.001/千token
        "max_tokens": 2048,
    },
    "medium": {
        "model": settings.LLM_MODEL_LIGHT,
        "cost_per_1k": 0.001,
        "max_tokens": 4096,
    },
    "heavy": {
        "model": settings.LLM_MODEL_HEAVY,
        "cost_per_1k": 0.004,    # ¥0.004/千token
        "max_tokens": 4096,
    },
}

# 任务类型 → 复杂度映射
_TASK_COMPLEXITY = {
    "question": "light",
    "rephrase": "light",
    "parse": "medium",
    "diagnose": "medium",
    "evaluate": "heavy",
    "report": "heavy",
}

# 成本统计
_cost_log: list[dict] = []


def classify_task(task_type: str, context: dict = None) -> str:
    """根据任务类型和上下文判断复杂度

    Args:
        task_type: 任务类型 (question/evaluate/report/parse/diagnose)
        context: 可选上下文，用于动态调整复杂度

    Returns:
        复杂度等级: "light" | "medium" | "heavy"
    """
    ctx = context or {}
    base = _TASK_COMPLEXITY.get(task_type, "medium")

    # 动态调整（只在 context 明确提供时生效）
    if task_type == "evaluate" and "answer_length" in ctx and ctx["answer_length"] < 50:
        return "medium"  # 回答很短，评估不需要旗舰模型

    if task_type == "question" and "current_round" in ctx and ctx["current_round"] >= 3:
        return "medium"  # 深度追问需要更好理解

    return base


def call_llm_routed(
    task_type: str,
    system_prompt: str,
    user_prompt: str,
    context: dict = None,
    temperature: float = None,
    max_tokens: int = None,
) -> str | None:
    """根据任务类型自动选择模型调用 LLM

    Args:
        task_type: 任务类型 (question/evaluate/report/parse/diagnose)
        system_prompt: 系统提示
        user_prompt: 用户提示
        context: 可选上下文，用于动态路由
        temperature: 覆盖默认温度
        max_tokens: 覆盖默认 max_tokens

    Returns:
        LLM 响应文本，失败返回 None
    """
    level = classify_task(task_type, context)
    pool = _MODEL_POOL[level]

    client = _get_client()
    if client is None:
        return None

    try:
        response = client.chat.completions.create(
            model=pool["model"],
            temperature=temperature if temperature is not None else (0.3 if level == "heavy" else 0.7),
            max_tokens=max_tokens or pool["max_tokens"],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        text = _clean_llm_text_response(response.choices[0].message.content)

        # 记录成本
        usage = response.usage
        if usage:
            cost = (usage.prompt_tokens + usage.completion_tokens) / 1000 * pool["cost_per_1k"]
            _cost_log.append({
                "task": task_type,
                "model": pool["model"],
                "level": level,
                "tokens": usage.prompt_tokens + usage.completion_tokens,
                "cost": cost,
            })

        return text
    except Exception as e:
        logger.error(f"LLM 调用失败 [{task_type}/{level}]: {e}")
        return None


def call_llm_routed_json(
    task_type: str,
    system_prompt: str,
    user_prompt: str,
    context: dict = None,
    **kwargs,
) -> dict | None:
    """路由调用 + JSON 解析"""
    text = call_llm_routed(task_type, system_prompt, user_prompt, context, **kwargs)
    if text is None:
        return None
    json_match = re.search(r"\{[\s\S]*\}", text)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.error(f"JSON 解析失败: {text[:200]}")
        return None


def get_cost_summary() -> dict:
    """获取本次会话的 LLM 调用成本统计"""
    if not _cost_log:
        return {"total_cost": 0, "calls": 0, "by_level": {}}

    by_level = {}
    for entry in _cost_log:
        level = entry["level"]
        if level not in by_level:
            by_level[level] = {"calls": 0, "tokens": 0, "cost": 0}
        by_level[level]["calls"] += 1
        by_level[level]["tokens"] += entry["tokens"]
        by_level[level]["cost"] += entry["cost"]

    total = sum(e["cost"] for e in _cost_log)
    # 计算全用旗舰模型的成本
    heavy_cost = _MODEL_POOL["heavy"]["cost_per_1k"]
    all_heavy = sum(e["tokens"] for e in _cost_log) / 1000 * heavy_cost

    return {
        "total_cost": round(total, 4),
        "calls": len(_cost_log),
        "all_heavy_cost": round(all_heavy, 4),
        "savings": round((1 - total / all_heavy) * 100, 1) if all_heavy > 0 else 0,
        "by_level": by_level,
    }


def _parse_tool_calls_from_text(content: str) -> list[dict[str, Any]]:
    """从文本内容中解析工具调用（兼容 DeepSeek 等不支持结构化 tool_calls 的模型）。

    DeepSeek 实际输出格式：
    <｜｜DSML｜｜tool_calls>
    <｜｜DSML｜｜invoke name="lookup_resume_field">
    <｜｜DSML｜｜invoke name="lookup_resume_field">
    </｜｜DSML｜｜invoke>
    </｜｜DSML｜｜invoke>
    </｜｜DSML｜｜tool_calls>
    """
    tool_calls = []
    known_tools = {"lookup_resume_field", "verify_code_snippet"}

    # 模式 1：XML 属性格式 — name="func_name"
    # 匹配 <...invoke name="func_name"...> 或 <...call name="func_name"...>
    xml_pattern = r'name\s*=\s*["\'](\w+)["\']'
    matches = re.findall(xml_pattern, content)
    if matches:
        seen = set()
        for func_name in matches:
            if func_name in known_tools and func_name not in seen:
                seen.add(func_name)
                # 尝试从内容中提取 JSON 参数
                args = _extract_json_near_tool(content, func_name)
                if not _has_required_tool_args(func_name, args):
                    logger.warning("忽略参数不完整的 DSML 工具调用: %s", func_name)
                    continue
                tool_calls.append({
                    "id": f"call_{len(tool_calls)}",
                    "name": func_name,
                    "arguments": args,
                })
        if tool_calls:
            return tool_calls

    # 模式 2：函数调用格式 — func_name({...})
    func_pattern = r'(lookup_resume_field|verify_code_snippet)\s*\((\{[^)]*\})\)'
    matches = re.findall(func_pattern, content, re.DOTALL)
    if matches:
        for func_name, args_str in matches:
            try:
                args = json.loads(args_str)
            except json.JSONDecodeError:
                args = {}
            if not _has_required_tool_args(func_name, args):
                logger.warning("忽略参数不完整的文本工具调用: %s", func_name)
                continue
            tool_calls.append({
                "id": f"call_{len(tool_calls)}",
                "name": func_name,
                "arguments": args,
            })
        return tool_calls

    return tool_calls


def _has_required_tool_args(func_name: str, args: dict) -> bool:
    """检查文本 fallback 解析出的工具参数是否足够执行。"""
    required = _TOOL_REQUIRED_ARGS.get(func_name, set())
    return bool(args) and all(str(args.get(key, "")).strip() for key in required)


def _clean_llm_text_response(content: str | None) -> str:
    """清洗普通 LLM 文本响应，防止 DSML 工具协议泄漏到业务层。"""
    return _strip_dsml_tool_text(content or "").strip()


def _strip_dsml_tool_text(content: str) -> str:
    """移除 DSML 工具调用文本，避免把模型内部工具协议展示给用户。"""
    if not content:
        return ""
    if _DSML_TOOL_RE.search(content):
        return ""
    return content


def _extract_json_near_tool(content: str, func_name: str) -> dict:
    """尝试从工具调用附近提取 JSON 参数

    优先查找 func_name 后面紧跟的 JSON，找不到再用全文第一个 JSON。
    """
    # 先找 func_name 附近的 JSON（func_name 后 200 字符内）
    func_pos = content.find(func_name)
    if func_pos >= 0:
        nearby = content[func_pos:func_pos + 200]
        json_matches = re.findall(r'\{[^{}]+\}', nearby)
        for jm in json_matches:
            try:
                return json.loads(jm)
            except json.JSONDecodeError:
                continue

    # 降级：找全文中的 JSON
    json_matches = re.findall(r'\{[^{}]+\}', content)
    for jm in json_matches:
        try:
            return json.loads(jm)
        except json.JSONDecodeError:
            continue
    return {}


def call_llm_with_tools(
    system_prompt: str,
    user_prompt: str,
    tools: list[dict],
    temperature: float = 0.7,
    max_tokens: int = 2048,
    task_type: str = "question",
) -> tuple[str | None, list[dict[str, Any]]]:
    """调用 LLM 并支持 Tool Calling。

    先尝试结构化 tool_calls（OpenAI 格式），
    如果模型不支持则从文本内容中解析工具调用（DeepSeek 兼容）。

    Returns:
        (content, tool_calls) 元组：
        - content: LLM 的文本回复（如果有）
        - tool_calls: 工具调用列表 [{"id": ..., "name": ..., "arguments": {...}}, ...]
        - 如果调用失败返回 (None, [])
    """
    client = _get_client()
    if client is None:
        logger.warning("LLM API key 未配置，跳过 LLM 调用")
        return None, []

    # 根据任务类型选择模型
    level = classify_task(task_type)
    pool = _MODEL_POOL[level]
    model = pool["model"]

    try:
        response = client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            tools=tools,
            tool_choice="auto",
        )

        message = response.choices[0].message
        content = message.content or ""

        # 优先：结构化 tool_calls（OpenAI 原生格式）
        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": args,
                })

        # 降级：从文本内容中解析工具调用（DeepSeek 兼容）
        if not tool_calls and content:
            tool_calls = _parse_tool_calls_from_text(content)
            # 包含 DSML 工具调用标签的文本是 LLM 的内部推理，
            # 不是最终回复 — 清空 content，最终问题由 continue_with_tool_results 生成
            content = "" if tool_calls else _strip_dsml_tool_text(content)

        return content.strip() if content else "", tool_calls

    except Exception as e:
        logger.error(f"LLM Tool Calling 调用失败: {e}")
        return None, []


def continue_with_tool_results(
    system_prompt: str,
    messages: list[dict],
    tool_results: list[dict[str, str]],
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> str | None:
    """将工具执行结果返回给 LLM，获取最终回复。

    Args:
        system_prompt: 系统提示
        messages: 完整对话历史（包含 assistant 的 tool_calls 消息）
        tool_results: 工具执行结果 [{"tool_call_id": ..., "content": ...}]
        temperature: 温度
        max_tokens: 最大 token

    Returns:
        LLM 最终文本回复
    """
    client = _get_client()
    if client is None:
        return None

    try:
        # 构建消息：system + 历史 + tool results
        full_messages = [{"role": "system", "content": system_prompt}]
        full_messages.extend(messages)
        for tr in tool_results:
            full_messages.append({
                "role": "tool",
                "tool_call_id": tr["tool_call_id"],
                "content": tr["content"],
            })

        response = client.chat.completions.create(
            model=settings.LLM_MODEL,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=full_messages,
        )

        return _clean_llm_text_response(response.choices[0].message.content)
    except Exception as e:
        logger.error(f"LLM Tool 结果处理失败: {e}")
        return None
