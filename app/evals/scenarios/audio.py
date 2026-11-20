from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from app.evals.scenarios.spec import ScenarioDataset

OPENROUTER_TTS_ENDPOINT = "https://openrouter.ai/api/v1/audio/speech"
OPENROUTER_DEFAULTS = {
    "en": ("microsoft/mai-voice-2.1-flash", "en-US-Harper:MAI-Voice-2.1-Flash"),
}
LOCAL_VOICES = {"documentary", "storyteller", "playful-host"}
LOCAL_CHANNELS = {
    "documentary": "martial-lore",
    "storyteller": "breaking-lore",
    "playful-host": "takano-cats",
}
MAX_AUDIO_BYTES = 20_000_000


def prepare_audio(
    dataset: ScenarioDataset,
    *,
    repository_root: Path,
    manifest_path: Path,
    backend: str,
    voices: dict[str, str],
    model: str | None = None,
    local_script: Path | None = None,
    local_python: str = sys.executable,
) -> dict[str, Any]:
    """Materialize scenario speech once and record immutable file hashes and voice IDs."""
    if backend not in {"recorded", "local-qwen3", "openrouter"}:
        raise ValueError("unsupported audio backend")
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "dataset_id": dataset.dataset_id,
        "backend": backend,
        "assets": {},
    }
    root = repository_root.resolve()
    for scenario in dataset.scenarios:
        for step in scenario.example_conversation:
            user = step.user
            if user.modality != "audio":
                continue
            asset_id = f"{scenario.id}:{step.id}"
            if user.source == "recorded":
                path = (root / user.path).resolve()
                manifest["assets"][asset_id] = _asset_entry(
                    path,
                    root,
                    backend="recorded",
                    model=None,
                    voice=None,
                    language=scenario.language,
                    reference_transcript=user.reference_transcript,
                    reused=True,
                )
                continue
            if scenario.provenance.casefold() != "synthetic":
                raise ValueError("speech providers accept only synthetic scenario inputs")
            voice = voices.get(scenario.language)
            if backend == "recorded":
                raise ValueError("synthesized audio requires a synthesis backend")
            if backend == "openrouter":
                provider_model, provider_voice = _openrouter_voice(
                    scenario.language, voice, model
                )
                path, generation_id, reused = _synthesize_openrouter(
                    user.speech_text or "",
                    repository_root=root,
                    model=provider_model,
                    voice=provider_voice,
                )
            else:
                if not local_script or not local_script.is_file():
                    raise ValueError("local-qwen3 requires --local-script pointing to generate_voice.py")
                provider_voice = voice or ("documentary" if scenario.language == "en" else "storyteller")
                if provider_voice not in LOCAL_VOICES:
                    raise ValueError("unsupported Qwen3 voice preset")
                provider_model = "qwen3-tts-1.7b-voice-design-8bit"
                if model and model != provider_model:
                    raise ValueError("the local Qwen3 script uses its configured model")
                path, reused = _synthesize_local(
                    user.speech_text or "",
                    repository_root=root,
                    script=local_script.resolve(),
                    python=local_python,
                    locale="en" if scenario.language == "en" else "pt-BR",
                    voice=provider_voice,
                )
                generation_id = None
            entry = _asset_entry(
                path,
                root,
                backend=backend,
                model=provider_model,
                voice=provider_voice,
                language=scenario.language,
                reference_transcript=user.reference_transcript,
                reused=reused,
            )
            entry["generation_id"] = generation_id
            manifest["assets"][asset_id] = entry
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


def load_audio_manifest(path: Path | None, repository_root: Path) -> dict[str, Any]:
    """Validate recorded or synthesized asset paths before handing bytes to Gemma."""
    if path is None:
        return {"assets": {}}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("assets"), dict):
        raise ValueError("invalid audio manifest")
    root = repository_root.resolve()
    for entry in manifest["assets"].values():
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError("audio manifest contains an invalid asset entry")
        if not isinstance(entry.get("sha256"), str):
            raise ValueError("audio manifest asset has no checksum")
        asset = (root / entry["path"]).resolve()
        if not asset.is_relative_to(root) or not asset.is_file():
            raise ValueError("audio manifest contains a missing or unsafe path")
        if asset.stat().st_size > MAX_AUDIO_BYTES:
            raise ValueError("audio manifest asset exceeds the 20 MB input limit")
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()
        if digest != entry.get("sha256"):
            raise ValueError("audio asset hash does not match its manifest")
    return manifest


def resolve_audio(
    scenario_id: str,
    step_id: str,
    source_path: str | None,
    manifest: dict[str, Any],
    repository_root: Path,
) -> tuple[bytes, dict[str, Any]]:
    """Return one verified audio clip and the voice provenance for its report row."""
    root = repository_root.resolve()
    entry = manifest.get("assets", {}).get(f"{scenario_id}:{step_id}")
    if entry is None and source_path:
        path = (root / source_path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("missing or unsafe audio asset")
        if path.stat().st_size > MAX_AUDIO_BYTES:
            raise ValueError("audio asset exceeds the 20 MB input limit")
        data = path.read_bytes()
        entry = {
            "path": str(path.relative_to(root)),
            "sha256": hashlib.sha256(data).hexdigest(),
            "backend": "recorded",
            "model": None,
            "voice": None,
            "language": None,
            "reused": True,
        }
    if entry is None:
        raise ValueError(f"no prepared audio for {scenario_id}:{step_id}")
    path = (root / entry["path"]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("audio manifest contains a missing or unsafe path")
    if path.stat().st_size > MAX_AUDIO_BYTES:
        raise ValueError("audio asset exceeds the 20 MB input limit")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
        raise ValueError("audio asset hash changed after preparation")
    return data, entry


def _openrouter_voice(
    language: str, requested_voice: str | None, requested_model: str | None
) -> tuple[str, str]:
    """Choose a known voice/model pair or require explicit compatible OpenRouter settings."""
    defaults = OPENROUTER_DEFAULTS.get(language)
    if defaults is None and not (requested_model and requested_voice):
        raise ValueError("Portuguese OpenRouter speech needs an explicit compatible model and voice")
    default_model, default_voice = defaults or ("", "")
    if requested_model and requested_model != default_model and not requested_voice:
        raise ValueError("a non-default OpenRouter model requires an explicit compatible voice")
    return requested_model or default_model, requested_voice or default_voice


def _synthesize_openrouter(
    text: str, *, repository_root: Path, model: str, voice: str
) -> tuple[Path, str | None, bool]:
    """Generate and cache bounded synthetic speech, retrying only transient failures."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is required for OpenRouter audio")
    cache_key = _cache_key(text, "openrouter", model, voice)
    destination = repository_root / ".eval_runs" / "scenarios" / "audio" / f"{cache_key}.mp3"
    metadata_path = destination.with_suffix(".json")
    if destination.is_file() and destination.stat().st_size <= MAX_AUDIO_BYTES and metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if hashlib.sha256(destination.read_bytes()).hexdigest() == metadata.get("sha256"):
            return destination, metadata.get("generation_id"), True
    request = urllib.request.Request(
        OPENROUTER_TTS_ENDPOINT,
        data=json.dumps(
            {"model": model, "input": " ".join(text.split()), "voice": voice, "response_format": "mp3"}
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        method="POST",
    )
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                content_type = response.headers.get("Content-Type", "").casefold()
                if not content_type.startswith("audio/"):
                    raise ValueError("OpenRouter did not return audio")
                data = response.read(MAX_AUDIO_BYTES + 1)
                if not data or len(data) > MAX_AUDIO_BYTES:
                    raise ValueError("OpenRouter returned empty or oversized audio")
                generation_id = response.headers.get("X-Generation-Id")
            break
        except urllib.error.HTTPError as exc:
            retryable = exc.code in {408, 429} or 500 <= exc.code <= 599
            if retryable and attempt < 3:
                time.sleep(min(2 ** (attempt - 1), 4))
                continue
            raise ValueError(f"OpenRouter audio request failed (HTTP {exc.code})") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt < 3:
                time.sleep(min(2 ** (attempt - 1), 4))
                continue
            raise ValueError(f"OpenRouter audio request failed ({type(exc).__name__})") from None
    _write_cache(destination, data)
    metadata_path.write_text(
        json.dumps(
            {"sha256": hashlib.sha256(data).hexdigest(), "generation_id": generation_id},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return destination, generation_id, False


def _synthesize_local(
    text: str,
    *,
    repository_root: Path,
    script: Path,
    python: str,
    locale: str,
    voice: str,
) -> tuple[Path, bool]:
    """Run the configured local Qwen3 script and cache its expected WAV output."""
    model = "qwen3-tts-1.7b-voice-design-8bit"
    cache_key = _cache_key(text, "local-qwen3", model, f"{locale}:{voice}")
    destination = repository_root / ".eval_runs" / "scenarios" / "audio" / f"{cache_key}.wav"
    metadata_path = destination.with_suffix(".json")
    if destination.is_file() and destination.stat().st_size <= MAX_AUDIO_BYTES and metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if hashlib.sha256(destination.read_bytes()).hexdigest() == metadata.get("sha256"):
            return destination, True
    channel = LOCAL_CHANNELS[voice]
    with tempfile.TemporaryDirectory(prefix="capy-eval-audio-") as temporary:
        output_dir = Path(temporary)
        command = [
            python,
            str(script),
            "--channel", channel,
            "--locale", locale,
            "--voice", voice,
            "--text", text,
            "--output-dir", str(output_dir),
        ]
        try:
            subprocess.run(
                command,
                check=True,
                timeout=1800,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            raise ValueError(f"local speech generation failed ({type(exc).__name__})") from None
        generated = output_dir / f"qwen-{channel}-{voice}-{locale}.wav"
        if not generated.is_file() or generated.stat().st_size == 0:
            raise ValueError("local speech script did not produce the expected WAV file")
        if generated.stat().st_size > MAX_AUDIO_BYTES:
            raise ValueError("local speech output exceeds the 20 MB input limit")
        _write_cache(destination, generated.read_bytes())
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    metadata_path.write_text(json.dumps({"sha256": digest}, indent=2) + "\n", encoding="utf-8")
    return destination, False


def _cache_key(text: str, backend: str, model: str, voice: str) -> str:
    """Identify audio by normalized speech and every setting that changes its sound."""
    identity = json.dumps(
        {"text": " ".join(text.split()), "backend": backend, "model": model, "voice": voice},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _write_cache(destination: Path, data: bytes) -> None:
    """Publish complete audio bytes atomically so interrupted generation leaves no cache hit."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + f".{os.getpid()}.{time.time_ns()}.part")
    temporary.write_bytes(data)
    temporary.replace(destination)


def _asset_entry(
    path: Path,
    root: Path,
    *,
    backend: str,
    model: str | None,
    voice: str | None,
    language: str,
    reference_transcript: str | None,
    reused: bool,
) -> dict[str, Any]:
    """Create the relative path, checksum, voice identity, and reuse metadata for one clip."""
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("audio cache must be inside the repository")
    if resolved.stat().st_size > MAX_AUDIO_BYTES:
        raise ValueError("audio asset exceeds the 20 MB input limit")
    data = resolved.read_bytes()
    return {
        "path": str(resolved.relative_to(root)),
        "sha256": hashlib.sha256(data).hexdigest(),
        "backend": backend,
        "model": model,
        "voice": voice,
        "language": language,
        "reference_transcript": reference_transcript,
        "reused": reused,
    }
