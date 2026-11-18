# Refactor plan — one graph node for text and audio

## Goal

A customer can type “Do you have a massage appointment tomorrow?” or say it in a voice note. Both turns should enter the same graph node, `understand_input`, and produce the same kind of scheduling interpretation. Telegram handles delivery and file downloads. The graph owns understanding from that point onward.

Here, “what the customer means” is a plain-language message such as “The customer wants available massage times tomorrow.” Gemma produces that message and extracts facts such as the service and date. Python merges the facts into the conversation draft. Laya receives exactly `message` and `context` and chooses the next action, such as `CHECK_AVAILABILITY`. Python checks that the action is allowed before executing it. This division keeps Gemma and Laya independently trainable and testable. [Model contracts](../model-contracts.md)

## Where to start reading

| Current code | Why it matters for the change |
| --- | --- |
| [TelegramWorker._message_input and _run_turn](../../app/telegram_worker.py) | The worker currently downloads and transcribes recordings, sanitizes the resulting text using the customer's display name, then calls the workflow. |
| [LangGraphWorkflow.handle_turn](../../app/graph/workflow.py) | The workflow seeds both `message` and user history before invoking the graph. Redis can save this initial input before the first node runs. |
| [build_graph](../../app/graph/builder.py) and [interpret, merge_draft and route_turn](../../app/graph/steps.py) | The graph already owns text interpretation, draft updates and routing. Replace the existing interpretation node here. |
| [create_runtime](../../app/runtime.py) and [GemmaUnderstanding.transcribe_recording](../../app/llm/gemma.py) | These configure and enforce the existing audio limits, chunk handling and complete-recording requirement. |
| [_evaluate_case and _read_turn_input](../../app/evals/runner.py) | The workflow evaluation currently transcribes before invoking the graph too. Moving only the Telegram path would leave the audio evaluation testing a different flow. |

## Implementation order

1. **Give each graph invocation its own input.** Keep existing text calls to `handle_turn(turn, actions)` working. Allow a recording through a keyword-only argument, with `TurnContext.message` optional for recording turns. Validate exactly one source of content before invoking the graph. Bind that source and any display name needed for redaction for the lifetime of the call, using the existing per-invocation binding pattern. Release the binding in `finally`, including when a provider raises. Keep recordings, raw text and the display name out of graph state, checkpoint configuration and trace attributes. A resumed conversation must get its current input from the new invocation, even when the previous checkpoint contains a `message`. [TurnContext](../../app/graph/contracts.py) · [Invocation binding](../../app/graph/context.py) · [Checkpoint saver](../../app/graph/redis_checkpoint.py)

2. **Replace `interpret` with `understand_input`.** Wire the graph as `load_context → understand_input → merge_draft → decide`. For typed input, sanitize the text and call the existing interpreter. For a recording, call `transcribe_recording` with the active service and professional names and the same business-local date used by the interpreter. Once all chunks succeed, sanitize the full transcript and call the same interpreter once. Supply previous conversation history and offered-slot context to both paths. Inject the configured transcriber, chunker and timeout at graph construction so the graph can also run with test doubles. Preserve the separate per-chunk timeout, decode limits and overall deadline configured today. [Graph builder](../../app/graph/builder.py) · [Graph steps](../../app/graph/steps.py) · [Runtime](../../app/runtime.py)

3. **Make the node responsible for the user history entry.** Remove the early append in `handle_turn` and the matching-text history shortcut in `interpret`. On success, the node returns the sanitized customer text as `message`, one user history entry with that text, and the sanitized `interpretation`. The `interpretation.message` field is Gemma’s rewrite for Laya. Keeping these two meanings explicit lets the next turn see what the customer actually said. Continue using the existing history reducer and assistant reply append. Completed checkpoints from earlier versions should still supply the draft, history and offered slots. [Workflow](../../app/graph/workflow.py) · [State and history reducer](../../app/graph/state.py)

4. **Reduce the Telegram worker to channel responsibilities.** Keep customer resolution, `/start`, contact cards, unsupported-message replies and the bounded download of Telegram `voice` and `audio` files. Pass the downloaded recording or typed text into the workflow. Remove the worker’s transcription call and its transcription-context helper. Reuse the per-turn catalog cache for the business-local date and graph context. Preserve the update-based idempotency key, existing customer-visible error handling and publication of a private checkpoint only after a successful graph turn. Add short docstrings to changed entry points explaining why the channel owns the download and why the node waits for the whole recording. [Telegram worker](../../app/telegram_worker.py) · [Turn processing](../../app/telegram_service.py) · [Catalog cache](../../app/customer_scheduling_actions.py)

5. **Run workflow evaluations through the production input path.** Pass fixture recording bytes to `handle_turn` in workflow mode. Wrap the evaluation transcriber to measure elapsed time and score its single completed transcript against the synthetic reference. This preserves word-error metrics without a second transcription or a production checkpoint field. Keep extraction-only evaluation independently callable, routing-only evaluation on its labelled interpreted message, and deterministic text evaluations runnable offline. Update the graph diagram, Telegram flow and evaluation guide to show where the recording becomes text and where Laya first sees the request. [Evaluation runner](../../app/evals/runner.py) · [Graph diagram](../graph.md) · [Architecture](../architecture.md) · [Evaluation guide](../evaluation.md)

## Acceptance tests

| Scenario | What to verify |
| --- | --- |
| Same request typed and spoken | In a graph test with fixed provider responses, the text path makes zero transcription calls. The recording is transcribed once as a whole, including any internal chunk calls. Both paths interpret once and send the same two-field request to Laya. Compare booking facts, chosen action and write count. |
| A customer corrects themselves late in a recording | Include a final chunk that changes the requested date. Only the complete transcript reaches interpretation and routing. A timeout, invalid recording, empty transcript or failed later chunk yields zero routing calls and zero booking writes. The previous published checkpoint still opens. |
| A voice note follows an offer | Start from a completed text turn with offered slots. Say “the second one.” Verify that the interpreter sees the prior offer, the user message appears once, and the selected slot belongs to that offer. Repeat with a checkpoint in the previous format and with two consecutive identical customer messages. |
| Two customers speak at the same time | Give overlapping graph calls distinct audio markers. Verify each transcriber receives only its own recording, including when one call fails. Inspect Redis checkpoints **and pending writes**, logs, traces and errors for audio markers, encoded audio, display names, phone numbers and legacy booking tokens. Use synthetic values covered by the existing redactor. |
| Telegram and evaluations | Exercise text, voice note, uploaded audio, over-limit download, contact, and `/start`. Verify one catalog fetch per scheduling turn and the existing duplicate-update booking protection. Confirm workflow audio fixtures exercise the graph node, extraction still reports audio metrics, and labelled routing invokes neither transcription nor interpretation. |

Use the existing [Telegram turn tests](../../tests/unit/test_telegram_turn.py), [audio pipeline tests](../../tests/unit/test_audio_pipeline.py), [graph workflow tests](../../tests/unit/test_graph_workflow.py) and [Redis integration tests](../../tests/integration/test_redis_conversation.py). Run the unit suite and offline workflow corpus first, then the database-backed checks in [test Compose](../../docker-compose.test.yml). With Gemma and Laya available, also run the English and Portuguese audio fixtures and record the served revisions.

```sh
uv run --extra dev pytest -q tests/unit
uv run python -m app.evals.runner --understanding deterministic --routing deterministic
docker compose -f docker-compose.test.yml run --build --rm checks
uv run python -m app.evals.runner --dataset evals/data/smoke-audio.jsonl --layer workflow --understanding gemma --routing laya --gemma-revision "$GEMMA_REVISION" --laya-revision "$LAYA_REVISION"
```
