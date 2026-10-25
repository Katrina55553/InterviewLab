"""Controllable, isolated protocol fixture. Never a fallback for real AI."""

import asyncio
import json

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
control = {"mode": "normal", "delay": 0, "calls": 0, "inflight": 0, "peak": 0}
hang_release = asyncio.Event()


@app.get("/control")
async def read_control():
    return control


@app.post("/control")
async def set_control(body: dict):
    assert set(body) <= {"mode", "delay", "reset"}
    if body.get("reset"):
        control["calls"] = control["peak"] = 0
    control.update({k: v for k, v in body.items() if k in {"mode", "delay"}})
    if control["mode"] == "hang":
        hang_release.clear()
    else:
        hang_release.set()
    return control


@app.post("/v1/chat/completions")
async def completion(body: dict):
    control["calls"] += 1
    control["inflight"] += 1
    control["peak"] = max(control["peak"], control["inflight"])
    try:
        await asyncio.sleep(float(control["delay"]))
        if control["mode"] == "error":
            return JSONResponse({"error": {"message": "controlled failure"}}, status_code=503)
        if control["mode"] == "hang":
            await hang_release.wait()
        prompt = json.dumps(body["messages"], ensure_ascii=False)
        system = body["messages"][0]["content"]
        if "复盘" in system or "已保存" in system:
            result = {"summary": "Test summary", "suggestions": ["Test suggestion"]}
        elif '"resume_score"' in prompt.replace('\\"', '"'):
            result = {"parsed": {"summary": "Test fixture", "skills": ["Python"]}, "resume_score": 76, "suggestions": ["Test evidence"]}
        elif '"parsed"' in prompt.replace('\\"', '"'):
            result = {"parsed": {"summary": "Test fixture", "skills": ["Python"]}}
        elif '"questions"' in prompt.replace('\\"', '"'):
            result = {"questions": [{"text": f"Test question {i}", "category": "test"} for i in range(5)]}
        elif '"dimensions"' in prompt.replace('\\"', '"'):
            result = {"score": 83, "dimensions": dict.fromkeys(["professional", "project", "analysis", "logic", "completeness"], 83),
                      "feedback": "Test evidence", "missing_points": [], "follow_up_needed": False}
        elif '"question"' in prompt.replace('\\"', '"'):
            result = {"question": "Test followup"}
        else:
            result = {"summary": "Test summary", "suggestions": ["Test suggestion"]}
        content = json.dumps(result)
        if body.get("stream"):
            async def stream():
                for part in ("Test ", "stream"):
                    yield "data: " + json.dumps({"id": "test", "object": "chat.completion.chunk", "created": 0,
                        "model": "test", "choices": [{"index": 0, "delta": {"content": part}, "finish_reason": None}]}) + "\n\n"
                yield 'data: [DONE]\n\n'
            return StreamingResponse(stream(), media_type="text/event-stream")
        return {"id": "test", "object": "chat.completion", "created": 0, "model": "test", "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80}}
    finally:
        control["inflight"] -= 1
