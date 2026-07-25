from __future__ import annotations

import math
from collections import Counter


class SparseBM25Scorer:
    def score_candidates(self, query: str, candidates: list[dict]) -> list[dict]:
        docs = [_tokenize(item.get("text") or (item.get("payload") or {}).get("text") or "") for item in candidates]
        query_terms = _tokenize(query)
        if not candidates or not query_terms:
            return [item | {"bm25_score": 0.0, "sparse_score_source": "bm25_empty"} for item in candidates]
        avgdl = sum(len(doc) for doc in docs) / max(len(docs), 1)
        df = Counter(term for doc in docs for term in set(doc))
        scored = []
        for item, doc in zip(candidates, docs, strict=False):
            tf = Counter(doc)
            score = 0.0
            for term in query_terms:
                if tf[term] <= 0:
                    continue
                idf = math.log(1 + (len(docs) - df[term] + 0.5) / (df[term] + 0.5))
                denom = tf[term] + 1.2 * (1 - 0.75 + 0.75 * len(doc) / max(avgdl, 1e-9))
                score += idf * (tf[term] * 2.2) / denom
            scored.append(item | {"bm25_score": round(score, 6), "sparse_score_source": "bm25_candidate_text"})
        return scored


def _tokenize(text: str) -> list[str]:
    compact = "".join(str(text or "").lower().split())
    words = str(text or "").lower().split()
    chars = list(compact)
    bigrams = [compact[i : i + 2] for i in range(max(len(compact) - 1, 0))]
    return words + chars + bigrams
