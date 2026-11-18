"""Serve CAPY's Gemma and Laya contracts on an Apple Silicon Mac."""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import platform
import wave
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.channels.audio_chunks import MAX_CHUNK_FRAMES, SAMPLE_RATE
from app.graph.contracts import DecisionOutput, WorkflowDraft
from app.llm.schemas import SchedulingInterpretation
from app.observability import debug_event

GEMMA_REPO = "litert-community/gemma-4-E2B-it-litert-lm"
GEMMA_REVISION = "2d74ac41a33d7da82f39f7d5720a04c68514a103"
GEMMA_FILE = "gemma-4-E2B-it.litertlm"
LAYA_REPO = "aac6fef/laya-multilingual-mlx"
LAYA_REVISION = "f2b4faf51023039425946074e2cf1361d2db11d5"

REWRITE_PROMPT = """Copyedit the latest customer request in its original language.
Keep the customer's perspective, meaning, question, dates, times, negation,
corrections, and uncertainty. Copy already clear requests. Use the context
only for references. Never choose the next action.

Return JSON with the edited message and facts from the latest request. Use null
for unknown or uncertain facts. Copy catalog names only if the customer named
them. Set local_date for an explicit date (YYYY-MM-DD) or an unambiguous relative
date using context.today. Set local_time for an unambiguous time (HH:MM).
When context.has_slots is true and the customer replies with a number, set
slot_index to that number and keep the number in the message.

Examples of facts:
"Could I get a haircut tomorrow?" when today is 2027-02-03 gives local_date
"2027-02-04". "Remarcar para 2028-07-09" gives local_date "2028-07-09".
"3" with choices available gives slot_index 3. "Talvez eu cancele" expresses
uncertainty, which must stay in the Portuguese message."""
TRANSCRIBE_PROMPT = """Transcribe the speech verbatim in its original language.
Output only the spoken words, preserving names, numbers, corrections and negation."""

ACTION_CRITERIA = {
    "CHECK_AVAILABILITY": "Find times for a new appointment or provide missing details for that search.",
    "BOOK": "Pick an offered time or accept the pending time for a new or rescheduled appointment.",
    "RESCHEDULE": "Move an existing appointment to a different date or time.",
    "CANCEL": "Explicitly cancel an existing appointment, or choose which booking to cancel.",
    "CONFIRM": "Confirm an existing appointment.",
    "LOOKUP_BOOKING": "Find or view an existing appointment or ask when it is.",
    "FAQ": "Ask about prices, services, or business policies.",
    "CLARIFY": "The customer is undecided or their scheduling request is ambiguous.",
    "ESCALATE": "An unsupported request, such as an unrelated task or access to customer data.",
}
CANCEL_QUESTION = {
    "type": "noul",
    "instructions": (
        "Does the customer explicitly want to cancel an appointment, or select "
        "an appointment in an ongoing cancellation? Moving, rescheduling, "
        "confirming, and undecided requests are not cancellations."
    ),
}
INTERPRETATION_SCHEMA = SchedulingInterpretation.model_json_schema()
INTERPRETATION_SCHEMA["required"] = list(INTERPRETATION_SCHEMA["properties"])
logger = logging.getLogger(__name__)


class TextTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modality: Literal["text"]
    message: str = Field(min_length=1, max_length=64_000)
    context: dict


class AudioChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modality: Literal["audio"]
    audio_base64: str = Field(min_length=1, max_length=1_400_000)
    context: dict


class RoutingTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=64_000)
    context: WorkflowDraft


def decode_audio(encoded: str) -> bytes:
    audio = base64.b64decode(encoded, validate=True)
    with wave.open(io.BytesIO(audio)) as wav:
        if (
            wav.getnchannels() != 1
            or wav.getsampwidth() != 2
            or wav.getframerate() != SAMPLE_RATE
            or not 0 < wav.getnframes() <= MAX_CHUNK_FRAMES
            or len(wav.readframes(wav.getnframes())) != 2 * wav.getnframes()
        ):
            raise ValueError("Invalid audio format or duration")
    return audio


def route(laya, turn: RoutingTurn) -> DecisionOutput:
    draft = turn.context
    state = {
        "message": turn.message,
        "context": {
            "operation": draft.operation,
            "missing": draft.missing,
            "service": draft.service_name,
            "requested_date": draft.local_date,
            "offered_times": len(draft.offered_slot_ids),
            "pending_time": bool(draft.pending_slot_id),
            "possible_existing_bookings": len(draft.candidate_booking_ids),
        },
    }
    criteria = ACTION_CRITERIA.copy()
    if not (draft.offered_slot_ids or draft.pending_slot_id):
        criteria.pop("BOOK")
    answers = laya.predict(
        state,
        {
            "next_action": {
                "type": "choice",
                "instructions": "Which scheduling action fits the latest request and the current booking draft?",
                "criteria": criteria,
            },
            "explicit_cancellation": CANCEL_QUESTION,
        },
    )["answers"]
    choice = answers["next_action"]
    action = choice["choice"]
    if action not in criteria:
        raise ValueError("Invalid routing choice")
    confidence = choice["probabilities"][action]
    if action == "CANCEL":
        confidence = min(confidence, answers["explicit_cancellation"]["noul"])
    return DecisionOutput(next_action=action, confidence=confidence)


def create_app(gemma, laya, litert) -> FastAPI:
    app = FastAPI()
    interpretation_format = litert.ResponseFormat.json(INTERPRETATION_SCHEMA)
    constraint = litert.ConstrainedDecodingConfig(
        provider=litert.LiteRtLmConstraintProviderType.LL_GUIDANCE
    )

    def complete(content, prompt: str, *, structured: bool = False) -> str:
        with gemma.create_conversation(
            system_message=prompt,
            max_output_tokens=512,
            thinking_config=litert.ThinkingConfig(enable_thinking=False),
            sampler_config=litert.SamplerConfig(top_k=1, temperature=0),
            constrained_decoding_config=constraint if structured else None,
        ) as conversation:
            response = conversation.send_message(
                content, response_format=interpretation_format if structured else None
            )
        text = "".join(
            part["text"] for part in response["content"] if part["type"] == "text"
        ).strip()
        if not text:
            raise ValueError("Empty model response")
        return text

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error):
        return JSONResponse(status_code=422, content={"detail": "Invalid model request"})

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # Keep inference on the server thread with one native engine per model.
    @app.post("/understand")
    async def understand(
        turn: Annotated[TextTurn | AudioChunk, Field(discriminator="modality")],
    ):
        debug_event("model.server.understand.requested", modality=turn.modality)
        try:
            if isinstance(turn, AudioChunk):
                try:
                    audio = decode_audio(turn.audio_base64)
                except (ValueError, wave.Error, EOFError):
                    raise HTTPException(
                        422, "Expected a 16 kHz mono PCM WAV clip"
                    ) from None
                content = litert.Contents.of(
                    TRANSCRIBE_PROMPT, litert.Content.AudioBytes(audio)
                )
                return {"transcript": complete(content, "You transcribe speech.")}
            model_input = json.dumps(
                {"context": turn.context, "latest_customer_message": turn.message},
                ensure_ascii=False,
            )
            model_output = complete(model_input, REWRITE_PROMPT, structured=True)
            return SchedulingInterpretation.model_validate_json(model_output)
        except (ValueError, RuntimeError, KeyError, TypeError) as error:
            logger.warning("Gemma inference failed (%s)", type(error).__name__)
            raise HTTPException(502, "Gemma inference failed") from None

    @app.post("/decide")
    async def decide(turn: RoutingTurn):
        debug_event("model.server.decide.requested")
        try:
            return route(laya, turn)
        except (ValueError, RuntimeError, KeyError, TypeError) as error:
            logger.warning("Laya inference failed (%s)", type(error).__name__)
            raise HTTPException(502, "Laya inference failed") from None

    return app


def download_models(directory: Path) -> None:
    from huggingface_hub import hf_hub_download, snapshot_download

    hf_hub_download(
        GEMMA_REPO,
        filename=GEMMA_FILE,
        revision=GEMMA_REVISION,
        local_dir=directory / "litert-gemma",
        token=False,
    )
    snapshot_download(
        LAYA_REPO,
        revision=LAYA_REVISION,
        local_dir=directory / "mlx-laya",
        allow_patterns=[
            "*.json",
            "model.safetensors",
            "encoder/*",
            "tokenizer/*",
            "LICENSE",
            "NOTICE",
        ],
        token=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["download", "serve"])
    parser.add_argument("--model-dir", type=Path, default=Path(".local-models"))
    parser.add_argument(
        "--gemma-model", type=Path, help="Override the Gemma .litertlm file"
    )
    parser.add_argument(
        "--laya-model", type=Path, help="Override the local Laya MLX directory"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()

    directory = args.model_dir.expanduser().resolve()
    if args.command == "download":
        download_models(directory)
        return
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("The local model service requires an Apple Silicon Mac.")

    gemma_path = (args.gemma_model or directory / "litert-gemma" / GEMMA_FILE).expanduser()
    laya_path = (args.laya_model or directory / "mlx-laya").expanduser()
    if not gemma_path.is_file() or not (laya_path / "model.safetensors").is_file():
        parser.error(
            "Model files are missing. Run the download command first or supply local paths."
        )

    import laya_mlx
    import litert_lm
    import uvicorn

    directory.mkdir(parents=True, exist_ok=True)
    litert_lm.set_min_log_severity(litert_lm.LogSeverity.FATAL)
    laya = laya_mlx.load(str(laya_path.resolve()))
    with litert_lm.Engine(
        str(gemma_path.resolve()),
        backend=litert_lm.Backend.GPU(),
        audio_backend=litert_lm.Backend.CPU(),
        max_num_tokens=4096,
        cache_dir=str(directory),
    ) as gemma:
        uvicorn.run(
            create_app(gemma, laya, litert_lm),
            host=args.host,
            port=args.port,
            access_log=False,
        )


if __name__ == "__main__":
    main()
