"""Snapshot format and command transition rules, separate from concurrency version."""

SCHEMA_VERSION = 1
ALLOWED = {"ready": {"start", "end"}, "active": {"answer", "skip", "rephrase", "end"},
           "reporting": set(), "completed": set(), "failed": set()}


def read_snapshot(snapshot: dict) -> dict:
    version = snapshot.get("schema_version", 1)
    if type(version) is not int or version != SCHEMA_VERSION:
        raise ValueError("不支持的面试快照版本，请升级服务后读取")
    return {**snapshot, "schema_version": SCHEMA_VERSION}


def validate_command(status: str, snapshot: dict, command: dict) -> None:
    read_snapshot(snapshot)
    action = command["type"]
    if action not in ALLOWED.get(status, set()):
        raise ValueError("当前面试状态不允许此命令")
    if action in {"answer", "skip", "rephrase"}:
        question = snapshot.get("current_question")
        if not question or question["id"] != command.get("expected_question_id"):
            raise ValueError("问题已变化，请刷新后操作")
