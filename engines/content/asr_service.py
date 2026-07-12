from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class AsrService:
    def __init__(
        self,
        model_size: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
        use_batched: bool | None = None,
        batch_size: int | None = None,
        chunk_length_seconds: int | None = None,
        beam_size: int | None = None,
        best_of: int | None = None,
        condition_on_previous_text: bool | None = None,
    ) -> None:
        self.model_size = model_size or os.getenv("ASR_MODEL_SIZE", "small")
        requested_device = device or os.getenv("ASR_DEVICE", "auto")
        requested_compute_type = compute_type or os.getenv("ASR_COMPUTE_TYPE", "auto")
        self.device = self._resolve_device(requested_device)
        self.compute_type = self._resolve_compute_type(requested_compute_type, self.device)
        self.use_batched = self._resolve_use_batched(use_batched, self.device)
        self.batch_size = self._resolve_batch_size(batch_size, self.device, self.use_batched)
        self.chunk_length_seconds = self._resolve_int_config(
            explicit_value=chunk_length_seconds,
            env_key="ASR_CHUNK_LENGTH_SECONDS",
            default_value=30 if self.use_batched else None,
        )
        self.beam_size = self._resolve_int_config(
            explicit_value=beam_size,
            env_key="ASR_BEAM_SIZE",
            default_value=3 if self.device == "cuda" else 5,
        )
        self.best_of = self._resolve_int_config(
            explicit_value=best_of,
            env_key="ASR_BEST_OF",
            default_value=self.beam_size,
        )
        self.condition_on_previous_text = self._resolve_bool_config(
            explicit_value=condition_on_previous_text,
            env_key="ASR_CONDITION_ON_PREVIOUS_TEXT",
            default_value=False if self.device == "cuda" else True,
        )

    def transcribe(self, audio_path: str | Path, language_hint: str | None = None) -> dict:
        try:
            from faster_whisper import BatchedInferencePipeline, WhisperModel
        except ImportError as exc:
            raise RuntimeError("faster-whisper is not installed") from exc
        model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
        transcribe_kwargs: dict[str, Any] = {
            "language": language_hint,
            "vad_filter": True,
            "beam_size": self.beam_size,
            "best_of": self.best_of,
            "condition_on_previous_text": self.condition_on_previous_text,
        }
        if self.chunk_length_seconds:
            transcribe_kwargs["chunk_length"] = self.chunk_length_seconds
        if self.use_batched:
            pipeline = BatchedInferencePipeline(model=model)
            transcribe_kwargs["batch_size"] = self.batch_size
            transcribe_kwargs["without_timestamps"] = False
            segments, info = pipeline.transcribe(str(audio_path), **transcribe_kwargs)
        else:
            segments, info = model.transcribe(str(audio_path), **transcribe_kwargs)
        items = []
        text_parts: list[str] = []
        for index, segment in enumerate(segments):
            segment_text = (segment.text or "").strip()
            text_parts.append(segment_text)
            items.append(
                {
                    "segment_index": index,
                    "start_ms": int(float(segment.start) * 1000),
                    "end_ms": int(float(segment.end) * 1000),
                    "speaker_label": "speaker_0",
                    "text": segment_text,
                    "avg_logprob": None,
                    "no_speech_prob": None,
                    "compression_ratio": None,
                    "confidence_score": None,
                }
            )
        return {
            "language": getattr(info, "language", language_hint or "unknown"),
            "duration_seconds": getattr(info, "duration", None),
            "text": "\n".join(part for part in text_parts if part).strip(),
            "segments": items,
            "provider": "faster_whisper",
            "model": self.model_size,
            "device": self.device,
            "compute_type": self.compute_type,
            "use_batched": self.use_batched,
            "batch_size": self.batch_size,
            "chunk_length_seconds": self.chunk_length_seconds,
            "beam_size": self.beam_size,
        }

    @classmethod
    def _resolve_device(cls, requested_device: str) -> str:
        value = (requested_device or "auto").strip().lower()
        if value != "auto":
            return value
        if cls._cuda_available():
            return "cuda"
        return "cpu"

    @staticmethod
    def _resolve_compute_type(requested_compute_type: str, device: str) -> str:
        value = (requested_compute_type or "auto").strip().lower()
        if value != "auto":
            return value
        if device == "cuda":
            return "float16"
        return "int8"

    @classmethod
    def _resolve_use_batched(cls, explicit_value: bool | None, device: str) -> bool:
        if explicit_value is not None:
            return explicit_value
        env_value = os.getenv("ASR_USE_BATCHED")
        if env_value is not None:
            return cls._parse_bool(env_value, default=device == "cuda")
        return device == "cuda"

    @classmethod
    def _resolve_batch_size(cls, explicit_value: int | None, device: str, use_batched: bool) -> int:
        if not use_batched:
            return 1
        return cls._resolve_int_config(
            explicit_value=explicit_value,
            env_key="ASR_BATCH_SIZE",
            default_value=16 if device == "cuda" else 4,
        ) or 1

    @classmethod
    def _resolve_int_config(
        cls,
        explicit_value: int | None,
        env_key: str,
        default_value: int | None,
    ) -> int | None:
        if explicit_value is not None:
            return explicit_value
        env_value = os.getenv(env_key)
        if env_value in (None, ""):
            return default_value
        return int(env_value)

    @classmethod
    def _resolve_bool_config(
        cls,
        explicit_value: bool | None,
        env_key: str,
        default_value: bool,
    ) -> bool:
        if explicit_value is not None:
            return explicit_value
        env_value = os.getenv(env_key)
        if env_value is None:
            return default_value
        return cls._parse_bool(env_value, default=default_value)

    @staticmethod
    def _parse_bool(raw_value: str | bool, default: bool = False) -> bool:
        if isinstance(raw_value, bool):
            return raw_value
        value = str(raw_value).strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
        return default

    @staticmethod
    def _cuda_available() -> bool:
        try:
            import ctranslate2
        except ImportError:
            return False
        try:
            return int(ctranslate2.get_cuda_device_count()) > 0
        except Exception:
            return False
