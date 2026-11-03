"""Turning text into vectors for the assistant's semantic search.

* `azure_openai`: an Azure OpenAI embeddings deployment (for example text-embedding-3-small).
* `mock`: a deterministic word-hashing embedder for tests and development without a key. It only
  matches shared words, so it is never presented as semantic search.
* `none` (default): no vectors; the assistant searches with PostgreSQL full-text search only.

Only LaneReach's own help text and the person's question are sent to the embeddings service,
never contact or email data.
"""

from __future__ import annotations

import hashlib
import math
import re
from itertools import pairwise
from typing import Protocol

from lanereach.core.config import Settings

DIMENSIONS = 1536
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "my",
        "of",
        "on",
        "or",
        "so",
        "that",
        "the",
        "this",
        "to",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
    ]
)


class EmbeddingsProvider(Protocol):
    """Embeds texts into `DIMENSIONS`-long vectors."""

    name: str
    model: str

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per text, in order."""
        ...


def _tokens(text: str) -> list[str]:
    words = [word for word in _WORD.findall(text.lower()) if word not in _STOP]
    return words + [f"{first}_{second}" for first, second in pairwise(words)]


class HashingEmbeddings:
    """Deterministic bag-of-words vectors (feature hashing, L2-normalised)."""

    name = "mock"
    model = "hashing-1536"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Hash every word and word pair into a fixed-size vector."""
        return [self._one(text) for text in texts]

    @staticmethod
    def _one(text: str) -> list[float]:
        vector = [0.0] * DIMENSIONS
        for token in _tokens(text):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % DIMENSIONS
            vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class AzureOpenAIEmbeddings:
    """Embeddings from an Azure OpenAI deployment.

    Args:
        settings: Endpoint, key, API version and the embeddings deployment name.
    """

    name = "azure_openai"

    def __init__(self, settings: Settings) -> None:
        import openai

        key = settings.azure_openai_api_key
        self.model = settings.azure_openai_embedding_deployment
        self._client = openai.AsyncAzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=key.get_secret_value() if key else "",
            api_version=settings.azure_openai_api_version,
            timeout=settings.llm_timeout_seconds,
            max_retries=2,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Call the deployment in batches of 64 texts."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 64):
            batch = texts[start : start + 64]
            response = await self._client.embeddings.create(
                model=self.model, input=batch, dimensions=DIMENSIONS
            )
            vectors.extend(
                list(item.embedding) for item in sorted(response.data, key=lambda row: row.index)
            )
        return vectors


def build_embeddings(settings: Settings) -> EmbeddingsProvider | None:
    """The configured embeddings provider, or None for full-text search only."""
    if settings.embeddings_provider == "azure_openai":
        if not (settings.azure_openai_endpoint and settings.azure_openai_embedding_deployment):
            return None
        return AzureOpenAIEmbeddings(settings)
    if settings.embeddings_provider == "mock":
        return HashingEmbeddings()
    return None
