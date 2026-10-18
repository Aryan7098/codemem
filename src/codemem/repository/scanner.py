"""Automated codebase scanner for bootstrapping CodeMem knowledge graphs from source repos."""

from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path
from typing import List, Optional, Set, Tuple

try:
    import tomllib
except ImportError:
    tomllib = None

from codemem.core.models import (
    EvidenceInput,
    MemoryInput,
    MemoryType,
    ScanReport,
)
from codemem.core.service import MemoryService


class CodebaseScanner:
    """Scans repository manifests and source code AST to bootstrap initial architectural memories."""

    IGNORE_DIRS = {
        ".git", ".venv", "venv", "env", "__pycache__", "node_modules",
        "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        ".codemem", ".idea", ".vscode", "target", "vendor",
    }

    def __init__(self, service: MemoryService) -> None:
        self.service = service

    def scan(self, repo_path: str = ".", max_files: int = 150) -> ScanReport:
        """
        Scan repository at repo_path, register it if not already registered,
        extract dependencies, key architectural classes/functions, and test conventions,
        and commit them as initial verified/observed memories.
        """
        repo_info = self.service.register_repository(repo_path)
        root = Path(repo_info.root_path)

        files_scanned = 0
        memories_created = 0
        dependencies_found: List[str] = []
        symbols_indexed: List[str] = []

        # 1. Scan Project Manifests (Dependencies & Project Config)
        manifest_memories, deps = self._scan_manifests(root, repo_info.repository_id)
        dependencies_found.extend(deps)
        for mem_in in manifest_memories:
            self.service.remember(mem_in)
            memories_created += 1

        # 2. Scan Test Suites & Conventions
        test_memories = self._scan_test_conventions(root, repo_info.repository_id)
        for mem_in in test_memories:
            self.service.remember(mem_in)
            memories_created += 1

        # 3. Scan Code AST for Core Architectural Components
        code_memories, syms, count = self._scan_source_code(root, repo_info.repository_id, max_files=max_files)
        files_scanned += count
        symbols_indexed.extend(syms)
        for mem_in in code_memories:
            self.service.remember(mem_in)
            memories_created += 1

        summary = (
            f"Successfully bootstrapped knowledge graph for {repo_info.repository_id}: "
            f"scanned {files_scanned} files, created {memories_created} initial memories, "
            f"indexed {len(symbols_indexed)} architectural symbols, and detected {len(dependencies_found)} dependencies."
        )

        return ScanReport(
            repository_id=repo_info.repository_id,
            root_path=str(root),
            files_scanned=files_scanned,
            memories_created=memories_created,
            dependencies_found=dependencies_found,
            symbols_indexed=symbols_indexed,
            summary=summary,
        )

    def _scan_manifests(self, root: Path, repo_id: str) -> Tuple[List[MemoryInput], List[str]]:
        memories: List[MemoryInput] = []
        deps: List[str] = []

        # Python: pyproject.toml
        pyproject_file = root / "pyproject.toml"
        if pyproject_file.is_file():
            try:
                content = pyproject_file.read_text(encoding="utf-8")
                name = None
                file_deps: List[str] = []
                if tomllib is not None:
                    parsed = tomllib.loads(content)
                    proj = parsed.get("project", {})
                    name = proj.get("name")
                    file_deps = proj.get("dependencies", [])
                else:
                    m = re.search(r'name\s*=\s*["\']([^"\']+)["\']', content)
                    if m:
                        name = m.group(1)

                if name:
                    memories.append(
                        MemoryInput(
                            repository_id=repo_id,
                            claim=f"Project is named '{name}' configured in pyproject.toml",
                            subject=name,
                            predicate="configured-in",
                            object="pyproject.toml",
                            memory_type=MemoryType.ARCHITECTURE,
                            tags=["project", "configuration", "python"],
                            confidence_score=0.95,
                            evidence=[EvidenceInput(file_path="pyproject.toml")],
                        )
                    )

                if file_deps:
                    for d in file_deps[:15]:
                        dep_name = re.split(r"[><=~!]", d)[0].strip()
                        deps.append(dep_name)
                    dep_str = ", ".join(deps[:10])
                    memories.append(
                        MemoryInput(
                            repository_id=repo_id,
                            claim=f"Project depends on core packages: {dep_str}",
                            subject="dependencies",
                            predicate="requires",
                            object=dep_str,
                            memory_type=MemoryType.DEPENDENCY,
                            tags=["dependency", "python"] + deps[:5],
                            confidence_score=0.95,
                            evidence=[EvidenceInput(file_path="pyproject.toml")],
                        )
                    )
            except Exception:
                pass

        # Python: requirements.txt
        req_file = root / "requirements.txt"
        if req_file.is_file() and not deps:
            try:
                lines = [l.strip() for l in req_file.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
                req_deps = [re.split(r"[><=~!]", l)[0].strip() for l in lines if l]
                deps.extend(req_deps)
                if req_deps:
                    dep_str = ", ".join(req_deps[:10])
                    memories.append(
                        MemoryInput(
                            repository_id=repo_id,
                            claim=f"Project depends on packages specified in requirements.txt: {dep_str}",
                            subject="requirements",
                            predicate="requires",
                            object=dep_str,
                            memory_type=MemoryType.DEPENDENCY,
                            tags=["dependency", "python"],
                            confidence_score=0.90,
                            evidence=[EvidenceInput(file_path="requirements.txt")],
                        )
                    )
            except Exception:
                pass

        # Node / JS: package.json
        pkg_file = root / "package.json"
        if pkg_file.is_file():
            try:
                pkg_data = json.loads(pkg_file.read_text(encoding="utf-8"))
                pkg_name = pkg_data.get("name", "node-project")
                node_deps = list(pkg_data.get("dependencies", {}).keys())
                deps.extend(node_deps)
                memories.append(
                    MemoryInput(
                        repository_id=repo_id,
                        claim=f"Node.js package '{pkg_name}' with dependencies: {', '.join(node_deps[:10]) if node_deps else 'none'}",
                        subject=pkg_name,
                        predicate="configured-in",
                        object="package.json",
                        memory_type=MemoryType.DEPENDENCY,
                        tags=["package", "node", "javascript"] + node_deps[:5],
                        confidence_score=0.95,
                        evidence=[EvidenceInput(file_path="package.json")],
                    )
                )
            except Exception:
                pass

        return memories, deps

    def _scan_test_conventions(self, root: Path, repo_id: str) -> List[MemoryInput]:
        memories: List[MemoryInput] = []
        tests_dir = root / "tests"
        if tests_dir.is_dir():
            test_files = [f.name for f in tests_dir.rglob("test_*.py") if f.is_file()]
            if test_files:
                sample_str = ", ".join(test_files[:5])
                memories.append(
                    MemoryInput(
                        repository_id=repo_id,
                        claim=f"Project maintains a pytest test suite in tests/ containing {len(test_files)} test modules ({sample_str})",
                        subject="test-suite",
                        predicate="located-in",
                        object="tests/",
                        memory_type=MemoryType.CONVENTION,
                        tags=["testing", "pytest", "test-suite"],
                        confidence_score=0.95,
                        evidence=[EvidenceInput(file_path="tests")],
                    )
                )
        return memories

    def _scan_source_code(self, root: Path, repo_id: str, max_files: int) -> Tuple[List[MemoryInput], List[str], int]:
        memories: List[MemoryInput] = []
        symbols: List[str] = []
        files_count = 0

        # Prioritize src/, lib/, or root
        search_dirs = [d for d in [root / "src", root / "lib", root] if d.is_dir()]

        for base_dir in search_dirs:
            for root_dir, dirnames, filenames in os.walk(base_dir):
                dirnames[:] = [d for d in dirnames if d not in self.IGNORE_DIRS]
                for fname in filenames:
                    if files_count >= max_files:
                        break
                    if not fname.endswith(".py") or fname.startswith("test_") or fname.startswith("."):
                        continue

                    full_path = Path(root_dir) / fname
                    try:
                        rel_path = full_path.relative_to(root).as_posix()
                    except ValueError:
                        continue
                    files_count += 1

                    try:
                        code = full_path.read_text(encoding="utf-8", errors="ignore")
                        tree = ast.parse(code, filename=str(full_path))
                    except Exception:
                        continue

                    # Extract top-level classes
                    for node in tree.body:
                        if isinstance(node, ast.ClassDef):
                            cls_name = node.name
                            symbols.append(cls_name)
                            doc = ast.get_docstring(node)
                            doc_excerpt = doc.split("\n")[0] if doc else f"Class defined in {rel_path}"
                            if len(doc_excerpt) > 120:
                                doc_excerpt = doc_excerpt[:117] + "..."

                            # Collect up to 25 core classes
                            if len(memories) < 25:
                                memories.append(
                                    MemoryInput(
                                        repository_id=repo_id,
                                        claim=f"{cls_name} defines {doc_excerpt}",
                                        subject=cls_name,
                                        predicate="defines",
                                        object=rel_path,
                                        memory_type=MemoryType.ARCHITECTURE,
                                        tags=["architecture", cls_name.lower(), fname.replace(".py", "").lower()],
                                        confidence_score=0.85,
                                        evidence=[
                                            EvidenceInput(
                                                file_path=rel_path,
                                                symbol=cls_name,
                                                line_start=node.lineno,
                                                line_end=getattr(node, "end_lineno", node.lineno),
                                            )
                                        ],
                                    )
                                )

        return memories, symbols, files_count
