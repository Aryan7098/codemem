"""Graph storage layer for CodeMem memory engine."""

from codemem.graph.adapter import FalkorDBAdapter
from codemem.graph.factory import create_graph_store, get_backend_name, resolve_backend_name
from codemem.graph.in_memory import InMemoryGraphStore
from codemem.graph.protocol import GraphStoreProtocol

__all__ = [
    "FalkorDBAdapter",
    "GraphStoreProtocol",
    "InMemoryGraphStore",
    "create_graph_store",
    "get_backend_name",
    "resolve_backend_name",
]
