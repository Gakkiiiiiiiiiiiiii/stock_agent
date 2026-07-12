from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class VideoOcrService:
    def __init__(self, tesseract_bin: str | None = None, language: str | None = None) -> None:
        self.tesseract_bin = tesseract_bin or os.getenv("TESSERACT_BIN", "tesseract")
        self.language = language or os.getenv("VIDEO_OCR_LANGUAGE", "chi_sim+eng")

    def available(self) -> bool:
        if Path(self.tesseract_bin).exists():
            return True
        return shutil.which(self.tesseract_bin) is not None

    def extract_text(self, image_path: str | Path) -> str:
        source = Path(image_path)
        if not source.exists():
            raise FileNotFoundError(source)
        if not self.available():
            return ""
        merged_lines: list[str] = []
        seen: set[str] = set()
        for psm in ("6", "11"):
            text = self._run_ocr(source, psm=psm)
            for line in self._clean_lines(text):
                if line in seen:
                    continue
                seen.add(line)
                merged_lines.append(line)
        return "\n".join(merged_lines).strip()

    def _run_ocr(self, source: Path, psm: str) -> str:
        try:
            result = subprocess.run(
                [
                    self.tesseract_bin,
                    str(source),
                    "stdout",
                    "-l",
                    self.language,
                    "--psm",
                    psm,
                    "-c",
                    "preserve_interword_spaces=1",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            return ""
        return (result.stdout or "").strip()

    @staticmethod
    def _clean_lines(text: str) -> list[str]:
        cleaned = []
        for raw_line in (text or "").splitlines():
            line = " ".join(raw_line.split()).strip()
            if not line:
                continue
            if len(line) == 1 and not line.isdigit():
                continue
            cleaned.append(line)
        return cleaned
