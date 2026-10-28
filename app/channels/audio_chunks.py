"""Split a recording near pauses, with overlap to protect words at the cuts."""

from __future__ import annotations

import math
import subprocess
import sys
import wave
from array import array
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory


SAMPLE_RATE = 16_000
SAMPLE_BYTES = 2
MAX_CHUNK_FRAMES = 30 * SAMPLE_RATE
OVERLAP_FRAMES = SAMPLE_RATE
WINDOW_FRAMES = SAMPLE_RATE // 50  # Examine sound levels every 20 milliseconds.
MIN_PAUSE_FRAMES = int(0.3 * SAMPLE_RATE)
SILENCE_AMPLITUDE = 32768 * 10 ** (-40 / 20)


class AudioError(RuntimeError):
    """A safe error code; decoder output and audio content are never included."""


@dataclass(frozen=True)
class AudioChunk:
    wav_bytes: bytes
    start_seconds: float
    end_seconds: float
    overlap_seconds: float


class AudioChunker:
    """Decode audio and yield WAV clips that never exceed 30 seconds.

    Prefer the middle of a pause lasting at least 300 ms between seconds
    25 and 29 of each clip. If no suitable pause exists, cut at 30 seconds.
    The next clip starts one second before the cut so a word split across
    clips can still be heard in full. This detects quiet audio, not sentences.

    FFmpeg converts input to mono, 16 kHz PCM in a private temporary directory.
    Decoding finishes successfully before any clips are yielded. Only one
    clip is loaded into memory at a time; the context manager removes decoded
    audio even when the consumer or provider fails partway through.
    """

    def __init__(
        self,
        *,
        ffmpeg_binary: str = "ffmpeg",
        decode_timeout_seconds: float = 120,
        max_input_bytes: int = 20_000_000,
        max_decoded_bytes: int = 512 * 1024 * 1024,
    ):
        if not math.isfinite(decode_timeout_seconds) or decode_timeout_seconds <= 0:
            raise ValueError("decode_timeout_seconds must be finite and positive")
        if max_input_bytes <= 0 or max_decoded_bytes < MAX_CHUNK_FRAMES * SAMPLE_BYTES:
            raise ValueError("audio size limits are invalid")
        self.ffmpeg_binary = ffmpeg_binary
        self.decode_timeout_seconds = decode_timeout_seconds
        self.max_input_bytes = max_input_bytes
        self.max_decoded_bytes = max_decoded_bytes

    @contextmanager
    def chunks(
        self,
        audio: bytes,
        *,
        decode_timeout_seconds: float | None = None,
    ) -> Iterator[Iterator[AudioChunk]]:
        """Use as ``with chunker.chunks(audio) as chunks: ...`` for cleanup."""
        if not audio:
            raise AudioError("empty_audio")
        if len(audio) > self.max_input_bytes:
            raise AudioError("audio_download_limit")
        timeout = self.decode_timeout_seconds if decode_timeout_seconds is None else decode_timeout_seconds
        if not math.isfinite(timeout) or timeout <= 0:
            raise AudioError("audio_decode_timeout")
        try:
            with TemporaryDirectory(prefix="scheduling-audio-") as directory:
                decoded = Path(directory) / "decoded.pcm"
                self._decode(audio, decoded, timeout)
                chunks = self._read_chunks(decoded)
                try:
                    yield chunks
                finally:
                    chunks.close()
        except OSError:
            raise AudioError("audio_io_failed") from None

    def _decode(self, audio: bytes, output: Path, timeout_seconds: float) -> None:
        # Only stdin is allowed as an input protocol: uploaded media cannot
        # reference local files or trigger network requests through FFmpeg.
        command = [
            self.ffmpeg_binary, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-xerror", "-protocol_whitelist", "pipe", "-threads", "1",
            "-i", "pipe:0", "-map", "0:a:0", "-vn", "-sn", "-dn",
            "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le",
            "-threads", "1", "-f", "s16le", "-fs", str(self.max_decoded_bytes),
            "pipe:1",
        ]
        try:
            with output.open("wb") as destination:
                subprocess.run(
                    command,
                    input=audio,
                    stdout=destination,
                    stderr=subprocess.DEVNULL,
                    timeout=timeout_seconds,
                    check=True,
                )
        except FileNotFoundError:
            raise AudioError("ffmpeg_unavailable") from None
        except subprocess.TimeoutExpired:
            raise AudioError("audio_decode_timeout") from None
        except subprocess.CalledProcessError:
            raise AudioError("invalid_audio") from None

        size = output.stat().st_size
        # FFmpeg's size limit stops output; never mistake that truncated
        # prefix for the full recording, even if FFmpeg exits successfully.
        if size >= self.max_decoded_bytes:
            raise AudioError("audio_decoded_limit")
        if not size or size % SAMPLE_BYTES:
            raise AudioError("invalid_audio")

    def _read_chunks(self, decoded: Path) -> Iterator[AudioChunk]:
        total_frames = decoded.stat().st_size // SAMPLE_BYTES
        start = 0
        previous_end = 0
        with decoded.open("rb") as source:
            while start < total_frames:
                source.seek(start * SAMPLE_BYTES)
                pcm = source.read(MAX_CHUNK_FRAMES * SAMPLE_BYTES)
                frames = len(pcm) // SAMPLE_BYTES
                cut = _pause_cut(pcm) if start + frames < total_frames else frames
                end = start + cut
                yield AudioChunk(
                    wav_bytes=_as_wav(pcm[:cut * SAMPLE_BYTES]),
                    start_seconds=start / SAMPLE_RATE,
                    end_seconds=end / SAMPLE_RATE,
                    overlap_seconds=max(0, previous_end - start) / SAMPLE_RATE,
                )
                if end == total_frames:
                    return
                previous_end = end
                start = end - OVERLAP_FRAMES


def _pause_cut(pcm: bytes) -> int:
    """Return the last suitable pause's midpoint, or the full clip length."""
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()

    silence_start = None
    cut = len(samples)
    search_start = 25 * SAMPLE_RATE
    search_end = min(29 * SAMPLE_RATE, len(samples))
    threshold_squared = SILENCE_AMPLITUDE ** 2
    for position in range(search_start, search_end, WINDOW_FRAMES):
        window = samples[position:min(position + WINDOW_FRAMES, search_end)]
        is_quiet = sum(value * value for value in window) <= threshold_squared * len(window)
        if is_quiet:
            if silence_start is None:
                silence_start = position
        elif silence_start is not None:
            if position - silence_start >= MIN_PAUSE_FRAMES:
                cut = (silence_start + position) // 2
            silence_start = None
    if silence_start is not None and search_end - silence_start >= MIN_PAUSE_FRAMES:
        cut = (silence_start + search_end) // 2
    return cut


def _as_wav(pcm: bytes) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(SAMPLE_BYTES)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(pcm)
    return buffer.getvalue()
