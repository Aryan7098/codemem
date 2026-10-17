"""Embedding provider protocol, deterministic local embedder, and FalkorDB vector index helpers."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Dict, List, Protocol, Set, Tuple, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for text embedding generators used in hybrid semantic retrieval."""

    @property
    def dimension(self) -> int:
        """Vector dimensionality."""
        ...

    def embed_text(self, text: str) -> List[float]:
        """Generate a normalized float vector for a text string."""
        ...


class DeterministicEmbeddingProvider:
    """
    Zero-dependency local embedding provider that projects text and domain semantic
    clusters into a normalized 64-dimensional vector space.
    """

    # Semantic concept clusters so synonym phrases ("check credentials" vs "verifies bearer tokens")
    # activate shared latent dimensions even when they share zero surface words.
    _SEMANTIC_CLUSTERS: List[Set[str]] = [
        {
            "auth", "authentication", "authenticate", "credentials", "credential",
            "token", "tokens", "bearer", "jwt", "oauth", "oauth2", "login",
            "session", "password", "secret", "identity", "security",
        },
        {
            "check", "checks", "checking", "verify", "verifies", "verified",
            "validate", "validates", "validation", "validator", "tokenvalidator",
            "inspect", "enforce", "guard", "middleware",
        },
        {
            "database", "db", "falkordb", "redis", "graph", "storage", "store",
            "persistence", "pool", "graphpool", "connection", "connections", "query",
        },
        {
            "api", "endpoint", "route", "router", "http", "rest", "mcp",
            "cli", "transport", "handler", "request", "response", "contract",
        },
        {
            "cache", "caching", "ttl", "invalidate", "stale", "freshness",
            "hash", "sha256", "commit", "git", "version", "supersede",
        },
    ]

    def __init__(self, dimension: int = 64) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_text(self, text: str) -> List[float]:
        vec = [0.0] * self._dimension
        tokens = re.findall(r"[a-z0-9_]+", text.lower())
        if not tokens:
            return vec

        # 1. Activate shared semantic cluster dimensions (dims 0..15)
        for token in tokens:
            for cluster_idx, cluster in enumerate(self._SEMANTIC_CLUSTERS):
                if token in cluster or any(c in token for c in cluster if len(c) > 4):
                    dim_primary = (cluster_idx * 3) % 16
                    dim_secondary = (cluster_idx * 3 + 1) % 16
                    vec[dim_primary] += 1.5
                    vec[dim_secondary] += 1.0

        # 2. Feature-hashed lexical & character n-gram dimensions (dims 16..dimension-1)
        lexical_span = max(1, self._dimension - 16)
        for token in tokens:
            if len(token) <= 2:
                continue
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            idx = 16 + (digest[0] % lexical_span)
            sign = 1.0 if (digest[1] % 2 == 0) else -1.0
            vec[idx] += sign * 0.6

        return normalize_vector(vec)


def normalize_vector(vec: List[float]) -> List[float]:
    """L2-normalize a vector."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm < 1e-12:
        return [0.0 for _ in vec]
    return [round(x / norm, 6) for x in vec]


def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
    """Compute cosine similarity in [0.0, 1.0] between two vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a < 1e-12 or norm_b < 1e-12:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_a * norm_b)))


class FalkorDBVectorIndexSpec:
    """Cypher DDL and query builders for FalkorDB `:Memory(embedding)` vector index."""

    @staticmethod
    def create_vector_index_cypher(dimension: int = 64, similarity_function: str = "cosine") -> str:
        """Return the FalkorDB Cypher DDL for creating the Memory embedding vector index."""
        return (
            f"CREATE VECTOR INDEX FOR (m:Memory) ON (m.embedding) "
            f"OPTIONS {{dimension: {dimension}, similarityFunction: '{similarity_function}'}}"
        )

    @staticmethod
    def vector_query_cypher(
        repo_id: str,
        query_embedding: List[float],
        top_k: int = 5,
        include_stale: bool = False,
    ) -> Tuple[str, Dict[str, object]]:
        """Build parameterized FalkorDB vector query restricted to repository scope."""
        stale_filter = "" if include_stale else "AND m.status <> 'STALE' "
        cypher = (
            "CALL db.idx.vector.queryNodes('Memory', 'embedding', $k, vecf32($embedding)) "
            "YIELD node AS m, score "
            "WHERE m.repository_id = $repo_id "
            "AND NOT m.status IN ['SUPERSEDED', 'CONTRADICTED'] "
            f"{stale_filter}"
            "RETURN m, (1.0 - score) AS similarity "
            "ORDER BY similarity DESC LIMIT $top_k"
        )
        params: Dict[str, object] = {
            "repo_id": repo_id,
            "embedding": query_embedding,
            "k": top_k * 2,
            "top_k": top_k,
        }
        return cypher, params
