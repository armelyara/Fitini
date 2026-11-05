"""
audio_transcriber.py — audio acquisition + transcription for Fitini Voice Analyzer.

Three sources, one async interface (`stream_transcriptions`):

  * MICROPHONE            — sounddevice capture -> energy VAD gating -> faster-whisper.
  * FILE                  — transcribe a recorded file with faster-whisper.

CRITICAL FIX vs. the original spec: VAD and Whisper are synchronous, CPU-heavy
calls. Running them directly inside the asyncio event loop blocks every WebSocket on the
server. Here, every model call is dispatched with `asyncio.to_thread(...)` so the event
loop stays responsive.
"""

from __future__ import annotations

import asyncio
import queue
import time
from typing import Any, AsyncGenerator, Dict, Optional


class AudioStreamTranscriber:
    def __init__(self, config: Dict[str, Any], use_microphone: bool = False):
        p = config.get("pipeline", {})
        self.config = config
        self.use_microphone = use_microphone
        self.sample_rate = int(p.get("sample_rate", 16000))
        self.model_size = p.get("model_size", "base.en")
        self.device = p.get("device", "cpu")
        self.compute_type = p.get("compute_type", "int8")
        self.vad_threshold = float(p.get("vad_threshold", 0.5))
        self.energy_threshold = float(p.get("energy_threshold", 0.01))
        self.silence_frames_limit = int(p.get("silence_frames", 18))
        self.chunk_samples = int(p.get("chunk_samples", 512))
        self.sim_interval = float(p.get("simulation_interval_seconds", 1.1))
        self.beam_size = int(p.get("beam_size", 1))
        self.language = p.get("language", "en")

        self.audio_queue: "queue.Queue" = queue.Queue()
        self.whisper_model = None
        self.vad_model = None

    # ------------------------------------------------------------------ #
    def _load_models(self) -> None:
        """Blocking Whisper load -- always called via asyncio.to_thread (no torch)."""
        from faster_whisper import WhisperModel  # type: ignore

        self.whisper_model = WhisperModel(
            self.model_size, device=self.device, compute_type=self.compute_type
        )

    @staticmethod
    def _now_ts() -> str:
        return time.strftime("%H:%M:%S")

    # ------------------------------------------------------------------ #
    async def stream_transcriptions(self) -> AsyncGenerator[Dict[str, str], None]:
        """Yield {'ts', 'text'} dicts as speech is transcribed (live capture)."""
        async for item in self._stream_microphone():
            yield item

    # ------------------------------------------------------------------ #
    def _audio_callback(self, indata, frames, time_info, status):  # pragma: no cover
        self.audio_queue.put(indata[:, 0].copy())

    def _reset_segmenter(self) -> None:
        self._speech_buffer: list = []
        self._silence_frames: int = 0

    async def _ingest_chunk(self, chunk, vad_fn, transcribe_fn):
        """Feed one audio chunk through the VAD-gated segmenter.

        `vad_fn(chunk) -> float` and `transcribe_fn(audio) -> str` are injected so this
        state machine can be exercised with real models (mic path) or stubs (tests),
        and both are dispatched OFF the event loop via asyncio.to_thread. Returns a
        transcribed string when a speech segment completes, else None.
        """
        import numpy as np  # type: ignore

        # VAD inference OFF the event loop.
        speech_prob = await asyncio.to_thread(vad_fn, chunk)

        if speech_prob > self.vad_threshold:
            self._speech_buffer.append(chunk)
            self._silence_frames = 0
        elif self._speech_buffer:
            self._speech_buffer.append(chunk)
            self._silence_frames += 1
            if self._silence_frames > self.silence_frames_limit:
                full_audio = np.concatenate(self._speech_buffer)
                self._reset_segmenter()
                # Transcription OFF the event loop.
                text = await asyncio.to_thread(transcribe_fn, full_audio)
                return text or None
        return None

    async def _stream_microphone(self) -> AsyncGenerator[Dict[str, str], None]:  # pragma: no cover
        import sounddevice as sd  # type: ignore
        import numpy as np  # type: ignore

        if self.whisper_model is None:
            await asyncio.to_thread(self._load_models)

        self._reset_segmenter()

        def vad_fn(chunk):
            # Energy-based VAD (RMS) -- deterministic, offline, no torch/Silero.
            rms = float(np.sqrt(np.mean(np.square(chunk)))) if len(chunk) else 0.0
            return 1.0 if rms >= self.energy_threshold else 0.0

        with sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self.chunk_samples,
            callback=self._audio_callback,
        ):
            while True:
                await asyncio.sleep(0.01)
                while not self.audio_queue.empty():
                    chunk = self.audio_queue.get_nowait()
                    text = await self._ingest_chunk(chunk, vad_fn, self._transcribe_array)
                    if text:
                        yield {"ts": self._now_ts(), "text": text}

    # ------------------------------------------------------------------ #
    def _transcribe_array(self, audio) -> str:  # pragma: no cover
        """Blocking Whisper call — always dispatched via asyncio.to_thread."""
        segments, _ = self.whisper_model.transcribe(
            audio, beam_size=self.beam_size, language=self.language, vad_filter=False
        )
        return " ".join(seg.text.strip() for seg in segments).strip()

    async def transcribe_file(self, path: str) -> AsyncGenerator[Dict[str, str], None]:
        """Transcribe a recorded file, yielding each segment as it is decoded."""
        if self.whisper_model is None:
            await asyncio.to_thread(self._load_models_whisper_only)

        segments = await asyncio.to_thread(self._file_segments, path)
        for seg in segments:
            yield {"ts": self._fmt_seconds(seg["start"]), "text": seg["text"]}

    def _load_models_whisper_only(self) -> None:  # pragma: no cover
        from faster_whisper import WhisperModel  # type: ignore

        self.whisper_model = WhisperModel(
            self.model_size, device=self.device, compute_type=self.compute_type
        )

    def _file_segments(self, path: str):  # pragma: no cover
        segments, _ = self.whisper_model.transcribe(
            path, beam_size=self.beam_size, language=self.language, vad_filter=True
        )
        out = []
        for seg in segments:
            txt = seg.text.strip()
            if txt:
                out.append({"start": seg.start, "text": txt})
        return out

    @staticmethod
    def _fmt_seconds(s: float) -> str:
        s = int(s or 0)
        return f"{s // 60:02d}:{s % 60:02d}"
