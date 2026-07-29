from __future__ import annotations

import hashlib


class KnowledgeConflictResolver:
    NEGATIVE = {"BEARISH"}
    POSITIVE = {"BULLISH"}

    def resolve(self, units: list[dict]) -> tuple[list[dict], list[dict]]:
        by_key: dict[str, list[dict]] = {}
        for unit in units:
            key = str(unit.get("conflict_key") or "")
            if not key:
                continue
            by_key.setdefault(key, []).append(unit)

        relations: list[dict] = []
        for key, group in by_key.items():
            if len(group) <= 1:
                continue
            group_id = "kcg_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
            for unit in group:
                unit["conflict_group_id"] = group_id
            ordered = sorted(group, key=lambda item: (item.get("as_of_time") is not None, item.get("as_of_time")), reverse=True)
            latest = ordered[0]
            for older in ordered[1:]:
                if self._contradicts(latest, older):
                    older["lifecycle_status"] = "SUPERSEDED"
                    relations.append(
                        {
                            "source_uid": latest.get("knowledge_uid"),
                            "target_uid": older.get("knowledge_uid"),
                            "relation_type": "SUPERSEDES",
                            "confidence_score": 0.72,
                            "attributes": {"reason": "same_conflict_key_newer_opposite_sentiment"},
                        }
                    )
                else:
                    relations.append(
                        {
                            "source_uid": latest.get("knowledge_uid"),
                            "target_uid": older.get("knowledge_uid"),
                            "relation_type": "REINFORCES",
                            "confidence_score": 0.62,
                            "attributes": {"reason": "same_conflict_key_same_direction"},
                        }
                    )
        return units, relations

    @classmethod
    def _contradicts(cls, left: dict, right: dict) -> bool:
        left_sentiment = str(left.get("sentiment") or "")
        right_sentiment = str(right.get("sentiment") or "")
        return (left_sentiment in cls.POSITIVE and right_sentiment in cls.NEGATIVE) or (left_sentiment in cls.NEGATIVE and right_sentiment in cls.POSITIVE)
