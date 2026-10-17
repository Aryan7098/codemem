"""Backend factory for switching between InMemoryGraphStore and FalkorDBGraphStore."""

from __future__ import annotations

import os
from typing import Optional

from codemem.graph.falkordb_store import FalkorDBGraphStore
from codemem.graph.in_memory import InMemoryGraphStore
from codemem.graph.protocol import GraphStoreProtocol


def get_backend_name(explicit_backend: Optional[str] = None) -> str:
    """Resolve active backend name from argument or CODEMEM_BACKEND env var."""
    return (explicit_backend or os.environ.get("CODEMEM_BACKEND", "memory")).strip().lower()


def create_graph_store(explicit_backend: Optional[str] = None) -> GraphStoreProtocol:
    """
    Instantiate the configured GraphStoreProtocol backend:
      - CODEMEM_BACKEND=memory   -> InMemoryGraphStore (default)
      - CODEMEM_BACKEND=falkordb -> FalkorDBGraphStore (connects to FALKORDB_HOST:FALKORDB_PORT)
    """
    backend = get_backend_name(explicit_backend)
    if backend == "falkordb":
        return FalkorDBGraphStore()
    if backend == "memory":
        return InMemoryGraphStore()
    raise ValueError(f"Unsupported CODEMEM_BACKEND='{backend}'. Expected 'memory' or 'falkordb'.")
