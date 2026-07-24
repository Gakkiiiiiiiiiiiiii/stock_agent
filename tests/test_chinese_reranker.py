from workers.reranker_api import RerankRequest, rerank


def test_chinese_reranker_handles_no_space_synonyms():
    result = rerank(
        RerankRequest(
            query="可灵视频模型",
            candidates=[
                {"chunk_id": "1", "text": "快手 AI 视频生成产品可灵正在推进商业化", "payload": {"status": "approved"}},
                {"chunk_id": "2", "text": "煤炭价格和高股息策略讨论", "payload": {}},
            ],
            top_k=2,
        )
    )
    assert result["reranked"][0]["chunk_id"] == "1"
