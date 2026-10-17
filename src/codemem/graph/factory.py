"""Storage backend resolution for CodeMem CLI and MCP transports."""

from __future__ import annotations

import os
from typing import Optional

from codemem.graph.adapter import FalkorDBAdapter
from codemem.graph.in_memory import InMemoryGraphStore
from codemem.graph.protocol import GraphStoreProtocol


def resolve_backend_name(backend: Optional[str] = None) -> str:
    """Return normalized backend name ('memory' or 'falkordb')."""
    raw = (backend or os.environ.get("CODEMEM_BACKEND", "memory")).strip().lower()
    if raw not in ("memory", "falkordb"):
        raise ValueError(f"Unsupported backend '{raw}'. Expected 'memory' or 'falkordb'.")
    return raw


get_backend_name = resolve_backend_name


def create_graph_store(
    backend: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    graph_name: Optional[str] = None,
) -> GraphStoreProtocol:
    """
    Instantiate GraphStoreProtocol based on explicit parameters or environment variables:
      - CODEMEM_BACKEND ('memory' | 'falkordb')
      - FALKORDB_HOST (default 'localhost')
      - FALKORDB_PORT (default 6379)
      - FALKORDB_GRAPH (default 'codemem')
    """
    resolved = resolve_backend_name(backend)
    if resolved == "memory":
        return InMemoryGraphStore()

    resolved_host = host or os.environ.get("FALKORDB_HOST", "localhost")
    resolved_port = int(port if port is not None else os.environ.get("FALKORDB_PORT", "6379"))
    resolved_graph = graph_name or os.environ.get("FALKORDB_GRAPH", "codemem")
    resolved_password = os.environ.get("FALKORDB_PASSWORD")

    try:
        return FalkorDBAdapter(
            host=resolved_host,
            port=resolved_port,
            graph_id=resolved_graph,
            graph_name=resolved_graph,
            password=resolved_password,
        )
    except ConnectionError as exc:
        raise RuntimeError(f"DATABASE_UNAVAILABLE: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(
            f"DATABASE_UNAVAILABLE: Could not connect to FalkorDB at {resolved_host}:{resolved_port} ({exc})"
        ) from exc
