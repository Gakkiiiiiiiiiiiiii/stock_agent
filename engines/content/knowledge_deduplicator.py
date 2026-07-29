from __future__ import annotations


class KnowledgeDeduplicator:
    def deduplicate(self, units: list[dict]) -> list[dict]:
        seen: dict[str, dict] = {}
        for unit in units:
            key = str(unit.get("content_hash") or unit.get("semantic_hash") or unit.get("statement"))
            if key not in seen:
                seen[key] = unit
                continue
            existing = seen[key]
            existing_evidence = existing.setdefault("evidence", [])
            for evidence in unit.get("evidence") or []:
                if evidence not in existing_evidence:
                    existing_evidence.append(evidence)
            existing["extraction_confidence"] = max(float(existing.get("extraction_confidence") or 0), float(unit.get("extraction_confidence") or 0))
        return list(seen.values())
