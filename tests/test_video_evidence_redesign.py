from engines.content.claim_evidence_verifier import ClaimEvidenceVerifier
from engines.content.cross_video_corroboration import CrossVideoCorroboration
from engines.content.external_fact_verifier import ExternalFactVerifier
from engines.content.knowledge_unit_extractor import KnowledgeUnitExtractor
from engines.content.knowledge_unit_normalizer import KnowledgeUnitNormalizer
from engines.content.temporal_window_builder import TemporalWindowBuilder
from engines.content.transcript_postprocessor import TranscriptPostprocessor
from engines.content.video_ocr_service import VideoOcrService
from storage.repositories.knowledge_repository import KnowledgeVectorTaskService


def test_missing_evidence_confidence_remains_unknown():
    window = TemporalWindowBuilder().build({"segments": [{"start_ms": 0, "end_ms": 1000, "text": "测试", "confidence_score": None}]})[0]
    assert window["confidence_score"] is None


def test_unresolved_evidence_reference_is_not_bound_to_first_window():
    assert KnowledgeUnitExtractor._find_evidence_window([{"window_index": 0, "transcript_text": "第一段"}], "window_99", "不存在") is None


def test_postprocessor_keeps_raw_text_and_correction_trace(tmp_path):
    corrections = tmp_path / "corrections.yaml"
    corrections.write_text("corrections:\n  适应率: 市盈率\n", encoding="utf-8")
    segment = TranscriptPostprocessor(corrections_path=corrections).normalize({"segments": [{"text": "适应率十倍"}]})["segments"][0]
    assert segment["raw_text"] == "适应率十倍"
    assert segment["normalized_text"] == "市盈率十倍"
    assert segment["correction_trace"]


def test_semantic_verifier_rejects_entity_number_cross_binding():
    result = ClaimEvidenceVerifier().verify({"statement": "B公司上涨20%。", "subject_name": "B公司", "evidence": [{"is_primary": True, "start_ms": 0, "end_ms": 1000, "raw_text": "A公司上涨20%，B公司下跌30%。"}]})
    assert result["support_status"] == "NEEDS_REVIEW"
    assert "DIRECTION_MATCH_FAILED" in result["reason_codes"]


def test_normalizer_uses_source_located_not_source_confirmed_by_default():
    unit = {"chapter_index": 0, "statement": "黄金主题仍有催化。", "subject_key": "黄金", "subject_name": "黄金", "entities": [], "evidence": [{"is_primary": True, "start_ms": 0, "end_ms": 1000, "raw_text": "黄金主题仍有催化。", "evidence_text": "黄金主题仍有催化。", "confidence_score": 0.9}]}
    result = KnowledgeUnitNormalizer().normalize([unit], {"bvid": "BVTEST"})[0]
    assert result["verification_status"] in {"SOURCE_LOCATED", "SOURCE_SUPPORTED"}
    assert result["verification_status"] != "SOURCE_CONFIRMED"


def test_ocr_evidence_preserves_blocks_and_bbox(monkeypatch, tmp_path):
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"frame")
    service = VideoOcrService(backend="paddleocr")
    monkeypatch.setattr(service, "_paddleocr_available", lambda: True)
    monkeypatch.setattr(service, "_get_paddleocr_engine", lambda: type("Engine", (), {"predict": lambda self, _: [{"rec_texts": ["100.2"], "rec_scores": [0.99], "rec_boxes": [[1, 2, 30, 20]]}]})())
    evidence = service.extract_evidence(image)
    assert evidence["blocks"][0]["score"] == 0.99
    assert evidence["table"][0][0]["bbox"] == [1, 2, 30, 20]


def test_external_verifier_never_promotes_without_provider():
    assert ExternalFactVerifier().verify_many([{"knowledge_kind": "FACT"}])[0]["truth_status"] == "NOT_EXTERNALLY_VERIFIED"


def test_cross_video_corroboration_is_narrative_not_truth():
    unit = {"subject_key": "储能", "predicate_key": "景气", "source_video_id": 2, "support_status": "SOURCE_SUPPORTED", "attributes": {}}
    result = CrossVideoCorroboration().annotate([unit], [unit, unit | {"source_video_id": 3}])[0]
    assert result["attributes"]["cross_video_corroboration"]["does_not_verify_truth"] is True


def test_vector_gate_requires_semantically_supported_knowledge():
    assert not KnowledgeVectorTaskService.is_indexable({"lifecycle_status": "ACTIVE", "support_status": "SOURCE_LOCATED"})
    assert KnowledgeVectorTaskService.is_indexable({"lifecycle_status": "ACTIVE", "support_status": "SOURCE_SUPPORTED"})
