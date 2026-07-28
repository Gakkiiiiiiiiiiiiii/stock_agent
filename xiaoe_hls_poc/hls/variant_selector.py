"""Variant 选择(11.3):best / worst / <height>p / <bandwidth>。"""

from __future__ import annotations

from ..errors import ErrorCode, PocError
from ..models import VariantInfo


def _height(v: VariantInfo) -> int:
    return v.height or 0


def _bandwidth(v: VariantInfo) -> int:
    return v.average_bandwidth or v.bandwidth or 0


def _sort_key(v: VariantInfo) -> tuple[int, int]:
    return (_height(v), _bandwidth(v))


def select_variant(variants: list[VariantInfo], quality: str = "best") -> VariantInfo:
    if not variants:
        raise PocError(ErrorCode.MASTER_NO_VARIANT, "无可用 Variant")
    ordered = sorted(variants, key=_sort_key)
    quality = (quality or "best").strip().lower()

    if quality == "best":
        chosen = ordered[-1]
        chosen.selection_reason = "best: 最高分辨率,同分辨率取最高带宽"
        return chosen
    if quality == "worst":
        chosen = ordered[0]
        chosen.selection_reason = "worst: 最低分辨率/带宽"
        return chosen

    if quality.endswith("p") and quality[:-1].isdigit():
        target = int(quality[:-1])
        exact = [v for v in ordered if v.height == target]
        if exact:
            chosen = exact[-1]
            chosen.selection_reason = f"{target}p: 精确匹配高度"
            return chosen
        below = [v for v in ordered if _height(v) and _height(v) < target]
        if below:
            chosen = below[-1]
            chosen.selection_reason = f"{target}p 不存在: 取不高于 {target} 的最高分辨率"
            return chosen
        chosen = ordered[0]
        chosen.selection_reason = f"全部高于 {target}p: 取最低分辨率(告警)"
        return chosen

    if quality.isdigit():
        target_bw = int(quality)
        # 不小于目标的最小带宽;全部低于目标时取最高带宽
        above = [v for v in ordered if _bandwidth(v) >= target_bw]
        if above:
            chosen = min(above, key=_bandwidth)
            chosen.selection_reason = f"带宽选择: 不小于 {target_bw} 的最小带宽"
        else:
            chosen = ordered[-1]
            chosen.selection_reason = f"全部低于 {target_bw}: 取最高带宽(告警)"
        return chosen

    raise PocError(
        ErrorCode.INPUT_INVALID,
        f"无法识别的 quality: {quality}",
        hint="支持 best / worst / 720p / 1080p / <带宽数值>",
    )
