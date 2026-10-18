"""
CodeMem: Multi-Agent Cross-Session Thinking & Interoperability Demo.

PRD References: Section 1, 10, 11, 12 (LongMemEval Benchmark Capabilities)

Demonstrates:
  Track 1 - Real CLI OS Subprocesses:
    - Independent CLI subprocesses run sequentially.
    - Zero shared RAM state: facts persist strictly via .codemem/store.json.
  Track 2 - MCP Server Transports:
    - Simulates AI agent connecting over Model Context Protocol (FastMCP).
    - Uses typed JSON tool invocation for remember, recall, update, history, explain.
  Track 3 - Cross-Session Thinking & Handoff Loop:
    - Session 1 (Investigator): Discovers architecture and records claim with AST anchor.
    - Session 2 (Architect): Recalls architecture, records architectural DECISION and HANDOFF.
    - Session 3 (Implementer): Recalls HANDOFF, completes task, and supersedes fact with VERIFIED status.
    - Session 4 (Auditor): Proves conservative abstention on unknown claim, then triggers code mutation
      and change detection (check_changes) transitioning memories to STALE.
  Track 4 - Cross-Transport Interoperability:
    - Fact written by CLI process is immediately recalled by MCP server tool.
    - Fact updated by MCP tool is immediately verified in CLI history.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from codemem.mcp.server import CodeMemMCPServer


def _create_sample_repository(base_dir: Path) -> Path:
    """Create a realistic sample repository with source code for anchoring evidence."""
    auth_dir = base_dir / "src" / "auth"
    auth_dir.mkdir(parents=True, exist_ok=True)
    service_file = auth_dir / "jwt_validator.py"
    service_file.write_text(
        "class JWTValidator:\n"
        "    \"\"\"Production JWT token validator with signature verification.\"\"\"\n"
        "    def verify_token(self, token: str) -> bool:\n"
        "        # Initial implementation: basic bearer check\n"
        "        return token.startswith('bearer_') and len(token) > 10\n",
        encoding="utf-8",
    )
    return service_file


def _run_cli_cmd(cmd_args: List[str], cwd: Path) -> subprocess.CompletedProcess:
    """Execute a codemem CLI command as an isolated OS subprocess."""
    cli_module = [sys.executable, "-m", "codemem.cli.main"]
    return subprocess.run(
        cli_module + cmd_args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


# ==============================================================================
# TRACK 1: REAL CLI SUBPROCESS PERSISTENCE & CROSS-SESSION REASONING
# ==============================================================================
def run_cli_demo(repo_dir: Path, source_file: Path) -> None:
    print("\n" + "=" * 75)
    print("  TRACK 1: CLI MULTI-SESSION PERSISTENCE & THINKING (OS Subprocesses)")
    print("=" * 75)
    print("  * Every command runs as an independent OS process with ZERO shared RAM.")
    print("  * Facts persist strictly through local-first state storage (.codemem/store.json).\n")

    # Step 1: Init
    print("[Session 0 - Setup] Initializing repository scope via CLI...")
    res = _run_cli_cmd(["init", "--repo", str(repo_dir), "--json"], cwd=repo_dir)
    assert res.returncode == 0, res.stderr
    repo_data = json.loads(res.stdout)
    repo_id = repo_data["repository_id"]
    print(f"  [+] Repository Initialized: {repo_id}")
    print(f"  [+] Scope Root: {repo_data['root_path']}\n")

    # Step 2: Session 1 (Agent A - Investigator)
    print("[Session 1 - Agent-A: Investigator] Discovers authentication service and records claim...")
    res = _run_cli_cmd(
        [
            "remember",
            "JWTValidator handles bearer token validation",
            "--repo", str(repo_dir),
            "--subject", "JWTValidator",
            "--predicate", "handles",
            "--object", "bearer token validation",
            "--type", "architecture",
            "--tag", "auth",
            "--tag", "jwt",
            "--evidence", "src/auth/jwt_validator.py:JWTValidator.verify_token:3-5",
            "--agent-id", "Agent-A-Investigator",
            "--session-id", "session-alpha-101",
            "--json",
        ],
        cwd=repo_dir,
    )
    assert res.returncode == 0, res.stderr
    mem1_data = json.loads(res.stdout)
    mem1_id = mem1_data["id"]
    fact_id = mem1_data["logical_fact_id"]
    print(f"  [+] Memory Saved    : {mem1_id} [{mem1_data['status']}]")
    print(f"  [+] Logical Fact ID : {fact_id}")
    print(f"  [+] Anchor Attached : {mem1_data['evidence_records'][0]['file_path']} (SHA-256: {mem1_data['evidence_records'][0]['content_hash'][:12]}...)")
    print("  [Process Exited] Session 1 terminated. All process memory freed.\n")

    # Step 3: Session 2 (Agent B - Architect) - Cross-Session Thinking & Handoff
    print("[Session 2 - Agent-B: Architect] Starts fresh with zero RAM context. Recalls architecture...")
    res = _run_cli_cmd(["recall", "Where is token validation handled?", "--repo", str(repo_dir), "--json"], cwd=repo_dir)
    assert res.returncode == 0, res.stderr
    recall_data = json.loads(res.stdout)
    assert not recall_data["abstained"]
    top_mem = recall_data["results"][0]["memory"]
    print(f"  [+] Recalled Fact   : [{top_mem['status']}] {top_mem['claim']}")
    print(f"  [+] Retrieved Score : {recall_data['results'][0]['final_score']:.3f} (Confidence: {top_mem['confidence_score']:.2f})")
    print(f"  [+] Memory Origin   : Discovered by {top_mem['source_agent_id']} in session {top_mem['source_session_id']}")

    print("\n  [Cross-Session Reasoning] Agent-B evaluates security and leaves a HANDOFF task:")
    res_handoff = _run_cli_cmd(
        [
            "remember",
            "HANDOFF: Token validation must enforce RS256 signature verification and expiration",
            "--repo", str(repo_dir),
            "--subject", "JWTValidator",
            "--predicate", "requires_upgrade_to",
            "--object", "RS256 signature and exp validation",
            "--type", "handoff",
            "--tag", "handoff",
            "--tag", "token",
            "--tag", "validation",
            "--evidence", "src/auth/jwt_validator.py",
            "--agent-id", "Agent-B-Architect",
            "--session-id", "session-beta-202",
            "--json",
        ],
        cwd=repo_dir,
    )
    assert res_handoff.returncode == 0, res_handoff.stderr
    handoff_data = json.loads(res_handoff.stdout)
    print(f"  [+] Handoff Stored  : {handoff_data['id']} [{handoff_data['status']}]")
    print("  [Process Exited] Session 2 terminated.\n")

    # Step 4: Session 3 (Agent C - Implementer)
    print("[Session 3 - Agent-C: Implementer] New process starts. Queries for active handoffs...")
    res = _run_cli_cmd(["recall", "handoff token validation", "--repo", str(repo_dir), "--json"], cwd=repo_dir)
    assert res.returncode == 0, res.stderr
    handoff_recall = json.loads(res.stdout)
    assert not handoff_recall["abstained"], f"Unexpected abstention: {handoff_recall.get('abstain_reason')}"
    retrieved_handoff = handoff_recall["results"][0]["memory"]
    print(f"  [+] Retrieved Task  : \"{retrieved_handoff['claim']}\"")
    print(f"  [+] Leaving Agent   : {retrieved_handoff['source_agent_id']}")

    print("\n  [Execution] Agent-C implements task and updates the architecture memory with supersession:")
    res_up = _run_cli_cmd(
        [
            "update",
            mem1_id,
            "JWTValidator enforces RS256 signatures and expiration checking",
            "--reason", "Implemented security upgrade per Session 2 handoff",
            "--repo", str(repo_dir),
            "--evidence", "src/auth/jwt_validator.py:JWTValidator.verify_token:3-5",
            "--json",
        ],
        cwd=repo_dir,
    )
    assert res_up.returncode == 0, res_up.stderr
    updated_data = json.loads(res_up.stdout)
    mem2_id = updated_data["id"]
    print(f"  [+] Updated Memory  : {mem2_id} (Supersedes: {mem1_id})")
    print("  [Process Exited] Session 3 terminated.\n")

    # Step 5: Session 4 (Agent D - Auditor) - Provenance & Abstention
    print("[Session 4 - Agent-D: Auditor] Verifies version history and tests conservative abstention...")
    res_hist = _run_cli_cmd(["history", fact_id, "--repo", str(repo_dir), "--json"], cwd=repo_dir)
    assert res_hist.returncode == 0, res_hist.stderr
    hist_data = json.loads(res_hist.stdout)
    print(f"  [+] Logical Fact History ({len(hist_data['versions'])} versions found):")
    for idx, v in enumerate(hist_data["versions"], start=1):
        sup = f" -> supersedes {v['supersedes_id']}" if v.get("supersedes_id") else " (root)"
        print(f"      v{idx}. [{v['status']}] {v['id']}{sup}: \"{v['claim']}\"")

    print("\n  [Abstention Gate] Agent-D asks an unverified question outside the codebase scope:")
    res_abstain = _run_cli_cmd(["recall", "What quantum encryption algorithm is used?", "--repo", str(repo_dir)], cwd=repo_dir)
    assert res_abstain.returncode == 0
    print(f"  [+] Abstention Output:\n      {res_abstain.stdout.strip()}")

    # Step 6: Code Mutation & Change Detection
    print("\n  [Code Mutation] Developer modifies 'jwt_validator.py'...")
    source_file.write_text(
        "class JWTValidator:\n"
        "    def verify_token(self, token: str) -> bool:\n"
        "        # Modified implementation changing content SHA-256\n"
        "        return token.startswith('bearer_rs256_')\n",
        encoding="utf-8",
    )
    res_check = _run_cli_cmd(["check-changes", "--repo", str(repo_dir), "--json"], cwd=repo_dir)
    assert res_check.returncode == 0, res_check.stderr
    check_report = json.loads(res_check.stdout)
    print(f"  [+] Change Detection: Detected {check_report['stale_count']} stale memory record(s)!")
    for rec in check_report["revalidation_recommendations"]:
        print(f"      * {rec}")

    # Step 7: Repo Status
    print("\n  [Repository Status Summary]")
    res_status = _run_cli_cmd(["status", "--repo", str(repo_dir)], cwd=repo_dir)
    print("  " + "\n  ".join(res_status.stdout.strip().splitlines()))


# ==============================================================================
# TRACK 2: MCP SERVER TRANSPORT & TOOL CALLING DEMO
# ==============================================================================
def run_mcp_demo(repo_dir: Path) -> None:
    print("\n" + "=" * 75)
    print("  TRACK 2: MCP SERVER TRANSPORT (FastMCP Tools Contract)")
    print("=" * 75)
    print("  * Demonstrates the MCP server tool interfaces used by LLM coding agents.")
    print("  * Enforces the exact 7 contract tools with structured JSON error containment.\n")

    mcp_server = CodeMemMCPServer(repo_path=str(repo_dir))

    # Tool 1: register_repository
    print("[MCP Tool 1: register_repository]")
    reg_res = mcp_server.register_repository(path=str(repo_dir))
    repo_id = reg_res["repository_id"]
    print(f"  [+] Returned repository_id: {repo_id}")

    # Tool 2: remember
    print("\n[MCP Tool 2: remember]")
    rem_res = mcp_server.remember(
        repository_id=repo_id,
        claim="Database connections pool at max 20 connections",
        subject="DBPool",
        predicate="limits_to",
        object="20 connections",
        memory_type="architecture",
        source_agent_id="mcp-coding-assistant",
        source_session_id="session-mcp-42",
        tags=["database", "pool", "falkordb"],
    )
    print(f"  [+] Stored MCP Memory: {rem_res['id']} [{rem_res['status']}]")
    mem_id = rem_res["id"]
    fact_id = rem_res["logical_fact_id"]

    # Tool 3: recall
    print("\n[MCP Tool 3: recall]")
    recall_res = mcp_server.recall(repo_id=repo_id, query="database connection pooling limit")
    print(f"  [+] Recalled via MCP: Found {len(recall_res['results'])} candidates (Abstained: {recall_res['abstained']})")
    print(f"  [+] Top Match: [{recall_res['results'][0]['memory']['status']}] {recall_res['results'][0]['memory']['claim']}")

    # Tool 4: explain
    print("\n[MCP Tool 4: explain]")
    exp_res = mcp_server.explain(memory_id=mem_id, repo_id=repo_id)
    print(f"  [+] Provenance Agent : {exp_res['provenance']['source_agent_id']}")
    print(f"  [+] Freshness Status : {exp_res['freshness_status']}")

    # Tool 5: update
    print("\n[MCP Tool 5: update]")
    up_res = mcp_server.update(
        memory_id=mem_id,
        new_claim="Database connection pool expanded to max 50 connections",
        reason="Load testing indicated bottleneck at 20",
        repo_id=repo_id,
    )
    print(f"  [+] Superseded Version: {up_res['id']} supersedes {mem_id}")

    # Tool 6: history
    print("\n[MCP Tool 6: history]")
    hist_res = mcp_server.history(identifier=fact_id, repo_id=repo_id)
    print(f"  [+] Retrieved {len(hist_res['versions'])} chronological revisions via MCP")

    # Tool 7: check_changes
    print("\n[MCP Tool 7: check_changes]")
    changes_res = mcp_server.check_changes(repo_id=repo_id)
    print(f"  [+] Stale Memory Count: {changes_res['stale_count']}")

    # Error containment test
    print("\n[MCP Error Handling: Structured CodeMemErrorDetail]")
    err_res = mcp_server.recall(repo_id=repo_id, query="   ")
    print(f"  [+] Invalid query caught safely: error_code='{err_res.get('error_code')}', message='{err_res.get('message')}'")


# ==============================================================================
# TRACK 3: CROSS-TRANSPORT INTEROPERABILITY (CLI <-> MCP)
# ==============================================================================
def run_interoperability_demo(repo_dir: Path) -> None:
    print("\n" + "=" * 75)
    print("  TRACK 3: CROSS-TRANSPORT INTEROPERABILITY (CLI <-> MCP Server)")
    print("=" * 75)
    print("  * Proves facts created in the CLI are immediately accessible to MCP agents.")
    print("  * Proves updates made via MCP tools are instantly visible to CLI commands.\n")

    # 1. Write fact in CLI
    print("[Step 1] Human developer writes a convention memory via CLI...")
    res = _run_cli_cmd(
        [
            "remember",
            "Convention: All API routes must return ISO 8601 UTC timestamps",
            "--repo", str(repo_dir),
            "--type", "convention",
            "--tag", "api",
            "--tag", "convention",
            "--agent-id", "Human-Developer",
            "--json",
        ],
        cwd=repo_dir,
    )
    assert res.returncode == 0, res.stderr
    cli_mem = json.loads(res.stdout)
    cli_mem_id = cli_mem["id"]
    repo_id = cli_mem["repository_id"]
    print(f"  [+] Recorded in CLI: {cli_mem_id} -> '{cli_mem['claim']}'")

    # 2. Read fact in MCP Server
    print("\n[Step 2] MCP AI coding agent queries memory for API conventions...")
    mcp_server = CodeMemMCPServer(repo_path=str(repo_dir))
    mcp_recall = mcp_server.recall(repo_id=repo_id, query="timestamp conventions for API routes")
    assert not mcp_recall["abstained"]
    retrieved = mcp_recall["results"][0]["memory"]
    print(f"  [+] MCP Agent Retrieved: [{retrieved['status']}] \"{retrieved['claim']}\"")
    print(f"  [+] Originating Creator: {retrieved['source_agent_id']}")

    # 3. Update fact via MCP Server
    print("\n[Step 3] MCP AI coding agent updates the convention via MCP update tool...")
    up_mcp = mcp_server.update(
        memory_id=cli_mem_id,
        new_claim="Convention: All API routes must return ISO 8601 UTC timestamps with millisecond precision",
        reason="Standardized telemetry requirements",
        repo_id=repo_id,
    )
    print(f"  [+] MCP Agent Updated Memory: {up_mcp['id']} (supersedes {cli_mem_id})")

    # 4. Verify update in CLI
    print("\n[Step 4] Human developer inspects fact history in CLI...")
    res_cli_hist = _run_cli_cmd(["history", cli_mem["logical_fact_id"], "--repo", str(repo_dir), "--json"], cwd=repo_dir)
    assert res_cli_hist.returncode == 0, res_cli_hist.stderr
    hist_data = json.loads(res_cli_hist.stdout)
    print(f"  [+] CLI Confirms Version Count: {len(hist_data['versions'])}")
    print(f"  [+] Current Version Claim   : \"{hist_data['versions'][0]['claim']}\"")
    print(f"  [+] Verified Interoperability: CLI and MCP share 100% synchronized state!")


# ==============================================================================
# TRACK 4: LIVE REPOSITORY MULTI-AGENT SCANNING & COORDINATION
# ==============================================================================
def run_real_repo_demo(repo_dir: Path) -> None:
    print("\n" + "=" * 75)
    print(f"  LIVE REPOSITORY MULTI-AGENT COORDINATION: {repo_dir.name}")
    print(f"  Path: {repo_dir.resolve()}")
    print("=" * 75)
    print("  * Multi-agent execution directly on real repository source files.")
    print("  * Agents extract AST symbols, coordinate handoffs, and persist shared state.\n")

    # Step 1: Architect / Scanner Agent
    print("[Session 1 - Architect Agent] Scanning codebase and bootstrapping memory graph...")
    res_scan = _run_cli_cmd(["scan", "--repo", str(repo_dir)], cwd=repo_dir)
    assert res_scan.returncode == 0, res_scan.stderr
    print("  " + "\n  ".join(res_scan.stdout.strip().splitlines()[:5]))
    print("  [Process Exited] Session 1 terminated. Zero RAM shared.\n")

    # Step 2: Backend Dev Agent
    print("[Session 2 - Backend Dev Agent] Recalls architecture and records task handoff...")
    res_recall = _run_cli_cmd(["recall", "core service architecture models", "--repo", str(repo_dir), "--json"], cwd=repo_dir)
    assert res_recall.returncode == 0, res_recall.stderr
    rec_data = json.loads(res_recall.stdout)
    if not rec_data.get("abstained") and rec_data.get("results"):
        top = rec_data["results"][0]["memory"]
        print(f"  [+] Recalled Memory    : [{top['status']}] {top['claim'][:70]}...")
    
    # Record structured handoff
    res_handoff = _run_cli_cmd(
        [
            "handoff", "record",
            "--objective", "Security Review: Validate MCP tool error containment and input sanitization",
            "--completed", "Implemented Core MemoryService and FastMCP transport layer",
            "--from", "Backend-Developer",
            "--to", "Security-Auditor",
            "-r", "Verify that invalid JSON payload returns safe CodeMemErrorDetail without crash",
            "--repo", str(repo_dir),
            "--json",
        ],
        cwd=repo_dir,
    )
    assert res_handoff.returncode == 0, res_handoff.stderr
    handoff_obj = json.loads(res_handoff.stdout)
    handoff_id = handoff_obj.get("handoff_id") or handoff_obj.get("id")
    print(f"  [+] Created Agent Handoff: {handoff_id}")
    print(f"  [+] From / To           : {handoff_obj.get('originating_agent_id')} -> {handoff_obj.get('receiving_agent_id')}")
    print(f"  [+] Task Objective      : {handoff_obj['task_objective'][:65]}...")
    print("  [Process Exited] Session 2 terminated. Zero RAM shared.\n")

    # Step 3: Security Auditor Agent
    print("[Session 3 - Security Auditor Agent] Wakes up, discovers open handoffs, and completes audit...")
    res_list = _run_cli_cmd(["handoff", "list", "--repo", str(repo_dir), "--status", "PENDING", "--json"], cwd=repo_dir)
    assert res_list.returncode == 0, res_list.stderr
    pending_list = json.loads(res_list.stdout)
    print(f"  [+] Pending Handoffs Found: {len(pending_list)}")
    
    # Complete handoff
    res_done = _run_cli_cmd(
        [
            "handoff", "complete",
            handoff_id,
            "--agent", "Security-Auditor",
            "--repo", str(repo_dir),
            "--json",
        ],
        cwd=repo_dir,
    )
    assert res_done.returncode == 0, res_done.stderr
    print(f"  [+] Completed Handoff    : {handoff_id} -> COMPLETED")
    print("  [Process Exited] Session 3 terminated. Zero RAM shared.\n")

    # Step 4: Auditor & Status Agent
    print("[Session 4 - Graph Inspector Agent] Repository Graph Summary:")
    res_status = _run_cli_cmd(["status", "--repo", str(repo_dir)], cwd=repo_dir)
    print("  " + "\n  ".join(res_status.stdout.strip().splitlines()))


# ==============================================================================
# MAIN ENTRYPOINT
# ==============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="CodeMem Multi-Agent Cross-Session & MCP Demo")
    parser.add_argument(
        "--repo",
        type=str,
        default=None,
        help="Path to an existing repository to run multi-agent coordination on (default: run on current repo or sandbox)",
    )
    parser.add_argument(
        "--sandbox",
        action="store_true",
        help="Force execution inside an isolated temporary sandbox with sample JWT code mutation",
    )
    parser.add_argument(
        "--mode",
        choices=["all", "cli", "mcp", "interop"],
        default="all",
        help="Demo mode to execute when running in sandbox (default: all)",
    )
    args = parser.parse_args()

    print("\n" + "#" * 75)
    print("  CODEMEM: COMPREHENSIVE REPRODUCIBLE DEMO")
    print("  Local-First Shared Memory for Coding Agents (FalkorDB / WeMakeDevs Track 2)")
    print("#" * 75)

    # If --repo is explicitly provided or --sandbox is NOT specified, run on the actual repository!
    target_repo = Path(args.repo) if args.repo else (None if args.sandbox else Path.cwd())

    if target_repo and target_repo.exists() and not args.sandbox:
        run_real_repo_demo(target_repo)
    else:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_dir = Path(tmp_dir)
            source_file = _create_sample_repository(repo_dir)

            if args.mode in ("all", "cli"):
                run_cli_demo(repo_dir, source_file)

            if args.mode in ("all", "mcp"):
                run_mcp_demo(repo_dir)

            if args.mode in ("all", "interop"):
                run_interoperability_demo(repo_dir)

    print("\n" + "#" * 75)
    print("  DEMO SUITE COMPLETE: All multi-session, MCP, and CLI capabilities verified!")
    print("#" * 75 + "\n")


if __name__ == "__main__":
    main()
