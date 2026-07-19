import pytest

from engines.content.transcript_postprocessor import TranscriptPostprocessor


def _make_transcript(text: str) -> dict:
    return {
        "text": text,
        "segments": [{"start_ms": 0, "end_ms": 1000, "text": text}],
    }


def test_normalize_converts_traditional_to_simplified():
    postprocessor = TranscriptPostprocessor()
    if postprocessor._get_opencc_converter() is None:
        pytest.skip("opencc 未安装")

    result = postprocessor.normalize(_make_transcript("歡迎收看鷹眼看盤，槓桿資金面臨平倉壓力。"))

    assert result["segments"][0]["text"].startswith("欢迎收看鹰眼看盘")
    assert "杠杆" in result["segments"][0]["text"]


def test_normalize_applies_term_corrections():
    postprocessor = TranscriptPostprocessor()

    result = postprocessor.normalize(_make_transcript("军量线没有站上，创业版继续调整。"))

    text = result["segments"][0]["text"]
    assert "均量线" in text
    assert "创业板" in text


def test_normalize_works_without_opencc(monkeypatch):
    postprocessor = TranscriptPostprocessor()
    monkeypatch.setattr(postprocessor, "_get_opencc_converter", lambda: None)

    result = postprocessor.normalize(_make_transcript("军量线没有站上。"))

    assert "均量线" in result["segments"][0]["text"]
