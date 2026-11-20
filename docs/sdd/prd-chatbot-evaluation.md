# PRD — CAPY chatbot evaluation

**Status** · The evaluation framework is implemented. Use the [evaluation framework guide](../evaluation-framework.md) for current commands, provider defaults, and report details.

## Intent checkpoint

- **Intent.** Author CAPY conversations in YAML and use those exact user turns to exercise the chatbot, one message at a time. Evaluate both the reply and the actual effect on scheduling.
- **Why.** A reply can sound correct while the chatbot selects the wrong slot or changes the wrong booking. Repeated runs should make those failures and their frequency visible before deployment.
- **Outcome.** A contributor can write a scenario, read its Markdown version, run and replay it, and open an HTML report backed by timestamped JSON.
- **Scope.** Text and audio inputs in English and Brazilian Portuguese, scripted multi-turn conversations, a small golden dataset, a challenge dataset, deterministic assertions, a case-specific LLM judge prompt, concurrent replay, and reporting. Audio generation exposes the backend, model, and voice. Include the application change needed to ask for explicit consent before creating an appointment.
- **Confirmed example contract.** The example conversation is the input script. Each user turn supplies exactly one input. CAPY's observed reply becomes the assistant message in the running history. An example assistant reply is reference material for people reading the spec.
- **Constraints.** Exercise the existing CAPY workflow and preserve its Gemma/Laya contracts. Use isolated scheduling data for tests. Keep secrets, customer data, and raw recordings out of reports and judge requests. Use the checked-in test YAML as the source for the generated Markdown.

## Behavior specification

Each scenario has a stable scenario ID and a behavior ID such as `CAPY-BOOK-001`. It includes setup, an ordered `example_conversation`, a judge prompt and named criteria, and deterministic expectations. The schema calls the reference reply `assistant_example` so the author can tell at a glance which text gets sent to CAPY.

The [executable golden dataset](../../evals/scenarios/golden.yaml) defines the English and Brazilian Portuguese booking journeys. The user asks to book a massage tomorrow, sends `"1"`, then answers the confirmation question. The first two turns must have zero booking writes. The final turn must create exactly one booking for the first slot CAPY offered. Use [render-spec](../evaluation-framework.md#author-a-scenario) to generate a Markdown view of those same examples and assertions.

An audio turn uses the same conversation structure. Its user entry identifies an existing audio fixture or specifies text to synthesize before running the test. The runner passes the prepared recording to CAPY. A reference transcript supports reporting and review. The operator can select a voice backend, model, and voice, with language-appropriate defaults. The receipt and report record which settings produced each clip.

```yaml
user:
  modality: audio
  source: synthesize
  reference_transcript: "Sim, pode agendar."
```

## Requirements

| ID | Requirement | Acceptance |
| --- | --- | --- |
| FR-01 | Define the behavior and example conversation in YAML. | Validation reports a precise error for invalid turns, duplicated IDs, unknown fields, or unsafe fixture paths. Markdown renders the same setup, user inputs, sample replies, rubric, and assertions. |
| FR-02 | Play the example as a conversation. | CAPY receives one user turn per invocation. In the second turn, its state contains the first user message and CAPY's first actual reply. A test with distinctive example-reply text proves that reference text stays out of runtime history. |
| FR-03 | Run text, stored audio, and pre-generated speech with selectable voices. | Mixed scenarios switch modality at the declared turn. A repeated run uses the same recorded bytes and records their checksum, backend, model, voice, and language. Unsupported combinations fail during audio preparation. |
| FR-04 | Check model outputs and scheduling effects deterministically. | Validate Gemma's `SchedulingInterpretation` before merging and Laya's `DecisionOutput` before applying graph policy. Compare declared fields and expected routing to those outputs. Verify exact write counts per turn and per case. Compare a selected booking with the slot offered earlier. |
| FR-05 | Judge conversational behavior against named criteria. | The YAML contains the case prompt and criteria. The judge evaluates the observed transcript and returns a validated verdict with evidence for each criterion. Missing or malformed verdicts appear as judge errors. |
| FR-06 | Supply golden and challenge datasets. | The golden set demonstrates core scheduling journeys. The challenge set includes intent changes, ambiguous choices, and more difficult text or speech. Cases can run by dataset, category, or ID. |
| FR-07 | Repeat and replay isolated runs concurrently. | A configurable limit bounds concurrent cases and repetitions. Each has its own conversation, checkpoint store, and booking state. Within a case, a turn waits for the previous reply. Replay selects individual attempts by status or ID and writes a new timestamped receipt with the original run and attempt IDs. |
| FR-08 | Explain failures in an HTML report. | The report shows results by dataset, category, language, modality, and voice. Each attempt shows selected voice settings, expected and actual Gemma and Laya outputs, reply and writes, judge evidence, and provider errors. |
| FR-09 | Show which evaluation layers cover a behavior. | Report declared coverage and observed evidence separately for deterministic checks, model simulation, and real database verification. Mark a layer as not run when the report has no matching evidence. Production feedback remains a later layer. |
| FR-10 | Ask for explicit confirmation before creating a booking. | Selecting a valid slot creates only a pending choice and a question. A later clear affirmative can create that booking once. A rejection, ambiguous answer, stale selection, or revised request makes zero booking mutations until the customer provides the detail or approval needed to proceed. A model's incorrect action proposal cannot alter another appointment while resolving the pending offer. |
| FR-11 | Return booking details in English and Brazilian Portuguese after creation. | The question matches the facts of the selected offer. The success reply matches the persisted service, date, local time and time zone, and professional when assigned. Project these facts into the judge rubric and verify the saved booking independently. A fluent reply with the wrong time or professional fails. |
| FR-12 | Document how to use and extend the framework. | A contributor can follow the guide from setup to their first offline report, add a YAML scenario, choose a voice and judge for a live run, replay a failure, and interpret the result at each evaluation layer. |

## Implemented booking behavior

Selecting a slot now records a pending choice and asks the customer to approve its exact service, professional, date, local time and timezone. That turn makes no booking write. A later clear approval, matching Laya `BOOK` proposal, and the same still-offered slot are all required before the transaction creates the appointment. The success reply is built from the saved booking. [Consent route](../../app/graph/steps.py#L341) · [Create preparation](../../app/graph/steps.py#L853) · [PostgreSQL journey](../../tests/integration/test_workflow_booking_journey.py)

The pending choice is specific to the offered slot and conversation. The question gives the customer enough detail to confirm that exact appointment. Only a checked affirmative response in a later turn allows creation. Resolve consent and withdrawal before allowing Laya's proposed action to change the draft or an existing appointment. A refusal such as “Não, deixa pra lá” clears the pending request and makes zero writes even if Laya proposes `CANCEL`. An unclear reply repeats the yes/no question and keeps other booking actions blocked until the customer resolves the pending choice. A revised date or time invalidates the old approval prompt and requires a fresh search. The booking transaction rechecks availability at creation time.

## Dataset scope

**Implemented golden set.** Covers the select–ask–confirm–create flow in English and Brazilian Portuguese. The selection turn expects zero booking writes. The separate approval turn expects one create and checks that the saved booking matches the earlier offer.

**Implemented challenge set.** Covers declining a pending offer while preserving an existing booking, changing the time before approval, canceling after a booking was created, and Portuguese audio approval. These cases keep the refusal and a real cancellation as distinct behaviors.

**Follow-up cases.** Add missing-date and existing-booking lookup/confirm/reschedule flows, ambiguous booking references, expired or stale offers against PostgreSQL, and a correction later in a recording. The current scenario fixture is synthetic and does not simulate transaction races or stale database availability.

## Applied implementation decisions

- The runner first serves fast regression checks against the actual graph with deterministic providers and isolated synthetic scheduling services. Real persistence stays in PostgreSQL integration tests; the scenario runner does not write to the database.
- Audio can use checked-in recorded fixtures, the local Qwen3-TTS script, or explicitly enabled OpenRouter synthesis. Current local defaults are `documentary` (English) and `storyteller` (Portuguese); `playful-host` is also available. Portuguese OpenRouter synthesis requires an explicit compatible model and voice.
- Remote speech generation and remote judging require `--allow-remote`, and scenario provenance is limited to `synthetic`.
- Runs repeat three times by default. Judge failures are advisory unless `--judge-gate` is set.
- The explicit-approval requirement applies to creating a new booking. Existing booking confirmation, cancellation and rescheduling retain their current workflows.

The [evaluation framework guide](../evaluation-framework.md) is the operational reference for running and extending the framework.

## Ownership and rollout

Dataset authors own scenarios, reference replies, and judge criteria. Evaluation code owns playback, assertions, receipts, and report calculation. The conversation workflow owns the pending approval and bilingual messages. Existing application services own booking rules and committed details. Any real booking verification uses the disposable test database and its existing migrations. Model training, automatic ingestion of production conversations, and an always-on evaluation service are outside this implementation.
