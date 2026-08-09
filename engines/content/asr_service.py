from __future__ import annotations

import os
import shutil
import subprocess
import sys
import ctypes
from pathlib import Path
from typing import Any


DEFAULT_ASR_INITIAL_PROMPT = (
    "以下是简体中文金融财经视频口播，内容涉及上证指数、深证成指、创业板、科创板、恒生科技、"
    "半导体、新能源、医药、券商、杠杆、平仓、强平、均量线、MACD、成交额、成交量、"
    "满仓、空仓、止盈、止损、支撑、压力、突破、回调。"
)


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
        initial_prompt: str | None = None,
    ) -> None:
        # Knowledge ingestion favours entity/number accuracy over throughput.  A
        # caller may still deliberately select a smaller model for preview jobs.
        self.model_size = model_size or os.getenv("ASR_MODEL_SIZE", "large-v3")
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
        self.initial_prompt = self._resolve_initial_prompt(initial_prompt)

    def transcribe(
        self,
        audio_path: str | Path,
        language_hint: str | None = None,
        speaker_mode: str = "UNKNOWN",
    ) -> dict:
        self._ensure_runtime_paths()
        try:
            from faster_whisper import BatchedInferencePipeline, WhisperModel
        except ImportError as exc:
            raise RuntimeError("faster-whisper is not installed") from exc
        speaker_label = self._speaker_label_for_mode(speaker_mode)
        model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
        transcribe_kwargs: dict[str, Any] = {
            "language": language_hint,
            "vad_filter": True,
            "beam_size": self.beam_size,
            "best_of": self.best_of,
            "condition_on_previous_text": self.condition_on_previous_text,
            "word_timestamps": True,
        }
        if self.initial_prompt:
            transcribe_kwargs["initial_prompt"] = self.initial_prompt
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
            avg_logprob = self._optional_float(getattr(segment, "avg_logprob", None))
            no_speech_prob = self._optional_float(getattr(segment, "no_speech_prob", None))
            compression_ratio = self._optional_float(getattr(segment, "compression_ratio", None))
            words = [
                {
                    "start_ms": int(float(word.start) * 1000),
                    "end_ms": int(float(word.end) * 1000),
                    "word": str(word.word or "").strip(),
                    "probability": self._optional_float(getattr(word, "probability", None)),
                }
                for word in (getattr(segment, "words", None) or [])
                if str(getattr(word, "word", "") or "").strip()
            ]
            text_parts.append(segment_text)
            quality_proxy = self._confidence_from_metrics(avg_logprob, no_speech_prob)
            word_probabilities = [word["probability"] for word in words if word.get("probability") is not None]
            items.append(
                {
                    "segment_index": index,
                    "start_ms": int(float(segment.start) * 1000),
                    "end_ms": int(float(segment.end) * 1000),
                    "speaker_label": speaker_label,
                    "text": segment_text,
                    "avg_logprob": avg_logprob,
                    "no_speech_prob": no_speech_prob,
                    "compression_ratio": compression_ratio,
                    "confidence_score": quality_proxy,
                    "asr_quality_proxy": quality_proxy,
                    "mean_word_probability": round(sum(word_probabilities) / len(word_probabilities), 4) if word_probabilities else None,
                    "min_word_probability": round(min(word_probabilities), 4) if word_probabilities else None,
                    "word_timestamps": words,
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
            "speaker_mode": str(speaker_mode or "UNKNOWN").strip().upper(),
        }

    @staticmethod
    def _speaker_label_for_mode(speaker_mode: str | None) -> str | None:
        """UNKNOWN/DIARIZE 不伪造说话人；仅 SINGLE_SPEAKER 确认单人口播。

        DIARIZE 模式下标签由 DiarizationService 覆写，此处保持未知。
        """
        mode = str(speaker_mode or "UNKNOWN").strip().upper()
        if mode == "SINGLE_SPEAKER":
            return "speaker_0"
        return None

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _confidence_from_metrics(avg_logprob: float | None, no_speech_prob: float | None) -> float | None:
        """A conservative, explicitly derived quality signal; never invent one."""
        if avg_logprob is None and no_speech_prob is None:
            return None
        # Whisper log probabilities are normally non-positive.  This is a
        # quality proxy, not a calibrated factual-confidence probability.
        logprob_quality = 1.0 if avg_logprob is None else max(0.0, min(1.0, 1.0 + avg_logprob / 2.0))
        speech_quality = 1.0 if no_speech_prob is None else max(0.0, min(1.0, 1.0 - no_speech_prob))
        return round((logprob_quality * 0.7) + (speech_quality * 0.3), 4)

    def _ensure_runtime_paths(self) -> None:
        if self.device != "cuda":
            return
        self._ensure_nvidia_library_path()
        self._preload_cuda_libraries()

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
    def _resolve_initial_prompt(explicit_value: str | None) -> str:
        if explicit_value is not None:
            return explicit_value.strip()
        env_value = os.getenv("ASR_INITIAL_PROMPT")
        if env_value is not None:
            return env_value.strip()
        return DEFAULT_ASR_INITIAL_PROMPT

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
        cuda_visible = os.getenv("CUDA_VISIBLE_DEVICES")
        if cuda_visible is not None and str(cuda_visible).strip() in {"", "-1", "none", "None"}:
            return False
        nvidia_smi = shutil.which("nvidia-smi")
        if not nvidia_smi:
            return False
        try:
            result = subprocess.run(
                [nvidia_smi, "-L"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return False
        return result.returncode == 0 and "GPU" in str(result.stdout or "")

    @staticmethod
    def _ensure_nvidia_library_path() -> None:
        if AsrService._is_windows():
            return
        candidate_dirs: list[str] = []
        for root in sys.path:
            site_root = Path(root)
            nvidia_root = site_root / "nvidia"
            if not nvidia_root.exists():
                continue
            for relative in (
                ("cublas", "lib"),
                ("cudnn", "lib"),
                ("cuda_runtime", "lib"),
                ("cuda_nvrtc", "lib"),
                ("cu13", "lib"),
            ):
                path = nvidia_root.joinpath(*relative)
                if path.is_dir():
                    candidate_dirs.append(str(path))
        if not candidate_dirs:
            return
        existing = os.environ.get("LD_LIBRARY_PATH", "")
        ordered: list[str] = []
        seen: set[str] = set()
        for path in candidate_dirs + ([entry for entry in existing.split(":") if entry] if existing else []):
            key = path.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(key)
        os.environ["LD_LIBRARY_PATH"] = ":".join(ordered)

    @staticmethod
    def _preload_cuda_libraries() -> None:
        if AsrService._is_windows():
            return
        for root in sys.path:
            site_root = Path(root)
            nvidia_root = site_root / "nvidia"
            if not nvidia_root.exists():
                continue
            for library_path in (
                nvidia_root / "cuda_runtime" / "lib" / "libcudart.so.12",
                nvidia_root / "cuda_nvrtc" / "lib" / "libnvrtc.so.12",
                nvidia_root / "cublas" / "lib" / "libcublasLt.so.12",
                nvidia_root / "cublas" / "lib" / "libcublas.so.12",
                nvidia_root / "cudnn" / "lib" / "libcudnn.so.9",
            ):
                if not library_path.exists():
                    continue
                try:
                    ctypes.CDLL(str(library_path), mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    continue

    @staticmethod
    def _is_windows() -> bool:
        return os.name == "nt"
