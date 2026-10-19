from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EvaluationScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    title: str
    utterance: str
    context: dict[str, Any] = Field(default_factory=dict)
    expected: dict[str, Any]
    category: str


class EvaluationResult(BaseModel):
    scenario_id: str
    title: str
    status: Literal["pass", "fail", "error"]
    expected: dict[str, Any]
    observed: dict[str, Any]
    mismatches: list[str] = Field(default_factory=list)
