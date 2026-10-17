"""Model Context Protocol (MCP) server wrapping CodeMem MemoryService."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    from mcp.server.mcpserver import MCPServer as FastMCP

from codemem.cli.main import (
    _get_state_file_path,
    load_persistent_store,
    save_persistent_store,
)
from codemem.core.models import (
    CodeMemErrorDetail,
    EvidenceInput,
    HandoffInput,
    HandoffStatus,
    MemoryInput,
    MemoryType,
)
from codemem.core.service import MemoryService
from codemem.graph.in_memory import InMemoryGraphStore


class CodeMemMCPServer:
    """
    MCP transport wrapper around MemoryService enforcing the API contract,
    structured CodeMemErrorDetail responses, and cross-session persistence.
    """

    def __init__(
        self,
        service: Optional[MemoryService] = None,
        repo_path: str = ".",
    ) -> None:
        self.repo_path = repo_path
        self._state_file = _get_state_file_path(repo_path)
        if service is not None:
            self.service = service
        else:
            store = load_persistent_store(self._state_file)
            self.service = MemoryService(store=store)

        self.mcp = FastMCP("codemem")
        self._register_tools()

    def _register_tools(self) -> None:
        """Bind contract methods and workflows as MCP tools."""
        self.mcp.tool(name="register_repository")(self.register_repository)
        self.mcp.tool(name="remember")(self.remember)
        self.mcp.tool(name="recall")(self.recall)
        self.mcp.tool(name="update")(self.update)
        self.mcp.tool(name="history")(self.history)
        self.mcp.tool(name="explain")(self.explain)
        self.mcp.tool(name="check_changes")(self.check_changes)
        self.mcp.tool(name="revalidate_memory")(self.revalidate_memory)
        self.mcp.tool(name="record_handoff")(self.record_handoff)
        self.mcp.tool(name="list_handoffs")(self.list_handoffs)
        self.mcp.tool(name="complete_handoff")(self.complete_handoff)

    def _flush_store(self) -> None:
        """Persist in-memory state to disk if backed by InMemoryGraphStore."""
        if isinstance(self.service.store, InMemoryGraphStore):
            save_persistent_store(self.service.store, self._state_file)

    def register_repository(self, path: str = ".") -> Dict[str, Any]:
        """Resolve, validate, and initialize repository scope."""
        try:
            self.repo_path = path
            self._state_file = _get_state_file_path(path)
            # Rehydrate from path state if active store is empty
            if isinstance(self.service.store, InMemoryGraphStore) and not self.service.store._repositories:
                self.service.store = load_persistent_store(self._state_file)

            info = self.service.register_repository(path)
            self._flush_store()
            return info.model_dump(mode="json")
        except Exception as exc:
            return CodeMemErrorDetail(
                error_code="INVALID_DIRECTORY",
                message=str(exc),
                details={"path": path},
            ).model_dump(mode="json")

    def remember(
        self,
        repository_id: str,
        claim: str,
        subject: Optional[str] = None,
        predicate: Optional[str] = None,
        object: Optional[str] = None,
        memory_type: str = "architecture",
        source_agent_id: str = "mcp-agent",
        source_session_id: str = "mcp-session",
        tags: Optional[List[str]] = None,
        evidence: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Store a new codebase fact, anchor its evidence, and commit it to the graph."""
        try:
            ev_inputs = [EvidenceInput.model_validate(e) for e in (evidence or [])]
            mem_input = MemoryInput(
                repository_id=repository_id,
                claim=claim,
                subject=subject,
                predicate=predicate,
                object=object,
                memory_type=MemoryType(memory_type.lower()),
                source_agent_id=source_agent_id,
                source_session_id=source_session_id,
                tags=tags or [],
                evidence=ev_inputs,
            )
            record = self.service.remember(mem_input)
            self._flush_store()
            return record.model_dump(mode="json")
        except ValueError as exc:
            msg = str(exc)
            code = "REPO_NOT_FOUND" if "Repository not registered" in msg else "INVALID_EVIDENCE_PATH"
            return CodeMemErrorDetail(
                error_code=code,
                message=msg,
                details={"repository_id": repository_id},
            ).model_dump(mode="json")
        except Exception as exc:
            return CodeMemErrorDetail(
                error_code="DATABASE_ERROR",
                message=str(exc),
            ).model_dump(mode="json")

    def recall(
        self,
        repo_id: str,
        query: str,
        top_k: int = 5,
        include_stale: bool = False,
    ) -> Dict[str, Any]:
        """Search repository memories using hybrid graph traversal and evidence-aware reranking."""
        try:
            if not query or not query.strip():
                return CodeMemErrorDetail(
                    error_code="INVALID_QUERY",
                    message="Query string cannot be empty.",
                ).model_dump(mode="json")

            resp = self.service.recall(
                repo_id=repo_id,
                query=query,
                top_k=top_k,
                include_stale=include_stale,
            )
            return resp.model_dump(mode="json")
        except ValueError as exc:
            return CodeMemErrorDetail(
                error_code="REPO_NOT_FOUND",
                message=str(exc),
                details={"repo_id": repo_id},
            ).model_dump(mode="json")

    def update(
        self,
        memory_id: str,
        new_claim: str,
        reason: str,
        repo_id: str,
        new_evidence: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Supersede an existing memory with a new version without deleting history."""
        try:
            ev_inputs = [EvidenceInput.model_validate(e) for e in new_evidence] if new_evidence else None
            record = self.service.update(
                memory_id=memory_id,
                new_claim=new_claim,
                reason=reason,
                repo_id=repo_id,
                new_evidence=ev_inputs,
            )
            self._flush_store()
            return record.model_dump(mode="json")
        except PermissionError as exc:
            return CodeMemErrorDetail(
                error_code="SCOPE_VIOLATION",
                message=str(exc),
                details={"memory_id": memory_id, "repo_id": repo_id},
            ).model_dump(mode="json")
        except ValueError as exc:
            raw_mem = self.service.store.get_memory(memory_id, repo_id=None)
            code = "SCOPE_VIOLATION" if (raw_mem is not None and raw_mem.repository_id != repo_id) else "MEMORY_NOT_FOUND"
            return CodeMemErrorDetail(
                error_code=code,
                message=str(exc),
                details={"memory_id": memory_id, "repo_id": repo_id},
            ).model_dump(mode="json")

    def history(self, identifier: str, repo_id: str) -> Dict[str, Any]:
        """Retrieve chronological version history and supersession links for a memory or logical fact."""
        try:
            resp = self.service.history(identifier=identifier, repo_id=repo_id)
            return resp.model_dump(mode="json")
        except PermissionError as exc:
            return CodeMemErrorDetail(
                error_code="SCOPE_VIOLATION",
                message=str(exc),
                details={"identifier": identifier, "repo_id": repo_id},
            ).model_dump(mode="json")
        except ValueError as exc:
            raw_mem = self.service.store.get_memory(identifier, repo_id=None)
            code = "SCOPE_VIOLATION" if (raw_mem is not None and raw_mem.repository_id != repo_id) else "FACT_NOT_FOUND"
            return CodeMemErrorDetail(
                error_code=code,
                message=str(exc),
                details={"identifier": identifier, "repo_id": repo_id},
            ).model_dump(mode="json")

    def explain(self, memory_id: str, repo_id: str) -> Dict[str, Any]:
        """Return full inspectable provenance for a specific memory."""
        try:
            resp = self.service.explain(memory_id=memory_id, repo_id=repo_id)
            return resp.model_dump(mode="json")
        except PermissionError as exc:
            return CodeMemErrorDetail(
                error_code="SCOPE_VIOLATION",
                message=str(exc),
                details={"memory_id": memory_id, "repo_id": repo_id},
            ).model_dump(mode="json")
        except ValueError as exc:
            raw_mem = self.service.store.get_memory(memory_id, repo_id=None)
            code = "SCOPE_VIOLATION" if (raw_mem is not None and raw_mem.repository_id != repo_id) else "MEMORY_NOT_FOUND"
            return CodeMemErrorDetail(
                error_code=code,
                message=str(exc),
                details={"memory_id": memory_id, "repo_id": repo_id},
            ).model_dump(mode="json")

    def check_changes(self, repo_id: str) -> Dict[str, Any]:
        """Compare recorded evidence file hashes against current repository state and flag stale memories."""
        try:
            resp = self.service.check_changes(repo_id=repo_id)
            self._flush_store()
            return resp.model_dump(mode="json")
        except ValueError as exc:
            return CodeMemErrorDetail(
                error_code="REPO_NOT_FOUND",
                message=str(exc),
                details={"repo_id": repo_id},
            ).model_dump(mode="json")

    def revalidate_memory(
        self,
        memory_id: str,
        repo_id: str,
        test_command: Optional[str] = None,
        test_passed: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Revalidate a stale or observed memory against the current codebase."""
        try:
            mem = self.service.revalidate(
                memory_id=memory_id,
                repo_id=repo_id,
                test_command=test_command,
                test_passed=test_passed,
            )
            self._flush_store()
            return mem.model_dump(mode="json")
        except PermissionError as exc:
            return CodeMemErrorDetail(
                error_code="SCOPE_VIOLATION",
                message=str(exc),
                details={"memory_id": memory_id, "repo_id": repo_id},
            ).model_dump(mode="json")
        except ValueError as exc:
            raw_mem = self.service.store.get_memory(memory_id, repo_id=None)
            code = "SCOPE_VIOLATION" if (raw_mem is not None and raw_mem.repository_id != repo_id) else "MEMORY_NOT_FOUND"
            return CodeMemErrorDetail(
                error_code=code,
                message=str(exc),
                details={"memory_id": memory_id, "repo_id": repo_id},
            ).model_dump(mode="json")

    def record_handoff(
        self,
        repository_id: str,
        originating_agent_id: str,
        task_objective: str,
        completed_work: str,
        receiving_agent_id: Optional[str] = None,
        open_risks: Optional[List[str]] = None,
        relevant_memory_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Record a structured agent handoff to preserve task continuity and risks."""
        try:
            inp = HandoffInput(
                repository_id=repository_id,
                originating_agent_id=originating_agent_id,
                receiving_agent_id=receiving_agent_id,
                task_objective=task_objective,
                completed_work=completed_work,
                open_risks=open_risks or [],
                relevant_memory_ids=relevant_memory_ids or [],
            )
            record = self.service.record_handoff(inp)
            self._flush_store()
            return record.model_dump(mode="json")
        except ValueError as exc:
            return CodeMemErrorDetail(
                error_code="REPO_NOT_FOUND",
                message=str(exc),
                details={"repo_id": repository_id},
            ).model_dump(mode="json")

    def list_handoffs(
        self,
        repo_id: str,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List handoffs for a repository, optionally filtered by status."""
        status_filter = HandoffStatus(status) if status else None
        handoffs = self.service.list_handoffs(repo_id, status=status_filter)
        return [h.model_dump(mode="json") for h in handoffs]

    def complete_handoff(
        self,
        handoff_id: str,
        repo_id: Optional[str] = None,
        receiving_agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Mark a handoff as completed by receiving agent."""
        try:
            record = self.service.complete_handoff(
                handoff_id=handoff_id,
                repo_id=repo_id,
                receiving_agent_id=receiving_agent_id,
            )
            self._flush_store()
            return record.model_dump(mode="json")
        except PermissionError as exc:
            return CodeMemErrorDetail(
                error_code="SCOPE_VIOLATION",
                message=str(exc),
                details={"handoff_id": handoff_id, "repo_id": repo_id},
            ).model_dump(mode="json")
        except ValueError as exc:
            return CodeMemErrorDetail(
                error_code="HANDOFF_NOT_FOUND",
                message=str(exc),
                details={"handoff_id": handoff_id},
            ).model_dump(mode="json")

    def run(self) -> None:
        """Start the FastMCP stdio server."""
        self.mcp.run()


def create_mcp_server(
    service: Optional[MemoryService] = None,
    repo_path: str = ".",
) -> CodeMemMCPServer:
    """Factory function for creating a CodeMemMCPServer instance."""
    return CodeMemMCPServer(service=service, repo_path=repo_path)


if __name__ == "__main__":
    create_mcp_server().run()
