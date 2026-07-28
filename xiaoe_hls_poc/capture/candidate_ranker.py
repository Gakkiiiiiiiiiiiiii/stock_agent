"""候选评分与选择(10.5 评分表):去重、打分、置信度判断。"""

from __future__ import annotations

from urllib.parse import urlsplit

from ..errors import ErrorCode, PocError
from ..security.redactor import path_fingerprint
from .candidate_detector import RawCandidate

# 10.5 评分表
SCORE_STATUS_OK = 30
SCORE_BODY_EXTM3U = 30
SCORE_CONTENT_TYPE = 20
SCORE_URL_M3U8 = 10
SCORE_SAME_ORIGIN = 10
SCORE_HAS_PLAYLIST_TAGS = 20
SCORE_PREVIEW_SHORT = -20
SCORE_STATUS_401_403 = -50
SCORE_ERROR_PAGE = -100

PREVIEW_DURATION_THRESHOLD = 120.0  # 秒,低于此视为试看候选
CONFIDENCE_GAP = 15.0  # 前两名分差小于该值时需要人工选择


def _same_origin(url_a: str, url_b: str) -> bool:
    if not url_a or not url_b:
        return False
    a, b = urlsplit(url_a), urlsplit(url_b)
    return a.hostname == b.hostname


def score_candidate(cand: RawCandidate, *, course_url: str = "") -> float:
    score = 0.0
    if cand.status in (200, 206):
        score += SCORE_STATUS_OK
    elif cand.status in (401, 403):
        score += SCORE_STATUS_401_403
    if cand.body_extm3u:
        score += SCORE_BODY_EXTM3U
    if "mpegurl" in (cand.content_type or ""):
        score += SCORE_CONTENT_TYPE
    if ".m3u8" in cand.url.lower():
        score += SCORE_URL_M3U8
    if _same_origin(cand.page_url or course_url, cand.url):
        score += SCORE_SAME_ORIGIN
    if cand.has_stream_inf or cand.has_extinf:
        score += SCORE_HAS_PLAYLIST_TAGS
    if cand.total_duration is not None and 0 < cand.total_duration < PREVIEW_DURATION_THRESHOLD:
        score += SCORE_PREVIEW_SHORT
    if cand.body_is_error_page:
        score += SCORE_ERROR_PAGE
    return score


def dedupe_candidates(cands: list[RawCandidate]) -> list[RawCandidate]:
    """按完整 URL 去重;同一路径不同签名只保留最新成功响应(10.5)。"""
    by_path: dict[str, RawCandidate] = {}
    for c in cands:
        key = path_fingerprint(c.url)
        prev = by_path.get(key)
        if prev is None:
            by_path[key] = c
            continue
        # 保留成功响应优先,其次后到的
        prev_ok = prev.status in (200, 206)
        cur_ok = c.status in (200, 206)
        if cur_ok and not prev_ok:
            by_path[key] = c
        elif cur_ok == prev_ok:
            by_path[key] = c  # 后到的新签名覆盖
    return list(by_path.values())


def rank_candidates(
    cands: list[RawCandidate], *, course_url: str = ""
) -> list[tuple[RawCandidate, float]]:
    deduped = dedupe_candidates(cands)
    scored = [(c, score_candidate(c, course_url=course_url)) for c in deduped]
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored


def pick_best(
    cands: list[RawCandidate], *, course_url: str = ""
) -> tuple[RawCandidate, float, bool]:
    """返回 (候选, 分数, 是否需人工确认)。无可信候选抛错。"""
    ranked = rank_candidates(cands, course_url=course_url)
    if not ranked:
        raise PocError(
            ErrorCode.CAPTURE_NO_MEDIA_REQUEST,
            "未捕获到 M3U8 请求",
            hint="确认已在浏览器中播放视频;若内容非 HLS 则本工具不适用",
        )
    best, best_score = ranked[0]
    if best.status in (401, 403) or best.body_is_error_page or not (
        best.body_extm3u or best.has_extinf or best.has_stream_inf or ".m3u8" in best.url.lower()
    ):
        raise PocError(
            ErrorCode.CAPTURE_RESPONSE_INVALID,
            "最佳候选不是合法 M3U8(错误页/未授权/缺少播放列表特征)",
        )
    need_confirm = len(ranked) > 1 and (best_score - ranked[1][1]) < CONFIDENCE_GAP
    return best, best_score, need_confirm
