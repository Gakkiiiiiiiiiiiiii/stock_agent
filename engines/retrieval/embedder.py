from __future__ import annotations

from hashlib import sha256


class DeterministicEmbedder:
    def __init__(self, vector_size: int = 64) -> None:
        self.vector_size = vector_size

    def embed(self, text: str) -> list[float]:
        digest = sha256(text.encode("utf-8")).digest()
        values = list(digest) * ((self.vector_size // len(digest)) + 1)
        return [round(value / 255.0, 6) for value in values[: self.vector_size]]

