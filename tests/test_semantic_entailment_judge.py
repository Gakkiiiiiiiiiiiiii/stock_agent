"""Semantic Entailment Judge 生产接入验收测试（最终收敛设计文档 §4，剩余项一）。

覆盖：
- judge 三标签正常路径（fake model_client 返回对应 JSON）；
- 非法 label / 坏 JSON / 模型不可用 / 调用异常的安全降级；
- Stage A 硬失败不可被 judge 升级（§16.2）；
- 基础设施失败（JUDGE_UNAVAILABLE 等）按弃权处理，不降级；
- VideoIngestService 生产链路：checks.judge 非空且 Verification Ledger
  记录真实 judge 的 provider/model/version，而不是 deterministic/v1。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from engines.content.claim_evidence_verifier import ClaimEvidenceVerifier
from engines.content.knowledge_unit_normalizer import KnowledgeUnitNormalizer
from engines.content.semantic_entailment_judge import JUDGE_VERSION, SemanticEntailmentJudge
from engines.content.video_ingest_service import VideoIngestService
from tests.test_content_service import (
    FakeAnalysisDocumentModel,
    FakeAudioPipeline,
    FakeBilibiliClient,
    FakeFrameExtractor,
)
from tests.test_full_ingest_evidence_fidelity import (
    FidelityAsrService,
    FidelityKnowledgeModel,
    FidelityVisionService,
)


class FakeModelClient:
    """可编程的 AnalysisModelClient fake。"""

    def __init__(self, content: str | None = None, *, available: bool = True, raises: bool = False) -> None:
        self._content = content
        self._available = available
        self._raises = raises
        self.settings = SimpleNamespace(provider="fake-provider", model="fake-model")
        self.calls: list[dict] = []

    def available(self) -> bool:
        return self._available

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises:
            raise RuntimeError("fake model outage")
        return {"provider": "fake-provider", "model": "fake-model", "content": self._content}


def _payload() -> dict:
    return {
        "claim": "黄金价格今年上涨20%。",
        "evidence": "黄金价格今年上涨20%。",
        "structured_checks": {"number_match": True, "entity_match": True},
    }


def test_semantic_judge_supported():
    client = FakeModelClient('{"label": "SUPPORTED", "score": 0.94, "reason_codes": []}')
    verdict = SemanticEntailmentJudge(client)(_payload())
    assert verdict["label"] == "SUPPORTED"
    assert verdict["score"] == 0.94
    assert verdict["provider"] == "fake-provider"
    assert verdict["model"] == "fake-model"
    assert verdict["version"] == JUDGE_VERSION
    # temperature=0 + JSON mode（§4）。
    assert client.calls[0]["temperature"] == 0.0
    assert client.calls[0]["response_format"] == {"type": "json_object"}


def test_semantic_judge_contradicted():
    client = FakeModelClient('{"label": "CONTRADICTED", "score": 0.9, "reason_codes": ["NLI_CONTRADICTION"]}')
    verdict = SemanticEntailmentJudge(client)(_payload())
    assert verdict["label"] == "CONTRADICTED"
    assert verdict["reason_codes"] == ["NLI_CONTRADICTION"]


def test_semantic_judge_not_enough_evidence():
    client = FakeModelClient('{"label": "NOT_ENOUGH_EVIDENCE", "score": 0.3, "reason_codes": []}')
    verdict = SemanticEntailmentJudge(client)(_payload())
    assert verdict["label"] == "NOT_ENOUGH_EVIDENCE"
    assert "JUDGE" not in " ".join(verdict["reason_codes"])


def test_semantic_judge_invalid_label_falls_back():
    client = FakeModelClient('{"label": "MAYBE", "score": 0.9, "reason_codes": []}')
    verdict = SemanticEntailmentJudge(client)(_payload())
    assert verdict["label"] == "NOT_ENOUGH_EVIDENCE"
    assert verdict["score"] == 0.0
    assert "JUDGE_LABEL_INVALID" in verdict["reason_codes"]


def test_semantic_judge_bad_json_falls_back():
    client = FakeModelClient("这不是 JSON")
    verdict = SemanticEntailmentJudge(client)(_payload())
    assert verdict["label"] == "NOT_ENOUGH_EVIDENCE"
    assert verdict["score"] == 0.0
    assert "JUDGE_ERROR" in verdict["reason_codes"]


def test_semantic_judge_unavailable_falls_back():
    client = FakeModelClient(available=False)
    verdict = SemanticEntailmentJudge(client)(_payload())
    assert verdict["label"] == "NOT_ENOUGH_EVIDENCE"
    assert verdict["score"] == 0.0
    assert "JUDGE_UNAVAILABLE" in verdict["reason_codes"]
    assert client.calls == []  # 不可用时不发起调用


def test_semantic_judge_exception_falls_back():
    client = FakeModelClient(raises=True)
    verdict = SemanticEntailmentJudge(client)(_payload())
    assert verdict["label"] == "NOT_ENOUGH_EVIDENCE"
    assert "JUDGE_ERROR" in verdict["reason_codes"]


def test_semantic_judge_score_clamped():
    client = FakeModelClient('{"label": "SUPPORTED", "score": 3.7, "reason_codes": []}')
    verdict = SemanticEntailmentJudge(client)(_payload())
    assert verdict["score"] == 1.0


def _unit(statement, raw_text, **overrides):
    unit = {
        "statement": statement,
        "evidence": [{"is_primary": True, "start_ms": 0, "end_ms": 1000, "raw_text": raw_text}],
    }
    unit.update(overrides)
    return unit


def test_stage_a_hard_failure_not_upgraded_by_real_judge():
    judge = SemanticEntailmentJudge(FakeModelClient('{"label": "SUPPORTED", "score": 1.0, "reason_codes": []}'))
    result = ClaimEvidenceVerifier(judge=judge).verify(
        _unit("B公司利润增长20%。", "A公司利润增长20%，B公司营收增长30%。", subject_name="B公司")
    )
    assert "NUMBER_MATCH_FAILED" in result["reason_codes"]
    assert result["support_status"] != "SOURCE_SUPPORTED"
    assert result["checks"]["judge"]["label"] == "SUPPORTED"
    # judge 元数据随 verification 流出，供 ledger 入账。
    assert result["judge"]["provider"] == "fake-provider"
    assert result["judge"]["version"] == JUDGE_VERSION


def test_infra_failure_abstains_instead_of_downgrading():
    judge = SemanticEntailmentJudge(FakeModelClient(available=False))
    result = ClaimEvidenceVerifier(judge=judge).verify(
        _unit("黄金价格今年上涨20%。", "黄金价格今年上涨20%。", subject_name="黄金")
    )
    # 裁判自身失效不等于证据不足：不降级，仅记录弃权 reason。
    assert result["support_status"] == "SOURCE_SUPPORTED"
    assert "JUDGE_UNAVAILABLE" in result["checks"]["judge"]["reason_codes"]


def test_genuine_not_enough_evidence_downgrades():
    judge = SemanticEntailmentJudge(FakeModelClient('{"label": "NOT_ENOUGH_EVIDENCE", "score": 0.4, "reason_codes": []}'))
    result = ClaimEvidenceVerifier(judge=judge).verify(
        _unit("黄金价格今年上涨20%。", "黄金价格今年上涨20%。", subject_name="黄金")
    )
    assert result["support_status"] == "NEEDS_REVIEW"
    assert "JUDGE_NOT_ENOUGH_EVIDENCE" in result["reason_codes"]


class JudgeAwareModel:
    """同时服务知识抽取与语义裁判的 fake model client。"""

    def __init__(self) -> None:
        self.judge_calls = 0

    def available(self) -> bool:
        return True

    def complete(self, **kwargs):
        system = str(kwargs.get("system") or "")
        if "语义一致性裁判" in system:
            self.judge_calls += 1
            return {
                "provider": "fake-judge",
                "model": "fake-judge-k3",
                "content": '{"label": "SUPPORTED", "score": 0.95, "reason_codes": []}',
            }
        return FidelityKnowledgeModel().complete(**kwargs)


def test_video_ingest_uses_semantic_judge(monkeypatch):
    from sqlalchemy import create_engine

    import storage.models.content  # noqa: F401
    import storage.models.knowledge  # noqa: F401
    import storage.models.vector  # noqa: F401
    from storage.db import Base, SessionLocal

    temp_root = Path("D:/project/stock_agent/.pytest-tmp")
    temp_root.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(tempfile.mkdtemp(prefix="semantic-judge-ingest-", dir=temp_root))
    engine = create_engine(
        f"sqlite:///{tmp_path / 'semantic_judge.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    SessionLocal.configure(bind=engine)
    Base.metadata.create_all(bind=engine)

    monkeypatch.setattr(
        "engines.content.video_ingest_service.write_memory_and_enqueue",
        lambda payload, target_collection="financial_knowledge", existing_memory_id=None: {
            "memory_id": 1,
            "task_id": 1,
            "target_collection": target_collection,
        },
    )
    monkeypatch.setattr(
        "engines.content.video_ingest_service.enqueue_memory_reindex",
        lambda memory_id, target_collection="financial_knowledge": {"memory_id": memory_id, "task_id": 2, "target_collection": target_collection},
    )

    fake_model = JudgeAwareModel()
    service = VideoIngestService(
        bilibili_client=FakeBilibiliClient(tmp_path),
        audio_pipeline=FakeAudioPipeline(),
        asr_service=FidelityAsrService(),
        frame_extractor=FakeFrameExtractor(),
        vision_service=FidelityVisionService(),
        storage_root=tmp_path / "content_storage",
    )
    service.knowledge_extractor.model_client = fake_model
    service.knowledge_normalizer = KnowledgeUnitNormalizer(
        entity_normalizer=service.entity_normalizer,
        verifier=ClaimEvidenceVerifier(judge=SemanticEntailmentJudge(fake_model)),
    )
    service.analysis_document_generator.model_client = FakeAnalysisDocumentModel()

    try:
        queued = service.enqueue_bilibili(url="https://www.bilibili.com/video/BVJUDGE123")
        detail = service.process_task(queued["task_id"])
        assert detail["task"]["status"] == "success"
        assert fake_model.judge_calls >= 1

        units = detail["knowledge_units"]
        assert units
        judged = [
            unit
            for unit in units
            if ((unit.get("attributes") or {}).get("verification") or {}).get("checks", {}).get("judge")
        ]
        assert judged, "参与语义验证的 unit 的 attributes.verification.checks.judge 不应为空"
        for unit in judged:
            verification = unit["attributes"]["verification"]
            assert verification["checks"]["judge"]["label"] == "SUPPORTED"
            assert verification["judge"]["provider"] == "fake-judge"

            ledger = [
                row
                for row in service.knowledge_repo.list_verifications(unit["id"])
                if row["verifier_type"] == "claim_evidence_semantic"
            ]
            assert ledger, "claim_evidence_semantic ledger 行应存在"
            for row in ledger:
                assert row["verifier_provider"] == "fake-judge"
                assert row["verifier_model"] == "fake-judge-k3"
                assert row["verifier_version"] == JUDGE_VERSION
    finally:
        shutil.rmtree(tmp_path, ignore_errors=True)
