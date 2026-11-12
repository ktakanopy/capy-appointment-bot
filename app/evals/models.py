from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.graph.contracts import WorkflowDraft


class EvaluationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    utterance: str = ""
    audio_path: str | None = None
    reference_transcript: str | None = None
    expected: dict[str, Any] = Field(default_factory=dict)
    expected_action: str | None = None
    expected_intent: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    draft: WorkflowDraft = Field(default_factory=WorkflowDraft)


class EvaluationScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str
    title: str
    utterance: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    expected: dict[str, Any] = Field(default_factory=dict)
    category: str = "scheduling"
    language: Literal["en", "pt"] = "en"
    split: Literal["train", "dev", "test"] = "test"
    group_id: str = ""
    provenance: str = "synthetic"
    turns: list[EvaluationTurn] = Field(default_factory=list)
    expected_writes: dict[str, int] = Field(default_factory=dict)
    existing_booking: bool = False
    authenticated: bool = True


class EvaluationResult(BaseModel):
    scenario_id: str
    title: str
    status: Literal["pass", "fail", "error"]
    expected: dict[str, Any]
    observed: dict[str, Any]
    mismatches: list[str] = Field(default_factory=list)
