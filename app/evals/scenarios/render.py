from __future__ import annotations

from app.evals.scenarios.spec import ScenarioDataset


def render_markdown(dataset: ScenarioDataset) -> str:
    """Render a validated executable dataset as readable conversation specs."""
    lines = [f"# Evaluation dataset: {dataset.dataset_id}", ""]
    if dataset.description:
        lines.extend([dataset.description, ""])
    for scenario in dataset.scenarios:
        lines.extend(
            [
                f"## {scenario.id}",
                "",
                f"- Behavior: `{scenario.spec_id}`",
                f"- Category: {scenario.category}",
                f"- Language: `{scenario.language}`",
                f"- Split: `{scenario.split}`",
                f"- Group: `{scenario.group_id}`",
                f"- Provenance: {scenario.provenance}",
                f"- Declared evaluation layers: `{scenario.declared_coverage}`",
                "",
                "### Setup",
                "",
                f"- Preset: `{scenario.setup.preset}`",
                f"- Business date: `{scenario.setup.today}`",
                f"- Customer verified: `{scenario.setup.verified_customer}`",
                f"- Existing booking: `{scenario.setup.existing_booking}`",
                f"- Locale hint: `{scenario.setup.locale_hint or 'not set'}`",
                f"- Service: {scenario.setup.service_name}",
                "",
                "### Example conversation and checks",
                "",
            ]
        )
        for step in scenario.example_conversation:
            user = step.user
            if user.modality == "text":
                input_text = user.text or ""
            elif user.source == "recorded":
                input_text = f"Recorded audio `{user.path}`; reference transcript: {user.reference_transcript}"
            else:
                input_text = f"Synthesize audio: {user.speech_text}"
            lines.extend(
                [
                    f"#### {step.id}",
                    "",
                    f"**Customer ({user.modality}):** {input_text}",
                    f"**Example reply (reference only):** {step.assistant_example or 'Not provided'}",
                    "",
                    "Expected checks:",
                    "",
                    f"- Gemma: `{step.assertions.gemma}`",
                    f"- Laya: `{step.assertions.laya.model_dump(exclude_none=True) if step.assertions.laya else None}`",
                    f"- Reply intent: `{step.assertions.intent}`",
                    f"- Offered slots: `{step.assertions.offered_slot_count}`",
                    f"- Writes this turn: `{step.assertions.writes}`",
                    f"- Booking: `{step.assertions.booking.model_dump(exclude_none=True) if step.assertions.booking else None}`",
                    "",
                ]
            )
        lines.extend(["### Judge", "", scenario.judge.prompt.strip(), ""])
        for criterion in scenario.judge.criteria:
            lines.append(
                f"- `{criterion.id}` on `{criterion.turn_id}`: {criterion.expected}"
            )
        lines.extend(
            [
                "",
                "### Final deterministic checks",
                "",
                f"- Writes: `{scenario.final_assertions.writes}`",
                f"- Booking: `{scenario.final_assertions.booking.model_dump(exclude_none=True) if scenario.final_assertions.booking else None}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
