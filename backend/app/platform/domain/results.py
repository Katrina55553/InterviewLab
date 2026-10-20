"""Validate known AI results before they can enter the reusable result ledger."""

from typing import Annotated

from pydantic import BaseModel, Field, ValidationError

from app.platform.contracts import PlatformError

DIMENSIONS = {"professional", "project", "analysis", "logic", "completeness"}


class Evaluation(BaseModel):
    score: int = Field(ge=0, le=100, strict=True)
    dimensions: dict[str, Annotated[int, Field(ge=0, le=100, strict=True)]]
    feedback: str = Field(min_length=1)
    missing_points: list[str] = Field(default_factory=list)
    follow_up_needed: bool = False


def validate_result(scene, value):
    valid = True
    if scene == "evaluate":
        try:
            value = Evaluation.model_validate(value).model_dump()
            valid = set(value["dimensions"]) == DIMENSIONS
        except ValidationError:
            valid = False
    elif scene == "resume":
        valid = (isinstance(value.get("parsed"), dict) and type(value.get("resume_score")) is int
                 and 0 <= value["resume_score"] <= 100 and isinstance(value.get("suggestions"), list)
                 and all(isinstance(x, str) for x in value["suggestions"]))
    elif scene == "questions":
        rows = value.get("questions")
        valid = isinstance(rows, list) and len(rows) == 5 and all(
            isinstance(row, dict) and isinstance(row.get("text"), str) and row["text"].strip() for row in rows)
    elif scene == "followup":
        valid = isinstance(value.get("question"), str) and bool(value["question"].strip())
    elif scene == "report":
        valid = (isinstance(value.get("summary"), str) and isinstance(value.get("suggestions"), list)
                 and all(isinstance(x, str) for x in value["suggestions"]))
    if not valid:
        raise PlatformError("AI_INVALID_RESPONSE", "模型返回结果不符合本节点契约，当前状态已保留", outcome="known")
    return value
