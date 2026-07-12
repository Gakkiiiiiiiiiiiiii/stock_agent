from __future__ import annotations

from hashlib import sha256


def chunk_text(text: str, chunk_size: int = 300) -> list[dict]:
    cleaned = " ".join(text.split())
    if not cleaned:
        return []
    chunks = []
    for index, start in enumerate(range(0, len(cleaned), chunk_size), start=1):
        chunk_text_value = cleaned[start : start + chunk_size]
        chunks.append(
            {
                "chunk_id": f"chunk_{index:03d}",
                "text": chunk_text_value,
                "content_hash": sha256(chunk_text_value.encode("utf-8")).hexdigest(),
            }
        )
    return chunks

