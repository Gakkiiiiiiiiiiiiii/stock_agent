from __future__ import annotations

import importlib
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from financial_agent.utils import project_root


logger = logging.getLogger(__name__)

VIDEO_OCR_DENOISE_PATH = Path("config") / "video_ocr_denoise.yaml"


class VideoOcrService:
    def __init__(
        self,
        tesseract_bin: str | None = None,
        language: str | None = None,
        backend: str | None = None,
        denoise_path: str | Path | None = None,
    ) -> None:
        self.tesseract_bin = tesseract_bin or os.getenv("TESSERACT_BIN", "tesseract")
        self.language = language or os.getenv("VIDEO_OCR_LANGUAGE", "chi_sim+eng")
        self.backend = (backend or os.getenv("VIDEO_OCR_BACKEND", "paddleocr")).strip().lower()
        self.paddle_lang = os.getenv("VIDEO_OCR_PADDLE_LANG", "ch").strip() or "ch"
        self.paddle_device = os.getenv("VIDEO_OCR_DEVICE", "auto").strip() or "auto"
        self.paddle_text_rec_score_thresh = self._read_float_env("VIDEO_OCR_SCORE_THRESH", 0.75)
        self.paddle_det_model_name = os.getenv("VIDEO_OCR_DET_MODEL_NAME", "PP-OCRv5_server_det").strip() or "PP-OCRv5_server_det"
        self.paddle_rec_model_name = os.getenv("VIDEO_OCR_REC_MODEL_NAME", "PP-OCRv5_server_rec").strip() or "PP-OCRv5_server_rec"
        if self.backend != "paddleocr":
            raise ValueError(f"unsupported VIDEO_OCR_BACKEND: {self.backend}. Only 'paddleocr' is allowed.")
        self._paddleocr_class = None
        self._paddleocr_engine = None
        self._dll_dir_handles: list[Any] = []
        self.denoise_config = self._load_denoise_config(denoise_path)

    def available(self) -> bool:
        return self._paddleocr_available()

    def extract_text(self, image_path: str | Path) -> str:
        source = Path(image_path)
        if not source.exists():
            raise FileNotFoundError(source)
        if not self.available():
            raise RuntimeError("PaddleOCR runtime is not installed or failed to import.")
        merged_lines: list[str] = []
        seen: set[str] = set()
        text = self._run_paddleocr(source)
        for line in self._clean_lines(text):
            if line in seen:
                continue
            seen.add(line)
            merged_lines.append(line)
        return "\n".join(merged_lines).strip()

    def _paddleocr_available(self) -> bool:
        return self._load_paddleocr_class() is not None

    def _run_paddleocr(self, source: Path) -> str:
        engine = self._get_paddleocr_engine()
        try:
            results = engine.predict(str(source))
        except Exception as exc:
            raise RuntimeError(f"PaddleOCR prediction failed for {source.name}: {exc}") from exc
        if not results:
            raise RuntimeError(f"PaddleOCR returned no result for {source.name}.")
        return self._extract_paddleocr_text(results[0])

    def _get_paddleocr_engine(self):
        if not self._paddleocr_available():
            raise RuntimeError("PaddleOCR runtime is not installed. Please install paddleocr and paddlepaddle-gpu.")
        if self._paddleocr_engine is None:
            self._ensure_paddle_runtime_paths()
            paddleocr_class = self._load_paddleocr_class()
            if paddleocr_class is None:
                raise RuntimeError("PaddleOCR runtime is not installed. Please install paddleocr and paddlepaddle-gpu.")
            try:
                resolved_device = self._resolve_paddle_device(self.paddle_device)
                self._paddleocr_engine = paddleocr_class(
                    lang=self.paddle_lang,
                    device=resolved_device,
                    text_detection_model_name=self.paddle_det_model_name,
                    text_recognition_model_name=self.paddle_rec_model_name,
                    text_rec_score_thresh=self.paddle_text_rec_score_thresh,
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                )
            except Exception as exc:
                raise RuntimeError(f"Failed to initialize PaddleOCR runtime on {self.paddle_device}: {exc}") from exc
        return self._paddleocr_engine

    def _ensure_paddle_runtime_paths(self) -> None:
        if os.name != "nt":
            return
        env_root = Path(sys.executable).resolve().parent
        nvidia_root = env_root / "Lib" / "site-packages" / "nvidia"
        if not nvidia_root.exists():
            return
        candidate_dirs: list[Path] = []
        candidate_dirs.extend(path for path in nvidia_root.glob("*/bin") if path.is_dir())
        candidate_dirs.extend(path for path in nvidia_root.glob("*/bin/x86_64") if path.is_dir())
        ordered_dirs = []
        seen_paths: set[str] = set()
        for path in candidate_dirs:
            key = str(path.resolve()).lower()
            if key in seen_paths:
                continue
            seen_paths.add(key)
            ordered_dirs.append(path.resolve())
        if not ordered_dirs:
            return
        current_path = os.environ.get("PATH", "")
        updated_path = current_path
        for path in reversed(ordered_dirs):
            path_str = str(path)
            if path_str.lower() not in current_path.lower():
                updated_path = f"{path_str};{updated_path}" if updated_path else path_str
        os.environ["PATH"] = updated_path
        if hasattr(os, "add_dll_directory"):
            for path in ordered_dirs:
                try:
                    self._dll_dir_handles.append(os.add_dll_directory(str(path)))
                except OSError:
                    continue

    def _extract_paddleocr_text(self, result: Any) -> str:
        rec_texts = list(result.get("rec_texts") or [])
        rec_scores = list(result.get("rec_scores") or [])
        raw_boxes = result.get("rec_boxes")
        rec_boxes = [] if raw_boxes is None else list(raw_boxes)
        items: list[dict[str, Any]] = []
        for text, score, box in zip(rec_texts, rec_scores, rec_boxes):
            cleaned = str(text or "").strip()
            confidence = self._coerce_float(score)
            if not cleaned or confidence < self.paddle_text_rec_score_thresh:
                continue
            coords = self._normalize_box(box)
            if coords is None:
                continue
            x1, y1, x2, y2 = coords
            items.append(
                {
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                    "text": cleaned,
                    "score": confidence,
                    "height": max(1, y2 - y1),
                }
            )
        if not items:
            return ""
        lines = self._group_paddleocr_lines(items)
        return "\n".join(lines).strip()

    @staticmethod
    def _normalize_box(box: Any) -> tuple[int, int, int, int] | None:
        try:
            values = [int(v) for v in box]
        except Exception:
            return None
        if len(values) == 4:
            return values[0], values[1], values[2], values[3]
        return None

    def _group_paddleocr_lines(self, items: list[dict[str, Any]]) -> list[str]:
        items = sorted(items, key=lambda item: (item["y1"], item["x1"]))
        lines: list[dict[str, Any]] = []
        for item in items:
            center_y = (item["y1"] + item["y2"]) / 2.0
            placed = False
            for line in lines:
                tolerance = max(12.0, min(line["avg_height"], item["height"]) * 0.7)
                if abs(center_y - line["center_y"]) <= tolerance:
                    line["items"].append(item)
                    count = len(line["items"])
                    line["center_y"] = ((line["center_y"] * (count - 1)) + center_y) / count
                    line["avg_height"] = ((line["avg_height"] * (count - 1)) + item["height"]) / count
                    placed = True
                    break
            if not placed:
                lines.append(
                    {
                        "center_y": center_y,
                        "avg_height": float(item["height"]),
                        "items": [item],
                    }
                )
        rendered: list[str] = []
        for line in sorted(lines, key=lambda entry: entry["center_y"]):
            parts = [segment["text"] for segment in sorted(line["items"], key=lambda segment: segment["x1"])]
            text = " ".join(parts).strip()
            if text and text not in rendered:
                rendered.append(text)
        return rendered

    def _resolve_paddle_device(self, configured: str | None) -> str:
        value = str(configured or "auto").strip().lower()
        if value and value != "auto":
            return value
        cuda_visible_devices = os.getenv("CUDA_VISIBLE_DEVICES", "").strip().lower()
        if cuda_visible_devices and cuda_visible_devices not in {"none", "void", "-1"}:
            return "gpu:0"
        nvidia_visible_devices = os.getenv("NVIDIA_VISIBLE_DEVICES", "").strip().lower()
        if nvidia_visible_devices and nvidia_visible_devices not in {"none", "void"}:
            return "gpu:0"
        if Path("/dev/nvidia0").exists() or Path("/dev/dxg").exists():
            return "gpu:0"
        nvidia_smi = shutil.which("nvidia-smi")
        if nvidia_smi:
            try:
                result = subprocess.run(
                    [nvidia_smi, "-L"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0 and "GPU" in str(result.stdout or ""):
                    return "gpu:0"
            except Exception:
                pass
        return "cpu"

    def _load_paddleocr_class(self):
        if self._paddleocr_class is not None:
            return self._paddleocr_class
        try:
            module = importlib.import_module("paddleocr")
        except ImportError:
            return None
        self._paddleocr_class = getattr(module, "PaddleOCR", None)
        return self._paddleocr_class


    @staticmethod
    def _read_float_env(name: str, default: float) -> float:
        raw_value = os.getenv(name)
        if raw_value is None:
            return default
        try:
            return float(raw_value)
        except ValueError:
            return default

    @staticmethod
    def _coerce_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _load_denoise_config(denoise_path: str | Path | None = None) -> dict[str, Any]:
        default = {"ui_terms": [], "period_tokens": [], "min_ui_term_hits": 2}
        path = Path(denoise_path) if denoise_path else project_root() / VIDEO_OCR_DENOISE_PATH
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            logger.warning("OCR 降噪配置加载失败（%s），跳过界面噪声过滤", path, exc_info=True)
            return default
        if not isinstance(data, dict):
            logger.warning("OCR 降噪配置格式不正确（%s），跳过界面噪声过滤", path)
            return default
        ui_terms = [str(term).strip() for term in data.get("ui_terms") or [] if str(term).strip()]
        period_tokens = [str(token).strip() for token in data.get("period_tokens") or [] if str(token).strip()]
        try:
            min_hits = max(1, int(data.get("min_ui_term_hits") or 2))
        except (TypeError, ValueError):
            min_hits = 2
        return {"ui_terms": ui_terms, "period_tokens": period_tokens, "min_ui_term_hits": min_hits}

    def _clean_lines(self, text: str) -> list[str]:
        cleaned = []
        for raw_line in (text or "").splitlines():
            line = " ".join(raw_line.split()).strip()
            if not line:
                continue
            if len(line) == 1 and not line.isdigit():
                continue
            if self._is_ui_noise_line(line):
                continue
            cleaned.append(line)
        return cleaned

    def _is_ui_noise_line(self, line: str) -> bool:
        ui_terms = self.denoise_config["ui_terms"]
        min_hits = self.denoise_config["min_ui_term_hits"]
        hits = {term for term in ui_terms if term in line}
        if len(hits) >= min_hits:
            return True
        period_tokens = set(self.denoise_config["period_tokens"])
        tokens = line.split()
        if len(tokens) >= 2 and all(token in period_tokens or re.fullmatch(r"\d+分钟", token) for token in tokens):
            return True
        return False
