from __future__ import annotations

import hashlib
import math
import os
import re
import subprocess
from pathlib import Path


DEFAULT_VISUAL_CUE_KEYWORDS = (
    "这里",
    "这张图",
    "这个位置",
    "往上看",
    "看这里",
    "图上",
    "画面",
    "K线",
    "均线",
    "缺口",
    "箱体",
    "成交量",
    "分时",
    "表格",
    "PPT",
    "这一页",
    "公告",
    "业绩",
    "净利",
    "利润",
    "营收",
    "订单",
    "预增",
    "同比",
    "财联社",
    "电报",
)


class VideoFrameExtractor:
    def __init__(
        self,
        ffmpeg_bin: str | None = None,
        ffprobe_bin: str | None = None,
        frame_interval_seconds: int | None = None,
        cue_window_seconds: int | None = None,
        max_frames: int | None = None,
        scene_threshold: float | None = None,
        cue_keywords: tuple[str, ...] | None = None,
        subtitle_crop_ratio: float | None = None,
        duplicate_distance_threshold: int | None = None,
        min_frames: int | None = None,
        max_frames_cap: int | None = None,
        target_seconds_per_frame: int | None = None,
    ) -> None:
        self.ffmpeg_bin = ffmpeg_bin or os.getenv("FFMPEG_BIN", "ffmpeg")
        default_ffprobe = "ffprobe.exe" if os.name == "nt" else "ffprobe"
        self.ffprobe_bin = ffprobe_bin or os.getenv("FFPROBE_BIN") or default_ffprobe
        self.frame_interval_seconds = int(os.getenv("VIDEO_FRAME_INTERVAL_SECONDS", str(frame_interval_seconds or 15)))
        self.cue_window_seconds = int(os.getenv("VIDEO_VISUAL_CUE_WINDOW_SECONDS", str(cue_window_seconds or 3)))
        max_frame_default = max_frames_cap or max_frames or 48
        self.min_frames = int(os.getenv("VIDEO_MIN_FRAMES", str(min_frames or 8)))
        self.max_frames_cap = int(os.getenv("VIDEO_MAX_FRAMES", str(max_frame_default)))
        self.target_seconds_per_frame = int(
            os.getenv("VIDEO_TARGET_SECONDS_PER_FRAME", str(target_seconds_per_frame or 75))
        )
        self.scene_threshold = float(os.getenv("VIDEO_SCENE_THRESHOLD", str(scene_threshold or 0.32)))
        self.cue_keywords = cue_keywords or DEFAULT_VISUAL_CUE_KEYWORDS
        self.subtitle_crop_ratio = min(0.45, max(0.0, float(os.getenv("VIDEO_SUBTITLE_CROP_RATIO", str(subtitle_crop_ratio or 0.22)))))
        self.duplicate_distance_threshold = int(
            os.getenv("VIDEO_FRAME_DUPLICATE_DISTANCE_THRESHOLD", str(duplicate_distance_threshold or 6))
        )

    def extract(self, video_path: str | Path, output_dir: str | Path, transcript_segments: list[dict] | None = None) -> list[dict]:
        source = Path(video_path)
        if not source.exists():
            raise FileNotFoundError(source)
        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        duration_ms = self._probe_duration_ms(source)
        frame_budget = self._resolve_frame_budget(duration_ms=duration_ms, transcript_segments=transcript_segments or [])
        timestamps = self._select_timestamps(
            video_path=source,
            duration_ms=duration_ms,
            transcript_segments=transcript_segments or [],
            frame_budget=frame_budget,
        )
        frames: list[dict] = []
        seen_hashes: set[str] = set()
        seen_signatures: list[int] = []
        for index, timestamp_ms in enumerate(timestamps, start=1):
            filename = f"{source.stem}_{timestamp_ms:09d}.jpg"
            image_path = target_dir / filename
            self._extract_single_frame(source, image_path, timestamp_ms)
            if not image_path.exists():
                continue
            digest = self._hash_file(image_path)
            if digest in seen_hashes:
                image_path.unlink(missing_ok=True)
                continue
            signature = self._compute_visual_signature(image_path)
            if signature is not None and any(
                self._hamming_distance(signature, existing) <= self.duplicate_distance_threshold
                for existing in seen_signatures
            ):
                image_path.unlink(missing_ok=True)
                continue
            seen_hashes.add(digest)
            if signature is not None:
                seen_signatures.append(signature)
            frames.append(
                {
                    "frame_index": index,
                    "timestamp_ms": timestamp_ms,
                    "image_path": str(image_path.resolve()),
                    "trigger_source": "cue" if self._is_cue_timestamp(timestamp_ms, transcript_segments or []) else "interval",
                }
            )
        return frames

    def _select_timestamps(self, video_path: Path, duration_ms: int, transcript_segments: list[dict], frame_budget: int) -> list[int]:
        timestamps: list[int] = [0]
        interval_ms = max(1, self.frame_interval_seconds) * 1000
        current = interval_ms
        while current < duration_ms:
            timestamps.append(current)
            current += interval_ms
        if duration_ms > 1000:
            timestamps.append(max(0, duration_ms - 1000))
        scene_timestamps = self._detect_scene_change_timestamps(video_path, limit=frame_budget)

        cue_timestamps: list[int] = []
        for segment in transcript_segments:
            text = str(segment.get("text") or "").strip()
            if not text:
                continue
            normalized = re.sub(r"\s+", "", text)
            if any(keyword in normalized for keyword in self.cue_keywords):
                start_ms = int(segment.get("start_ms") or 0)
                end_ms = int(segment.get("end_ms") or start_ms)
                cue_timestamps.extend(
                    [
                        max(0, start_ms - self.cue_window_seconds * 1000),
                        start_ms,
                        min(duration_ms, end_ms + self.cue_window_seconds * 1000),
                    ]
                )

        prioritized = self._dedupe_sorted(cue_timestamps, tolerance_ms=2000)
        scene_based = self._dedupe_sorted(scene_timestamps, tolerance_ms=2500)
        regular = self._dedupe_sorted(timestamps, tolerance_ms=2000)

        reserve_slots = min(8, max(4, frame_budget // 3))
        coverage_quota = max(self.min_frames, frame_budget - reserve_slots)
        selected = self._sample_evenly(regular, coverage_quota)

        for timestamp_ms in prioritized:
            if len(selected) >= frame_budget:
                break
            if all(abs(timestamp_ms - existing) > 2000 for existing in selected):
                selected.append(timestamp_ms)
        for timestamp_ms in scene_based:
            if len(selected) >= frame_budget:
                break
            if all(abs(timestamp_ms - existing) > 2000 for existing in selected):
                selected.append(timestamp_ms)
        for timestamp_ms in regular:
            if len(selected) >= frame_budget:
                break
            if all(abs(timestamp_ms - existing) > 2000 for existing in selected):
                selected.append(timestamp_ms)
        return sorted(selected)

    def _resolve_frame_budget(self, duration_ms: int, transcript_segments: list[dict]) -> int:
        duration_seconds = max(1, math.ceil(duration_ms / 1000))
        baseline = max(self.min_frames, math.ceil(duration_seconds / max(1, self.target_seconds_per_frame)))
        cue_count = 0
        for segment in transcript_segments:
            text = str(segment.get("text") or "").strip()
            if not text:
                continue
            normalized = re.sub(r"\s+", "", text)
            if any(keyword in normalized for keyword in self.cue_keywords):
                cue_count += 1
        cue_bonus = min(12, math.ceil(cue_count / 2))
        adaptive_cap = max(self.max_frames_cap, baseline)
        budget = baseline + cue_bonus
        return max(self.min_frames, min(adaptive_cap, budget))

    def _is_cue_timestamp(self, timestamp_ms: int, transcript_segments: list[dict]) -> bool:
        for segment in transcript_segments:
            text = str(segment.get("text") or "").strip()
            if not text:
                continue
            normalized = re.sub(r"\s+", "", text)
            if not any(keyword in normalized for keyword in self.cue_keywords):
                continue
            start_ms = int(segment.get("start_ms") or 0)
            end_ms = int(segment.get("end_ms") or start_ms)
            if start_ms - self.cue_window_seconds * 1000 <= timestamp_ms <= end_ms + self.cue_window_seconds * 1000:
                return True
        return False

    def _extract_single_frame(self, video_path: Path, image_path: Path, timestamp_ms: int) -> None:
        seconds = max(0.0, timestamp_ms / 1000.0)
        subprocess.run(
            [
                self.ffmpeg_bin,
                "-y",
                "-ss",
                f"{seconds:.3f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(image_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def _probe_duration_ms(self, video_path: Path) -> int:
        result = subprocess.run(
            [
                self.ffprobe_bin,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        duration_seconds = float((result.stdout or "0").strip() or 0.0)
        return max(0, int(duration_seconds * 1000))

    def _detect_scene_change_timestamps(self, video_path: Path, limit: int) -> list[int]:
        try:
            result = subprocess.run(
                [
                    self.ffmpeg_bin,
                    "-i",
                    str(video_path),
                    "-filter:v",
                    f"select='gt(scene,{self.scene_threshold})',metadata=print",
                    "-an",
                    "-f",
                    "null",
                    "-",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            return []
        output = "\n".join([result.stdout or "", result.stderr or ""])
        timestamps: list[int] = []
        for match in re.finditer(r"pts_time:(\d+(?:\.\d+)?)", output):
            timestamps.append(int(float(match.group(1)) * 1000))
        return timestamps[: max(1, limit)]

    @staticmethod
    def _dedupe_sorted(timestamps: list[int], tolerance_ms: int) -> list[int]:
        cleaned = sorted(set(int(item) for item in timestamps if int(item) >= 0))
        selected: list[int] = []
        for timestamp_ms in cleaned:
            if not selected or timestamp_ms - selected[-1] > tolerance_ms:
                selected.append(timestamp_ms)
        return selected

    @staticmethod
    def _sample_evenly(timestamps: list[int], limit: int) -> list[int]:
        cleaned = sorted(set(int(item) for item in timestamps if int(item) >= 0))
        if limit <= 0 or not cleaned:
            return []
        if len(cleaned) <= limit:
            return cleaned
        if limit == 1:
            return [cleaned[0]]
        result: list[int] = []
        last_index = len(cleaned) - 1
        for slot in range(limit):
            index = round(slot * last_index / (limit - 1))
            result.append(cleaned[index])
        return sorted(set(result))

    @staticmethod
    def _hash_file(path: Path) -> str:
        return hashlib.sha1(path.read_bytes()).hexdigest()

    def _compute_visual_signature(self, image_path: Path) -> int | None:
        crop_height_expr = max(0.0, 1.0 - self.subtitle_crop_ratio)
        filter_chain = f"crop=iw:ih*{crop_height_expr:.4f}:0:0,scale=8:8,format=gray"
        try:
            result = subprocess.run(
                [
                    self.ffmpeg_bin,
                    "-v",
                    "error",
                    "-i",
                    str(image_path),
                    "-vf",
                    filter_chain,
                    "-frames:v",
                    "1",
                    "-f",
                    "rawvideo",
                    "-",
                ],
                check=True,
                capture_output=True,
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        pixels = result.stdout or b""
        if len(pixels) < 64:
            return None
        sample = pixels[:64]
        average = sum(sample) / len(sample)
        signature = 0
        for pixel in sample:
            signature = (signature << 1) | int(pixel >= average)
        return signature

    @staticmethod
    def _hamming_distance(left: int, right: int) -> int:
        return (left ^ right).bit_count()
