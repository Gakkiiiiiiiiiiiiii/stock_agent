from __future__ import annotations


def extract_memory(title: str, content: str, memory_type: str = "strategy_experience_memory") -> dict:
    return {
        "memory_type": memory_type,
        "title": title,
        "content": content.strip(),
        "confidence": 0.72,
        "importance": "medium",
        "status": "validated",
    }

