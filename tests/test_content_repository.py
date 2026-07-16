from __future__ import annotations

from storage.repositories.content_repository import _truncate_text


def test_truncate_text_respects_limit():
    assert _truncate_text("abcdef", 4) == "abc…"
    assert _truncate_text("abc", 4) == "abc"
    assert _truncate_text("", 4) is None
    assert _truncate_text(None, 4) is None
