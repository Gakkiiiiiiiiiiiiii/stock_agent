"""网络捕获编排(8.4):浏览器监听 -> 评分 -> 人工选择 -> 持久化。"""

from __future__ import annotations

from ..auth import browser_auth
from ..errors import ErrorCode, PocError
from ..models import CapturedMediaRequest
from ..security.redactor import redact_url
from . import capture_store
from .candidate_ranker import pick_best, rank_candidates


def run_capture(
    course_url: str,
    profile_name: str,
    *,
    input_fn=input,
    output_fn=print,
) -> CapturedMediaRequest:
    candidates = browser_auth.capture_media_requests(
        course_url, profile_name, input_fn=input_fn, output_fn=output_fn
    )
    best, score, need_confirm = pick_best(candidates, course_url=course_url)

    if need_confirm:
        from ..auth.browser_auth import wait_for_user

        ranked = rank_candidates(candidates, course_url=course_url)
        output_fn("捕获到多个置信度接近的候选,请选择:")
        for i, (cand, s) in enumerate(ranked[:5]):
            output_fn(
                f"  [{i}] score={s:.0f} status={cand.status} {redact_url(cand.url)}"
            )
        choice = wait_for_user(
            profile_name, input_fn=lambda: input_fn("输入序号(默认 0): "),
            output_fn=output_fn,
        ).strip() or "0"
        try:
            idx = int(choice)
            best, score = ranked[idx]
        except (ValueError, IndexError) as exc:
            raise PocError(
                ErrorCode.CAPTURE_MULTIPLE_CANDIDATES, "候选选择无效"
            ) from exc

    capture = capture_store.save_capture(
        best, profile_name=profile_name, score=score, course_url=course_url
    )
    output_fn(f"已保存 capture: {capture.capture_id} ({capture.playlist_url_redacted})")
    return capture
