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
from codemem.graph.factory import create_graph_store, resolve_backend_name
from codemem.graph.in_memory import InMemoryGraphStore


class CodeMemMCPServer:
    """
    MCP transport wrapper around MemoryService enforcing the API contract,
    structured CodeMemErrorDetail responses, and cross-session persistence.
    """

    def __init__(
        self,
        service: Optional[MemoryService] = None,
        backend: Optional[str] = None,
        repo_path: str = ".",
    ) -> None:
        self.repo_path = repo_path
        self._state_file = _get_state_file_path(repo_path)
        if service is not None:
            self.service = service
        else:
            resolved = resolve_backend_name(backend)
            if resolved == "falkordb":
                store = create_graph_store("falkordb")
            else:
                store = load_persistent_store(self._state_file)
            self.service = MemoryService(store=store)

        self.mcp = FastMCP("codemem")
        self._register_tools()
        self._register_resources()
        self._register_prompts()

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
        self.mcp.tool(name="scan_repository")(self.scan_repository)
        self.mcp.tool(name="visualize_graph")(self.visualize_graph)

    def _register_resources(self) -> None:
        """Bind MCP resources for automatic context window injection."""
        self.mcp.resource("codemem://repo/{repo_id}/status")(self.get_repo_status_resource)
        self.mcp.resource("codemem://repo/{repo_id}/memories")(self.get_repo_memories_resource)
        self.mcp.resource("codemem://repo/{repo_id}/handoffs")(self.get_repo_handoffs_resource)

    def _register_prompts(self) -> None:
        """Bind MCP prompt templates for multi-agent workflows."""
        self.mcp.prompt(name="session_startup_recall")(self.session_startup_recall_prompt)
        self.mcp.prompt(name="agent_handoff_briefing")(self.agent_handoff_briefing_prompt)

    # ------------------------------------------------------------------
    # MCP Resources
    # ------------------------------------------------------------------

    def get_repo_status_resource(self, repo_id: str) -> str:
        """
        Resource URI: codemem://repo/{repo_id}/status
        Returns JSON summary of repository registration, memory status breakdown, and staleness check.
        """
        import json
        from codemem.core.models import MemoryStatus

        repo = self.service.store.get_repository(repo_id)
        if repo is None:
            return json.dumps(
                CodeMemErrorDetail(
                    error_code="REPO_NOT_FOUND",
                    message=f"Repository not registered: {repo_id}",
                    details={"repo_id": repo_id},
                ).model_dump(mode="json"),
                indent=2,
            )

        memories = self.service.store.get_all_memories(repo_id, include_superseded=False)
        counts = {status.value: 0 for status in MemoryStatus}
        for m in memories:
            counts[m.status.value] = counts.get(m.status.value, 0) + 1

        change_report = self.service.check_changes(repo_id)
        payload = {
            "repository": repo.model_dump(mode="json"),
            "active_memory_count": len(memories),
            "status_breakdown": counts,
            "stale_count": change_report.stale_count,
            "changed_files": change_report.changed_files,
            "revalidation_recommendations": change_report.revalidation_recommendations,
        }
        return json.dumps(payload, indent=2)

    def get_repo_memories_resource(self, repo_id: str) -> str:
        """
        Resource URI: codemem://repo/{repo_id}/memories
        Returns JSON array of all active memories and attached evidence for context injection.
        """
        import json

        repo = self.service.store.get_repository(repo_id)
        if repo is None:
            return json.dumps(
                CodeMemErrorDetail(
                    error_code="REPO_NOT_FOUND",
                    message=f"Repository not registered: {repo_id}",
                    details={"repo_id": repo_id},
                ).model_dump(mode="json"),
                indent=2,
            )

        memories = self.service.store.get_all_memories(repo_id, include_superseded=False)
        payload = {
            "repository_id": repo_id,
            "count": len(memories),
            "memories": [m.model_dump(mode="json") for m in memories],
        }
        return json.dumps(payload, indent=2)

    def get_repo_handoffs_resource(self, repo_id: str) -> str:
        """
        Resource URI: codemem://repo/{repo_id}/handoffs
        Returns active 'handoff' and 'bug' memories for cross-session continuity.
        """
        import json
        from codemem.core.models import MemoryType

        repo = self.service.store.get_repository(repo_id)
        if repo is None:
            return json.dumps(
                CodeMemErrorDetail(
                    error_code="REPO_NOT_FOUND",
                    message=f"Repository not registered: {repo_id}",
                    details={"repo_id": repo_id},
                ).model_dump(mode="json"),
                indent=2,
            )

        memories = self.service.store.get_all_memories(repo_id, include_superseded=False)
        handoffs = [
            m.model_dump(mode="json")
            for m in memories
            if m.memory_type in (MemoryType.HANDOFF, MemoryType.BUG)
        ]
        return json.dumps(
            {"repository_id": repo_id, "handoff_and_bug_count": len(handoffs), "items": handoffs},
            indent=2,
        )

    # ------------------------------------------------------------------
    # MCP Prompts
    # ------------------------------------------------------------------

    def session_startup_recall_prompt(self, repo_id: str, task_description: str = "general development") -> str:
        """
        MCP Prompt: session_startup_recall
        Guides an LLM coding agent on how to initialize context and verify code freshness at session startup.
        """
        return (
            f"You are starting a coding session on repository '{repo_id}' for task: '{task_description}'.\n\n"
            "Follow this mandatory CodeMem startup protocol before modifying any files:\n"
            f"1. Read resource `codemem://repo/{repo_id}/status` or call tool `check_changes(repo_id='{repo_id}')` "
            "to detect any STALE memories caused by recent git/file modifications.\n"
            f"2. Read resource `codemem://repo/{repo_id}/handoffs` to inspect open handoff notes and known bugs from prior agents.\n"
            f"3. Call `recall(repo_id='{repo_id}', query='{task_description}', top_k=5)` to retrieve verified architectural "
            "facts and API contracts relevant to your task.\n"
            "4. If `recall` returns `abstained=true`, inspect the source files directly and anchor new discoveries via `remember`."
        )

    def agent_handoff_briefing_prompt(
        self,
        repo_id: str,
        session_id: str = "default-session",
        summary: str = "",
    ) -> str:
        """
        MCP Prompt: agent_handoff_briefing
        Guides an LLM coding agent on how to package and persist handoff knowledge before ending a session.
        """
        return (
            f"You are preparing an agent handoff briefing for repository '{repo_id}' (session '{session_id}').\n"
            f"Session summary context: {summary or 'End-of-session handoff'}\n\n"
            "Perform these steps to ensure the next coding agent has verifiable ground truth:\n"
            "1. For any existing claims invalidated by your code edits, call `update(memory_id=..., new_claim=..., reason=..., repo_id=...)` "
            "so outdated versions transition to SUPERSEDED.\n"
            "2. For every new architectural decision, API contract, or bug fix, call `remember` with exact `evidence` "
            "(`file_path`, `symbol`, and `test_command`/`test_passed` if verified by pytest).\n"
            f"3. Record any unfinished work or open risks using `remember(repository_id='{repo_id}', memory_type='handoff', ...)` "
            f"so the next agent can read `codemem://repo/{repo_id}/handoffs` immediately."
        )

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

    def scan_repository(
        self,
        path: Optional[str] = None,
        max_files: int = 150,
    ) -> Dict[str, Any]:
        """Scan a repository, extract manifests and AST symbols, and bootstrap initial knowledge graph."""
        try:
            target_path = path or self.repo_path
            report = self.service.scan_repository(path=target_path, max_files=max_files)
            self._flush_store()
            return report.model_dump(mode="json")
        except Exception as exc:
            return CodeMemErrorDetail(
                error_code="SCAN_FAILED",
                message=str(exc),
                details={"path": path or self.repo_path},
            ).model_dump(mode="json")

    def visualize_graph(
        self,
        path: Optional[str] = None,
        open_browser: bool = False,
    ) -> Dict[str, Any]:
        """
        Tool 13: visualize_graph
        Generates an interactive HTML graph visualization of the repository memory graph.
        Returns the absolute path to the generated HTML viewer and node/edge statistics.
        """
        try:
            import json
            from pathlib import Path
            from scripts.visualize_graph import generate_interactive_graph

            target_dir = Path(path).resolve() if path else Path(self.repo_path).resolve()
            generate_interactive_graph(target_dir)
            out_file = target_dir / "graph_viewer.html"

            store_file = target_dir / ".codemem" / "store.json"
            node_count = 0
            edge_count = 0
            if store_file.exists():
                store_data = json.loads(store_file.read_text(encoding="utf-8"))
                node_count = len(store_data.get("memories", [])) + len(store_data.get("handoffs", [])) + len(store_data.get("repositories", []))
                edge_count = len(store_data.get("memories", [])) * 2

            if open_browser and out_file.exists():
                import webbrowser
                webbrowser.open(out_file.as_uri())

            return {
                "status": "SUCCESS",
                "html_path": str(out_file),
                "nodes": node_count,
                "edges": edge_count,
                "browser_opened": open_browser,
            }
        except Exception as exc:
            return CodeMemErrorDetail(
                error_code="VISUALIZATION_FAILED",
                message=str(exc),
                details={"path": path or self.repo_path},
            ).model_dump(mode="json")

    def run(self) -> None:
        """Start the FastMCP stdio server."""
        self.mcp.run()


def create_mcp_server(
    service: Optional[MemoryService] = None,
    backend: Optional[str] = None,
    repo_path: str = ".",
) -> CodeMemMCPServer:
    """Factory function for creating a CodeMemMCPServer instance."""
    return CodeMemMCPServer(service=service, backend=backend, repo_path=repo_path)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CodeMem MCP Server")
    parser.add_argument("--backend", default=None, choices=["memory", "falkordb"], help="Graph storage backend")
    parser.add_argument("--repo-path", default=".", help="Repository root path")
    args = parser.parse_args()

    create_mcp_server(backend=args.backend, repo_path=args.repo_path).run()
