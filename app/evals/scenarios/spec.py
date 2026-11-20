from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ActionName = Literal[
    "BOOK", "RESCHEDULE", "CANCEL", "CHECK_AVAILABILITY", "FAQ", "CONFIRM",
    "ESCALATE", "LOOKUP_BOOKING", "CLARIFY",
]
WriteName = Literal["create", "confirm", "cancel", "reschedule"]
CoverageLayer = Literal["deterministic", "model", "database"]


class StrictModel(BaseModel):
    """Reject undeclared scenario fields so typos cannot silently weaken checks."""

    model_config = ConfigDict(extra="forbid")


class UserInput(StrictModel):
    """Describe exactly one text message, recorded clip, or synthesized utterance."""

    modality: Literal["text", "audio"]
    text: str | None = None
    source: Literal["recorded", "synthesize"] | None = None
    path: str | None = None
    speech_text: str | None = None
    reference_transcript: str | None = None

    @model_validator(mode="after")
    def validate_input_source(self) -> UserInput:
        """Require fields that match the modality so a turn has one clear source."""
        if self.modality == "text":
            if not self.text or not self.text.strip():
                raise ValueError("text inputs require nonblank text")
            if any((self.source, self.path, self.speech_text, self.reference_transcript)):
                raise ValueError("text inputs cannot declare audio fields")
        elif self.source == "recorded":
            if (
                not self.path
                or not self.path.strip()
                or not self.reference_transcript
                or not self.reference_transcript.strip()
            ):
                raise ValueError("recorded audio requires a path and reference transcript")
            if self.text or self.speech_text:
                raise ValueError("recorded audio cannot include synthesis text")
        elif self.source == "synthesize":
            if (
                not self.speech_text
                or not self.speech_text.strip()
                or not self.reference_transcript
                or not self.reference_transcript.strip()
            ):
                raise ValueError("synthesized audio requires speech and reference text")
            if self.path or self.text:
                raise ValueError("synthesized audio cannot include a fixture path")
        else:
            raise ValueError("audio inputs require source: recorded or synthesize")
        return self


class BookingMatch(StrictModel):
    """Point a booking assertion to a numbered offer from an earlier turn."""

    turn_id: str
    position: int = Field(ge=1)


class BookingExpectation(StrictModel):
    """State the expected saved booking status and optional offer match."""

    status: str | None = None
    matches_offer: BookingMatch | None = None


class LayaExpectation(StrictModel):
    """Declare the typed Laya action fields that a turn should propose."""

    next_action: ActionName
    confidence: float | None = Field(default=None, ge=0, le=1)
    needs_human: bool | None = None


class TurnAssertions(StrictModel):
    """Collect model, reply, offer, and per-turn scheduling write expectations."""

    gemma: dict[str, Any] = Field(default_factory=dict)
    laya: LayaExpectation | None = None
    intent: str | None = None
    offered_slot_count: int | None = Field(default=None, ge=0)
    writes: dict[WriteName, int]
    booking: BookingExpectation | None = None

    @field_validator("writes")
    @classmethod
    def require_nonnegative_writes(cls, value: dict[WriteName, int]) -> dict[WriteName, int]:
        """Reject negative counts because write assertions represent observed effects."""
        if any(count < 0 for count in value.values()):
            raise ValueError("write expectations cannot be negative")
        return value


class ConversationStep(StrictModel):
    """Pair one executable user message with its reference reply and checks."""

    id: str
    user: UserInput
    assistant_example: str | None = None
    assertions: TurnAssertions


class ScenarioSetup(StrictModel):
    """Provide the synthetic business, calendar, customer, and locale defaults."""

    preset: Literal["two_massage_slots"] = "two_massage_slots"
    verified_customer: bool = True
    existing_booking: bool = False
    today: str
    business_timezone: str = "UTC"
    locale_hint: str | None = None
    service_name: str = "Massage"

    @model_validator(mode="after")
    def validate_date_and_timezone(self) -> ScenarioSetup:
        """Catch invalid calendar or timezone setup before a worker starts."""
        try:
            date.fromisoformat(self.today)
            ZoneInfo(self.business_timezone)
        except (ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError("setup requires an ISO date and valid business timezone") from error
        return self


class JudgeCriterion(StrictModel):
    """Describe one judge decision and the appointment facts it may compare."""

    id: str
    turn_id: str
    expected: str
    facts_from: Literal["selected_offer", "saved_booking"] | None = None


class JudgeSpec(StrictModel):
    """Keep the scenario-specific judge prompt and its named criteria together."""

    prompt: str = Field(min_length=1)
    criteria: list[JudgeCriterion] = Field(min_length=1)


class FinalAssertions(StrictModel):
    """Define expected whole-conversation effects and final booking state."""

    writes: dict[WriteName, int]
    booking: BookingExpectation | None = None
    existing_booking_status: str | None = None

    @field_validator("writes")
    @classmethod
    def require_nonnegative_writes(cls, value: dict[WriteName, int]) -> dict[WriteName, int]:
        """Reject negative totals because final assertions count actual writes."""
        if any(count < 0 for count in value.values()):
            raise ValueError("write expectations cannot be negative")
        return value


class Scenario(StrictModel):
    """Specify one behavior, its ordered dialogue, rubric, and deterministic checks."""

    id: str
    spec_id: str
    category: str
    language: Literal["en", "pt"]
    split: Literal["train", "dev", "test"] = "test"
    group_id: str
    provenance: Literal["synthetic"] = "synthetic"
    declared_coverage: list[CoverageLayer] = Field(
        default_factory=lambda: ["deterministic", "model", "database"]
    )
    setup: ScenarioSetup
    example_conversation: list[ConversationStep] = Field(min_length=1)
    judge: JudgeSpec
    final_assertions: FinalAssertions

    @model_validator(mode="after")
    def validate_references(self) -> Scenario:
        """Resolve IDs up front so assertions cannot point at missing or later turns."""
        allowed_gemma_fields = {
            "message", "service_name", "professional_name", "local_date",
            "local_time", "slot_index",
        }
        for step in self.example_conversation:
            unknown = set(step.assertions.gemma) - allowed_gemma_fields
            if unknown:
                raise ValueError(f"unknown Gemma assertion fields: {sorted(unknown)}")
        turn_ids = [step.id for step in self.example_conversation]
        if len(turn_ids) != len(set(turn_ids)):
            raise ValueError(f"scenario {self.id} has duplicate conversation step IDs")
        criterion_ids = [criterion.id for criterion in self.judge.criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError(f"scenario {self.id} has duplicate judge criterion IDs")
        positions = {turn_id: index for index, turn_id in enumerate(turn_ids)}
        for criterion in self.judge.criteria:
            if criterion.turn_id not in positions:
                raise ValueError(f"criterion {criterion.id} references an unknown turn")
        for index, step in enumerate(self.example_conversation):
            match = step.assertions.booking.matches_offer if step.assertions.booking else None
            if match and positions.get(match.turn_id, index) >= index:
                raise ValueError("booking assertions must reference an earlier offer turn")
        match = self.final_assertions.booking.matches_offer if self.final_assertions.booking else None
        if match and match.turn_id not in positions:
            raise ValueError("final booking assertion references an unknown offer turn")
        return self


class ScenarioDataset(StrictModel):
    """Bundle scenarios under one versioned dataset ID and description."""

    schema_version: Literal[1]
    dataset_id: str
    description: str = ""
    scenarios: list[Scenario] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_ids(self) -> ScenarioDataset:
        """Require unique case IDs because reports and replay address cases by ID."""
        ids = [scenario.id for scenario in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError(f"dataset {self.dataset_id} has duplicate scenario IDs")
        return self


class _UniqueKeyLoader(yaml.SafeLoader):
    """Use YAML's safe constructors while replacing silent duplicate-key overrides."""

    pass


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: Any, deep: bool = False):
    """Reject duplicate YAML keys because silent overrides make specs misleading."""
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_dataset(path: str | Path, *, repository_root: Path) -> ScenarioDataset:
    """Load a strict YAML behavior spec and reject missing or out-of-repo audio.

    The scenario YAML is both the executable conversation and its human
    reference, so path checks happen before any model or speech provider runs.
    """
    source = Path(path).resolve()
    try:
        raw = yaml.load(source.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except yaml.YAMLError as error:
        raise ValueError(f"invalid scenario YAML in {source}: {error}") from error
    dataset = ScenarioDataset.model_validate(raw)
    root = repository_root.resolve()
    for scenario in dataset.scenarios:
        for step in scenario.example_conversation:
            if step.user.source == "recorded":
                asset = (root / step.user.path).resolve()
                if not asset.is_relative_to(root) or not asset.is_file():
                    raise ValueError(f"missing or unsafe audio file in {scenario.id}")
    return dataset


def validate_dataset_groups(datasets: list[ScenarioDataset]) -> None:
    """Prevent related scenarios from leaking across training and held-out splits."""
    groups: dict[str, str] = {}
    ids: set[tuple[str, str]] = set()
    for dataset in datasets:
        for scenario in dataset.scenarios:
            key = (dataset.dataset_id, scenario.id)
            if key in ids:
                raise ValueError(f"duplicate scenario ID across datasets: {key}")
            ids.add(key)
            group = scenario.group_id
            if group in groups and groups[group] != scenario.split:
                raise ValueError(f"group {group} crosses dataset splits")
            groups[group] = scenario.split
