"""Click CLI entrypoint for CodeMem."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import click

from codemem.core.models import (
    AgentHandoffRecord,
    CodeMemErrorDetail,
    EvidenceInput,
    EvidenceRecord,
    HandoffInput,
    HandoffStatus,
    MemoryInput,
    MemoryRecord,
    MemoryType,
    RepositoryInfo,
)
from codemem.core.service import MemoryService
from codemem.graph.factory import create_graph_store, get_backend_name, resolve_backend_name
from codemem.graph.in_memory import InMemoryGraphStore


DEFAULT_STATE_FILE = ".codemem/store.json"


def _get_state_file_path(repo_path: str = ".") -> Path:
    """Resolve path to local JSON persistence file for CLI execution."""
    env_path = os.environ.get("CODEMEM_STORE_PATH")
    if env_path:
        return Path(env_path)
    canonical = Path(repo_path).resolve()
    try:
        from codemem.repository.git import GitAdapter
        git_root = GitAdapter.get_git_root(str(canonical))
        base = Path(git_root) if git_root else canonical
    except Exception:
        base = canonical
    return base / DEFAULT_STATE_FILE


def load_persistent_store(state_file: Path) -> InMemoryGraphStore:
    """Load InMemoryGraphStore state from disk if available."""
    store = InMemoryGraphStore()
    if not state_file.is_file():
        return store

    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        for repo_dict in data.get("repositories", []):
            repo = RepositoryInfo.model_validate(repo_dict)
            store.upsert_repository(repo)

        for mem_dict in data.get("memories", []):
            mem = MemoryRecord.model_validate(mem_dict)
            ev_list = [EvidenceRecord.model_validate(e) for e in mem_dict.get("evidence_records", [])]
            store.save_memory(mem, ev_list)

        for handoff_dict in data.get("handoffs", []):
            handoff = AgentHandoffRecord.model_validate(handoff_dict)
            store.save_handoff(handoff)

        for new_id, edge_val in data.get("supersedes_edges", {}).items():
            store._supersedes_edges[new_id] = tuple(edge_val)  # type: ignore[assignment]

        for src_id, tgt_id in data.get("contradicts_edges", {}).items():
            store._contradicts_edges[src_id] = tgt_id
    except Exception:
        return InMemoryGraphStore()

    return store


def save_persistent_store(store: InMemoryGraphStore, state_file: Path) -> None:
    """Persist InMemoryGraphStore state to disk."""
    state_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "repositories": [r.model_dump(mode="json") for r in store._repositories.values()],
        "memories": [m.model_dump(mode="json") for m in store._memories.values()],
        "handoffs": [h.model_dump(mode="json") for h in getattr(store, "_handoffs", {}).values()],
        "supersedes_edges": {k: list(v) for k, v in store._supersedes_edges.items()},
        "contradicts_edges": dict(getattr(store, "_contradicts_edges", {})),
    }
    state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def get_service_and_repo(
    path: str = ".",
    backend: Optional[str] = None,
) -> Tuple[MemoryService, RepositoryInfo, Path]:
    """Initialize MemoryService using CODEMEM_BACKEND ('falkordb' or persistent 'memory')."""
    state_file = _get_state_file_path(path)
    if backend is None:
        ctx = click.get_current_context(silent=True)
        if ctx and ctx.obj and isinstance(ctx.obj, dict):
            backend = ctx.obj.get("backend")

    resolved = resolve_backend_name(backend)
    if resolved == "falkordb":
        store = create_graph_store("falkordb")
        service = MemoryService(store=store)
        repo = service.register_repository(path)
        return service, repo, state_file

    store = load_persistent_store(state_file)
    service = MemoryService(store=store)
    repo = service.register_repository(path)
    save_persistent_store(store, state_file)
    return service, repo, state_file


def _parse_evidence_specs(
    evidence_specs: Tuple[str, ...],
    test_cmd: Optional[str],
    test_passed: Optional[bool],
) -> List[EvidenceInput]:
    """
    Parse CLI evidence strings in format:
      - 'path/to/file.py'
      - 'path/to/file.py:symbol_name'
      - 'path/to/file.py:symbol_name:10-25'
    """
    results: List[EvidenceInput] = []
    for spec in evidence_specs:
        parts = spec.split(":")
        file_path = parts[0].strip()
        symbol = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
        line_start: Optional[int] = None
        line_end: Optional[int] = None
        if len(parts) > 2 and parts[2].strip():
            span = parts[2].strip()
            if "-" in span:
                s_start, s_end = span.split("-", 1)
                line_start = int(s_start)
                line_end = int(s_end)
            else:
                line_start = int(span)
                line_end = int(span)

        results.append(
            EvidenceInput(
                file_path=file_path,
                symbol=symbol,
                line_start=line_start,
                line_end=line_end,
                test_command=test_cmd,
                test_passed=test_passed,
            )
        )
    return results


def _emit_error(error_code: str, message: str, as_json: bool = False) -> None:
    """Emit structured CodeMemErrorDetail and exit with non-zero code."""
    if "DATABASE_UNAVAILABLE" in message or "Failed to connect to FalkorDB" in message:
        error_code = "DATABASE_UNAVAILABLE"
    err = CodeMemErrorDetail(error_code=error_code, message=message)
    if as_json:
        click.echo(err.model_dump_json(indent=2), err=True)
    else:
        click.secho(f"[{err.error_code}] {err.message}", fg="red", err=True)
    sys.exit(1)


@click.group()
@click.option(
    "--backend",
    type=click.Choice(["memory", "falkordb"], case_sensitive=False),
    default=None,
    envvar="CODEMEM_BACKEND",
    help="Storage backend ('memory' or 'falkordb').",
)
@click.version_option(version="0.1.0", prog_name="codemem")
@click.pass_context
def cli(ctx: click.Context, backend: Optional[str] = None) -> None:
    """CodeMem: Local-first, evidence-backed shared memory layer for coding agents."""
    ctx.ensure_object(dict)
    if backend:
        ctx.obj["backend"] = backend


@cli.command("init")
@click.option(
    "--backend",
    type=click.Choice(["memory", "falkordb"], case_sensitive=False),
    default=None,
    help="Storage backend ('memory' or 'falkordb').",
)
@click.option("--path", "--repo", "path", default=".", help="Path to repository root.")
@click.option("--json", "as_json", is_flag=True, help="Output result as JSON.")
def init_cmd(path: str, as_json: bool, backend: Optional[str] = None) -> None:
    """Register and initialize a repository scope."""
    try:
        _, repo, _ = get_service_and_repo(path, backend=backend)
        if as_json:
            click.echo(repo.model_dump_json(indent=2))
        else:
            click.secho(f"Repository initialized: {repo.repository_id}", fg="green", bold=True)
            click.echo(f"  Root Path : {repo.root_path}")
            click.echo(f"  Git Active: {repo.git_available} (HEAD: {repo.head_commit or 'N/A'})")
    except Exception as exc:
        code = "DATABASE_UNAVAILABLE" if ("DATABASE_UNAVAILABLE" in str(exc) or "Failed to connect to FalkorDB" in str(exc)) else "INVALID_DIRECTORY"
        _emit_error(code, str(exc), as_json=as_json)


@cli.command("remember")
@click.argument("claim")
@click.option(
    "--backend",
    type=click.Choice(["memory", "falkordb"], case_sensitive=False),
    default=None,
    help="Storage backend ('memory' or 'falkordb').",
)
@click.option("--path", "--repo", "path", default=".", help="Repository path.")
@click.option("--repo-id", default=None, help="Explicit repository_id override.")
@click.option("--subject", default=None, help="Entity or component name.")
@click.option("--predicate", default=None, help="Relationship or action.")
@click.option("--object", "obj", default=None, help="Target entity or concept.")
@click.option(
    "--type",
    "memory_type",
    type=click.Choice([t.value for t in MemoryType], case_sensitive=False),
    default=MemoryType.ARCHITECTURE.value,
    help="Category of memory.",
)
@click.option("--tag", "tags", multiple=True, help="Conceptual tag (can be passed multiple times).")
@click.option(
    "--evidence",
    "evidence_specs",
    multiple=True,
    help="Evidence anchor in format 'file_path[:symbol[:line_start-line_end]]'.",
)
@click.option("--test-command", default=None, help="Verification test command.")
@click.option("--test-passed", is_flag=True, default=False, help="Flag indicating test command passed.")
@click.option("--agent-id", default="cli-agent", help="Originating agent identifier.")
@click.option("--session-id", default="cli-session", help="Originating session identifier.")
@click.option("--json", "as_json", is_flag=True, help="Output result as JSON.")
def remember_cmd(
    claim: str,
    path: str,
    repo_id: Optional[str],
    subject: Optional[str],
    predicate: Optional[str],
    obj: Optional[str],
    memory_type: str,
    tags: Tuple[str, ...],
    evidence_specs: Tuple[str, ...],
    test_command: Optional[str],
    test_passed: bool,
    agent_id: str,
    session_id: str,
    as_json: bool,
    backend: Optional[str] = None,
) -> None:
    """Store a new codebase claim with optional evidence anchors."""
    try:
        service, repo, state_file = get_service_and_repo(path, backend=backend)
        target_repo_id = repo_id or repo.repository_id

        ev_inputs = _parse_evidence_specs(
            evidence_specs,
            test_cmd=test_command,
            test_passed=True if test_passed else None,
        )

        mem_input = MemoryInput(
            repository_id=target_repo_id,
            claim=claim,
            subject=subject,
            predicate=predicate,
            object=obj,
            memory_type=MemoryType(memory_type.lower()),
            source_agent_id=agent_id,
            source_session_id=session_id,
            tags=list(tags),
            evidence=ev_inputs,
        )
        record = service.remember(mem_input)
        if isinstance(service.store, InMemoryGraphStore):
            save_persistent_store(service.store, state_file)

        if as_json:
            click.echo(record.model_dump_json(indent=2))
        else:
            click.secho(f"Memory recorded: {record.id} [{record.status.value}]", fg="green", bold=True)
            click.echo(f"  Fact ID   : {record.logical_fact_id}")
            click.echo(f"  Claim     : {record.claim}")
            click.echo(f"  Confidence: {record.confidence_score:.2f}")
            if record.evidence_records:
                click.echo(f"  Evidence  : {len(record.evidence_records)} anchor(s) attached")
    except ValueError as exc:
        _emit_error("INVALID_EVIDENCE_PATH", str(exc), as_json=as_json)
    except Exception as exc:
        code = "DATABASE_UNAVAILABLE" if ("DATABASE_UNAVAILABLE" in str(exc) or "Failed to connect to FalkorDB" in str(exc)) else "DATABASE_ERROR"
        _emit_error(code, str(exc), as_json=as_json)


@cli.command("recall")
@click.argument("query")
@click.option(
    "--backend",
    type=click.Choice(["memory", "falkordb"], case_sensitive=False),
    default=None,
    help="Storage backend ('memory' or 'falkordb').",
)
@click.option("--path", "--repo", "path", default=".", help="Repository path.")
@click.option("--repo-id", default=None, help="Explicit repository_id override.")
@click.option("--top-k", default=5, type=int, help="Maximum results to return.")
@click.option("--include-stale", is_flag=True, default=False, help="Include STALE memories in results.")
@click.option("--json", "as_json", is_flag=True, help="Output result as JSON.")
def recall_cmd(
    query: str,
    path: str,
    repo_id: Optional[str],
    top_k: int,
    include_stale: bool,
    as_json: bool,
    backend: Optional[str] = None,
) -> None:
    """Search repository memories with evidence-aware ranking and abstention."""
    try:
        service, repo, _ = get_service_and_repo(path, backend=backend)
        target_repo_id = repo_id or repo.repository_id
        response = service.recall(
            repo_id=target_repo_id,
            query=query,
            top_k=top_k,
            include_stale=include_stale,
        )

        if as_json:
            click.echo(response.model_dump_json(indent=2))
            return

        if response.abstained:
            click.secho(f"ABSTAINED: {response.abstain_reason}", fg="yellow", bold=True)
            return

        click.secho(f"Found {len(response.results)} memory record(s):", fg="cyan", bold=True)
        for idx, item in enumerate(response.results, start=1):
            m = item.memory
            click.echo(f"\n{idx}. [{m.status.value}] {m.claim} (Score: {item.final_score:.3f}, ID: {m.id})")
            if item.staleness_warning:
                click.secho(f"   WARNING: {item.staleness_warning}", fg="yellow")
            for ev in m.evidence_records:
                sym_str = f":{ev.symbol}" if ev.symbol else ""
                click.echo(f"   - Evidence: {ev.file_path}{sym_str}")
    except Exception as exc:
        code = "DATABASE_UNAVAILABLE" if ("DATABASE_UNAVAILABLE" in str(exc) or "Failed to connect to FalkorDB" in str(exc)) else "REPO_NOT_FOUND"
        _emit_error(code, str(exc), as_json=as_json)


@cli.command("update")
@click.argument("memory_id")
@click.argument("new_claim")
@click.option("--reason", required=True, help="Audit reason for superseding the memory.")
@click.option(
    "--backend",
    type=click.Choice(["memory", "falkordb"], case_sensitive=False),
    default=None,
    help="Storage backend ('memory' or 'falkordb').",
)
@click.option("--path", "--repo", "path", default=".", help="Repository path.")
@click.option("--repo-id", default=None, help="Explicit repository_id override.")
@click.option(
    "--evidence",
    "evidence_specs",
    multiple=True,
    help="Fresh evidence anchor in format 'file_path[:symbol[:line_start-line_end]]'.",
)
@click.option("--json", "as_json", is_flag=True, help="Output result as JSON.")
def update_cmd(
    memory_id: str,
    new_claim: str,
    reason: str,
    path: str,
    repo_id: Optional[str],
    evidence_specs: Tuple[str, ...],
    as_json: bool,
    backend: Optional[str] = None,
) -> None:
    """Supersede an existing memory version with an updated claim."""
    try:
        service, repo, state_file = get_service_and_repo(path, backend=backend)
        target_repo_id = repo_id or repo.repository_id
        ev_inputs = _parse_evidence_specs(evidence_specs, test_cmd=None, test_passed=None) if evidence_specs else None

        updated = service.update(
            memory_id=memory_id,
            new_claim=new_claim,
            reason=reason,
            repo_id=target_repo_id,
            new_evidence=ev_inputs,
        )
        if isinstance(service.store, InMemoryGraphStore):
            save_persistent_store(service.store, state_file)

        if as_json:
            click.echo(updated.model_dump_json(indent=2))
        else:
            click.secho(f"Memory updated: {updated.id} supersedes {memory_id}", fg="green", bold=True)
            click.echo(f"  New Claim: {updated.claim}")
            click.echo(f"  Status   : {updated.status.value}")
    except PermissionError as exc:
        _emit_error("SCOPE_VIOLATION", str(exc), as_json=as_json)
    except ValueError as exc:
        _emit_error("MEMORY_NOT_FOUND", str(exc), as_json=as_json)
    except Exception as exc:
        code = "DATABASE_UNAVAILABLE" if ("DATABASE_UNAVAILABLE" in str(exc) or "Failed to connect to FalkorDB" in str(exc)) else "SCOPE_VIOLATION"
        _emit_error(code, str(exc), as_json=as_json)


@cli.command("history")
@click.argument("identifier")
@click.option(
    "--backend",
    type=click.Choice(["memory", "falkordb"], case_sensitive=False),
    default=None,
    help="Storage backend ('memory' or 'falkordb').",
)
@click.option("--path", "--repo", "path", default=".", help="Repository path.")
@click.option("--repo-id", default=None, help="Explicit repository_id override.")
@click.option("--json", "as_json", is_flag=True, help="Output result as JSON.")
def history_cmd(
    identifier: str,
    path: str,
    repo_id: Optional[str],
    as_json: bool,
    backend: Optional[str] = None,
) -> None:
    """Show chronological revision history for a memory_id or logical_fact_id."""
    try:
        service, repo, _ = get_service_and_repo(path, backend=backend)
        target_repo_id = repo_id or repo.repository_id
        hist = service.history(identifier=identifier, repo_id=target_repo_id)

        if as_json:
            click.echo(hist.model_dump_json(indent=2))
            return

        click.secho(f"History for Logical Fact: {hist.logical_fact_id} (Current: {hist.current_version_id})", fg="cyan", bold=True)
        for v in hist.versions:
            sup_info = f" -> supersedes {v.supersedes_id}" if v.supersedes_id else ""
            click.echo(f"  - {v.id} [{v.status.value}] ({v.recorded_at}){sup_info}: {v.claim}")
    except PermissionError as exc:
        _emit_error("SCOPE_VIOLATION", str(exc), as_json=as_json)
    except ValueError as exc:
        _emit_error("FACT_NOT_FOUND", str(exc), as_json=as_json)
    except Exception as exc:
        code = "DATABASE_UNAVAILABLE" if ("DATABASE_UNAVAILABLE" in str(exc) or "Failed to connect to FalkorDB" in str(exc)) else "FACT_NOT_FOUND"
        _emit_error(code, str(exc), as_json=as_json)


@cli.command("explain")
@click.argument("memory_id")
@click.option(
    "--backend",
    type=click.Choice(["memory", "falkordb"], case_sensitive=False),
    default=None,
    help="Storage backend ('memory' or 'falkordb').",
)
@click.option("--path", "--repo", "path", default=".", help="Repository path.")
@click.option("--repo-id", default=None, help="Explicit repository_id override.")
@click.option("--json", "as_json", is_flag=True, help="Output result as JSON.")
def explain_cmd(
    memory_id: str,
    path: str,
    repo_id: Optional[str],
    as_json: bool,
    backend: Optional[str] = None,
) -> None:
    """Inspect full provenance, evidence hashes, and freshness of a memory."""
    try:
        service, repo, _ = get_service_and_repo(path, backend=backend)
        target_repo_id = repo_id or repo.repository_id
        explanation = service.explain(memory_id=memory_id, repo_id=target_repo_id)

        if as_json:
            click.echo(explanation.model_dump_json(indent=2))
            return

        mem = explanation.memory
        click.secho(f"Memory Explanation: {mem.id}", fg="cyan", bold=True)
        click.echo(f"  Claim           : {mem.claim}")
        click.echo(f"  Freshness Status: {explanation.freshness_status}")
        click.echo(f"  Agent / Session : {explanation.provenance.get('source_agent_id')} / {explanation.provenance.get('source_session_id')}")
        click.echo(f"  Recorded At     : {explanation.provenance.get('recorded_at')}")
        if explanation.changed_evidence_files:
            click.secho(f"  Changed Files   : {', '.join(explanation.changed_evidence_files)}", fg="yellow")
        for ev in explanation.evidence_records:
            click.echo(f"  Evidence [{ev.id}]: {ev.file_path} (symbol={ev.symbol or 'N/A'}, sha256={ev.content_hash or 'N/A'})")
    except PermissionError as exc:
        _emit_error("SCOPE_VIOLATION", str(exc), as_json=as_json)
    except ValueError as exc:
        _emit_error("MEMORY_NOT_FOUND", str(exc), as_json=as_json)
    except Exception as exc:
        code = "DATABASE_UNAVAILABLE" if ("DATABASE_UNAVAILABLE" in str(exc) or "Failed to connect to FalkorDB" in str(exc)) else "MEMORY_NOT_FOUND"
        _emit_error(code, str(exc), as_json=as_json)


@cli.command("check-changes")
@click.option(
    "--backend",
    type=click.Choice(["memory", "falkordb"], case_sensitive=False),
    default=None,
    help="Storage backend ('memory' or 'falkordb').",
)
@click.option("--path", "--repo", "path", default=".", help="Repository path.")
@click.option("--repo-id", default=None, help="Explicit repository_id override.")
@click.option("--json", "as_json", is_flag=True, help="Output result as JSON.")
def check_changes_cmd(
    path: str,
    repo_id: Optional[str],
    as_json: bool,
    backend: Optional[str] = None,
) -> None:
    """Detect modified evidence files and flag affected memories as STALE."""
    try:
        service, repo, state_file = get_service_and_repo(path, backend=backend)
        target_repo_id = repo_id or repo.repository_id
        report = service.check_changes(repo_id=target_repo_id)

        if isinstance(service.store, InMemoryGraphStore):
            save_persistent_store(service.store, state_file)

        if as_json:
            click.echo(report.model_dump_json(indent=2))
            return

        if report.stale_count == 0:
            click.secho("All active memories are fresh.", fg="green", bold=True)
        else:
            click.secho(f"Detected {report.stale_count} stale memory record(s)!", fg="yellow", bold=True)
            for rec in report.revalidation_recommendations:
                click.echo(f"  - {rec}")
    except ValueError as exc:
        _emit_error("REPO_NOT_FOUND", str(exc), as_json=as_json)
    except Exception as exc:
        code = "DATABASE_UNAVAILABLE" if ("DATABASE_UNAVAILABLE" in str(exc) or "Failed to connect to FalkorDB" in str(exc)) else "REPO_NOT_FOUND"
        _emit_error(code, str(exc), as_json=as_json)


@cli.command("revalidate")
@click.argument("memory_id")
@click.option(
    "--backend",
    type=click.Choice(["memory", "falkordb"], case_sensitive=False),
    default=None,
    help="Storage backend ('memory' or 'falkordb').",
)
@click.option("--path", "--repo", "path", default=".", help="Repository path.")
@click.option("--repo-id", default=None, help="Explicit repository_id override.")
@click.option("--test-cmd", default=None, help="Optional test command that verified the fact.")
@click.option("--test-passed", is_flag=True, default=False, help="Whether the test execution passed.")
@click.option("--json", "as_json", is_flag=True, help="Output result as JSON.")
def revalidate_cmd(
    memory_id: str,
    path: str,
    repo_id: Optional[str],
    test_cmd: Optional[str],
    test_passed: bool,
    as_json: bool,
    backend: Optional[str] = None,
) -> None:
    """Revalidate a stale or observed memory against current code state."""
    try:
        service, repo, state_file = get_service_and_repo(path, backend=backend)
        target_repo_id = repo_id or repo.repository_id
        revalidated = service.revalidate(
            memory_id=memory_id,
            repo_id=target_repo_id,
            test_command=test_cmd,
            test_passed=test_passed if test_cmd else None,
        )
        if isinstance(service.store, InMemoryGraphStore):
            save_persistent_store(service.store, state_file)

        if as_json:
            click.echo(revalidated.model_dump_json(indent=2))
            return

        click.secho(f"Memory revalidated: {revalidated.id}", fg="green", bold=True)
        click.echo(f"  Claim : {revalidated.claim}")
        click.echo(f"  Status: {revalidated.status.value} (Confidence: {revalidated.confidence_score})")
        for ev in revalidated.evidence_records:
            click.echo(f"  Evidence: {ev.file_path} (sha256={ev.content_hash or 'N/A'}, test_passed={ev.test_passed})")
    except PermissionError as exc:
        _emit_error("SCOPE_VIOLATION", str(exc), as_json=as_json)
    except ValueError as exc:
        _emit_error("MEMORY_NOT_FOUND", str(exc), as_json=as_json)
    except Exception as exc:
        code = "DATABASE_UNAVAILABLE" if ("DATABASE_UNAVAILABLE" in str(exc) or "Failed to connect to FalkorDB" in str(exc)) else "SCOPE_VIOLATION"
        _emit_error(code, str(exc), as_json=as_json)


@cli.command("status")
@click.option(
    "--backend",
    type=click.Choice(["memory", "falkordb"], case_sensitive=False),
    default=None,
    help="Storage backend ('memory' or 'falkordb').",
)
@click.option("--path", "--repo", "path", default=".", help="Repository path.")
@click.option("--repo-id", default=None, help="Explicit repository_id override.")
@click.option("--json", "as_json", is_flag=True, help="Output result as JSON.")
def status_cmd(path: str, repo_id: Optional[str], as_json: bool, backend: Optional[str] = None) -> None:
    """Show current repository memory status, fact counts, and freshness."""
    try:
        service, repo, state_file = get_service_and_repo(path, backend=backend)
        target_repo_id = repo_id or repo.repository_id
        all_active = service.store.get_all_memories(target_repo_id, include_superseded=False)
        all_with_superseded = service.store.get_all_memories(target_repo_id, include_superseded=True)
        stale_count = sum(1 for m in all_active if m.status.value == "STALE")
        verified_count = sum(1 for m in all_active if m.status.value == "VERIFIED")
        observed_count = sum(1 for m in all_active if m.status.value == "OBSERVED")
        inferred_count = sum(1 for m in all_active if m.status.value == "INFERRED")

        status_data = {
            "repository_id": repo.repository_id,
            "root_path": repo.root_path,
            "git_available": repo.git_available,
            "head_commit": repo.head_commit,
            "state_file": str(state_file),
            "active_memories": len(all_active),
            "total_versions": len(all_with_superseded),
            "counts_by_status": {
                "VERIFIED": verified_count,
                "OBSERVED": observed_count,
                "INFERRED": inferred_count,
                "STALE": stale_count,
            },
        }

        if as_json:
            click.echo(json.dumps(status_data, indent=2))
            return

        click.secho(f"CodeMem Repository Status: {repo.repository_id}", fg="cyan", bold=True)
        click.echo(f"  Root Path       : {repo.root_path}")
        click.echo(f"  Git Active      : {repo.git_available} (HEAD: {repo.head_commit or 'N/A'})")
        click.echo(f"  State Store     : {state_file}")
        click.echo(f"  Active Memories : {len(all_active)} (Total Versions: {len(all_with_superseded)})")
        click.echo(f"  Status Breakdown: {verified_count} VERIFIED, {observed_count} OBSERVED, {inferred_count} INFERRED, {stale_count} STALE")
    except Exception as exc:
        code = "DATABASE_UNAVAILABLE" if ("DATABASE_UNAVAILABLE" in str(exc) or "Failed to connect to FalkorDB" in str(exc)) else "INVALID_DIRECTORY"
        _emit_error(code, str(exc), as_json=as_json)


@cli.command("mcp")
@click.option(
    "--backend",
    type=click.Choice(["memory", "falkordb"], case_sensitive=False),
    default=None,
    help="Storage backend ('memory' or 'falkordb').",
)
@click.option("--path", "--repo", "path", default=".", help="Repository path.")
def mcp_cmd(path: str, backend: Optional[str] = None) -> None:
    """Launch the CodeMem MCP server over stdio for LLM coding agents."""
    from codemem.mcp.server import create_mcp_server
    create_mcp_server(backend=backend, repo_path=path).run()


@cli.group("handoff")
def handoff_grp() -> None:
    """Manage structured agent handoff records for multi-agent workflows."""


@handoff_grp.command("record")
@click.option("--path", "--repo", "path", default=".", help="Repository path.")
@click.option("--repo-id", default=None, help="Explicit repository_id override.")
@click.option("--from", "originating_agent_id", default="cli-agent", help="Originating agent ID.")
@click.option("--to", "receiving_agent_id", default=None, help="Receiving agent ID.")
@click.option("--objective", required=True, help="Task objective for the handoff.")
@click.option("--completed", required=True, help="Summary of completed work.")
@click.option("--risk", "-r", "open_risks", multiple=True, help="Open risks or follow-up traps.")
@click.option("--memory", "-m", "relevant_memory_ids", multiple=True, help="Relevant memory IDs.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def handoff_record_cmd(
    path: str,
    repo_id: Optional[str],
    originating_agent_id: str,
    receiving_agent_id: Optional[str],
    objective: str,
    completed: str,
    open_risks: Tuple[str, ...],
    relevant_memory_ids: Tuple[str, ...],
    as_json: bool,
) -> None:
    """Record an agent handoff for task transitions and continuity."""
    try:
        service, repo, state_file = get_service_and_repo(path)
        actual_repo_id = repo_id if repo_id is not None else repo.repository_id

        risks_list: List[str] = []
        for r in open_risks:
            risks_list.extend([item.strip() for item in r.split(",") if item.strip()])

        mem_list: List[str] = []
        for m in relevant_memory_ids:
            mem_list.extend([item.strip() for item in m.split(",") if item.strip()])

        inp = HandoffInput(
            repository_id=actual_repo_id,
            originating_agent_id=originating_agent_id,
            receiving_agent_id=receiving_agent_id,
            task_objective=objective,
            completed_work=completed,
            open_risks=risks_list,
            relevant_memory_ids=mem_list,
        )
        record = service.record_handoff(inp)
        if isinstance(service.store, InMemoryGraphStore):
            save_persistent_store(service.store, state_file)

        if as_json:
            click.echo(record.model_dump_json(indent=2))
        else:
            click.secho(f"Handoff recorded: {record.handoff_id}", fg="green", bold=True)
            click.echo(f"  Repository : {record.repository_id}")
            click.echo(f"  From Agent : {record.originating_agent_id}")
            click.echo(f"  To Agent   : {record.receiving_agent_id or 'Unassigned'}")
            click.echo(f"  Objective  : {record.task_objective}")
            click.echo(f"  Status     : {record.status.value}")
            if record.open_risks:
                click.echo(f"  Open Risks : {', '.join(record.open_risks)}")
            if record.relevant_memory_ids:
                click.echo(f"  Memories   : {', '.join(record.relevant_memory_ids)}")
    except Exception as exc:
        _emit_error("HANDOFF_FAILED", str(exc), as_json=as_json)


@handoff_grp.command("list")
@click.option("--path", "--repo", "path", default=".", help="Repository path.")
@click.option("--repo-id", default=None, help="Explicit repository_id override.")
@click.option(
    "--status",
    type=click.Choice([s.value for s in HandoffStatus], case_sensitive=False),
    default=None,
    help="Filter by handoff status.",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def handoff_list_cmd(
    path: str,
    repo_id: Optional[str],
    status: Optional[str],
    as_json: bool,
) -> None:
    """List agent handoffs for the repository."""
    try:
        service, repo, _ = get_service_and_repo(path)
        actual_repo_id = repo_id if repo_id is not None else repo.repository_id
        status_filter = HandoffStatus(status) if status else None
        handoffs = service.list_handoffs(actual_repo_id, status=status_filter)

        if as_json:
            click.echo(json.dumps([h.model_dump(mode="json") for h in handoffs], indent=2))
        else:
            if not handoffs:
                click.echo("No handoff records found.")
                return
            click.secho(f"Handoffs for {actual_repo_id} ({len(handoffs)} found):", fg="cyan", bold=True)
            for h in handoffs:
                click.secho(f"\n[*] {h.handoff_id} [{h.status.value}]", fg="yellow", bold=True)
                click.echo(f"    From      : {h.originating_agent_id} -> {h.receiving_agent_id or 'Any'}")
                click.echo(f"    Objective : {h.task_objective}")
                click.echo(f"    Completed : {h.completed_work}")
                if h.open_risks:
                    click.echo(f"    Risks     : {', '.join(h.open_risks)}")
                if h.relevant_memory_ids:
                    click.echo(f"    Memories  : {', '.join(h.relevant_memory_ids)}")
                click.echo(f"    Created   : {h.created_at}")
    except Exception as exc:
        _emit_error("HANDOFF_FAILED", str(exc), as_json=as_json)


@handoff_grp.command("complete")
@click.argument("handoff_id")
@click.option("--path", "--repo", "path", default=".", help="Repository path.")
@click.option("--repo-id", default=None, help="Explicit repository_id override.")
@click.option("--agent", "receiving_agent_id", default=None, help="Completing agent ID.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def handoff_complete_cmd(
    handoff_id: str,
    path: str,
    repo_id: Optional[str],
    receiving_agent_id: Optional[str],
    as_json: bool,
) -> None:
    """Mark a handoff as completed by receiving agent."""
    try:
        service, repo, state_file = get_service_and_repo(path)
        actual_repo_id = repo_id if repo_id is not None else repo.repository_id
        record = service.complete_handoff(handoff_id, repo_id=actual_repo_id, receiving_agent_id=receiving_agent_id)
        if isinstance(service.store, InMemoryGraphStore):
            save_persistent_store(service.store, state_file)

        if as_json:
            click.echo(record.model_dump_json(indent=2))
        else:
            click.secho(f"Handoff {record.handoff_id} marked as COMPLETED", fg="green", bold=True)
            if record.receiving_agent_id:
                click.echo(f"  Completed by: {record.receiving_agent_id}")
    except Exception as exc:
        _emit_error("HANDOFF_FAILED", str(exc), as_json=as_json)


@cli.command("eval")
@click.option("--json", "as_json", is_flag=True, help="Output benchmark results as JSON.")
@click.option("--output", "output_file", default=None, help="Save benchmark report to a file.")
def eval_cmd(as_json: bool, output_file: Optional[str]) -> None:
    """Run the reproducible LongMemEval evaluation benchmark suite."""
    from codemem.evaluation import BenchmarkRunner

    runner = BenchmarkRunner()
    report = runner.run_benchmark()

    if output_file:
        Path(output_file).write_text(report.model_dump_json(indent=2), encoding="utf-8")

    if as_json:
        click.echo(report.model_dump_json(indent=2))
        return

    click.secho("\n===========================================================================", fg="cyan", bold=True)
    click.secho("  CODEMEM EVALUATION BENCHMARK (LongMemEval & RepoMem Categories)", fg="cyan", bold=True)
    click.secho("===========================================================================", fg="cyan", bold=True)
    click.echo(f"Timestamp          : {report.timestamp}")
    click.echo(f"Scenarios Evaluated: {report.total_scenarios}")
    click.echo(f"Passed             : {report.total_passed}/{report.total_scenarios} ({report.overall_pass_rate}%)")
    click.echo("\n--- CATEGORY-LEVEL ACCURACY & RECALL ---")
    click.echo(f"{'Category':<22} {'Passed':<10} {'Pass Rate':<12} {'Precision':<10} {'Recall':<10} {'F1':<8}")
    click.echo("-" * 72)
    for cat in report.category_results:
        prec = f"{cat.precision:.2f}" if cat.precision is not None else "N/A"
        rec = f"{cat.recall:.2f}" if cat.recall is not None else "N/A"
        f1 = f"{cat.f1_score:.2f}" if cat.f1_score is not None else "N/A"
        click.echo(f"{cat.category.value:<22} {f'{cat.passed_tests}/{cat.total_tests}':<10} {f'{cat.pass_rate}%':<12} {prec:<10} {rec:<10} {f1:<8}")

    click.echo("\n--- COMPARATIVE PERFORMANCE (Memory Enabled vs Memory Disabled) ---")
    for comp in report.comparative_results:
        click.secho(f"[*] {comp.scenario}", fg="green", bold=True)
        click.echo(f"    Task       : {comp.task_description}")
        click.echo(f"    Inspections: {comp.memory_disabled_inspections} files -> {comp.memory_enabled_inspections} files ({comp.reduction_pct:.1f}% reduction)")
        click.echo(f"    Latency    : {comp.memory_disabled_latency_ms:.1f}ms -> {comp.memory_enabled_latency_ms:.1f}ms ({comp.speedup_multiplier:.1f}x speedup)")
        click.echo(f"    Knowledge  : {'Preserved across sessions' if comp.knowledge_preserved else 'Lost'}")

    click.secho("\n===========================================================================", fg="cyan", bold=True)
    click.secho(f"  RESULT: {report.summary}", fg="green", bold=True)
    click.secho("===========================================================================\n", fg="cyan", bold=True)


if __name__ == "__main__":
    cli()
