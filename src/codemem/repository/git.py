"""Git and filesystem inspection adapter for repository identity and evidence hashing."""

from __future__ import annotations

import ast
import hashlib
import os
import subprocess
from pathlib import Path
from typing import Optional, Tuple


class GitAdapter:
    """Helper for reading Git metadata, file hashes, and AST anchors."""

    @staticmethod
    def resolve_repository_identity(repo_path: str) -> Tuple[str, str, Optional[str], Optional[str], bool]:
        """
        Implements 3-tier hybrid repository ID resolution:
        1. Check .codemem/config.json if present
        2. Normalized Git remote URL + relative subpath + root commit
        3. Local canonical path fallback
        Returns: (repo_id, canonical_root_path, head_commit, remote_url, git_available)
        """
        canonical_path = os.path.realpath(repo_path)
        if not os.path.exists(canonical_path):
            raise ValueError(f"Repository path does not exist: {repo_path}")

        # Tier 1: Check .codemem/config.json
        config_path = os.path.join(canonical_path, ".codemem", "config.json")
        if os.path.isfile(config_path):
            try:
                import json
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    if "repository_id" in cfg:
                        head = GitAdapter.get_head_commit(canonical_path)
                        return (cfg["repository_id"], canonical_path, head, None, head is not None)
            except Exception:
                pass

        # Tier 2: Check Git
        head_commit = GitAdapter.get_head_commit(canonical_path)
        git_root = GitAdapter.get_git_root(canonical_path)

        if head_commit is not None and git_root is not None:
            remote_url = GitAdapter.get_remote_url(canonical_path)
            root_commit = GitAdapter.get_root_commit(canonical_path) or head_commit
            rel_subpath = os.path.relpath(canonical_path, git_root).replace("\\", "/")

            norm_remote = ""
            if remote_url:
                norm_remote = remote_url.strip().lower()
                if norm_remote.endswith(".git"):
                    norm_remote = norm_remote[:-4]
                # Strip ssh/https prefixes
                for prefix in ("https://", "http://", "git@", "ssh://"):
                    if norm_remote.startswith(prefix):
                        norm_remote = norm_remote[len(prefix):]
                        break

            composite_string = f"{norm_remote}:{rel_subpath}:{root_commit}"
            repo_id = hashlib.sha256(composite_string.encode("utf-8")).hexdigest()[:16]
            return (repo_id, canonical_path, head_commit, remote_url, True)

        # Tier 3: Local non-git fallback
        path_hash = hashlib.sha256(canonical_path.lower().encode("utf-8")).hexdigest()[:12]
        repo_id = f"local-{path_hash}"
        return (repo_id, canonical_path, None, None, False)

    @staticmethod
    def get_git_root(path: str) -> Optional[str]:
        """Find the root directory of the git working tree."""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=path,
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                return os.path.realpath(res.stdout.strip())
        except Exception:
            pass
        return None

    @staticmethod
    def get_head_commit(path: str) -> Optional[str]:
        """Get the current HEAD commit hash."""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=path,
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
        return None

    @staticmethod
    def get_root_commit(path: str) -> Optional[str]:
        """Get the initial root commit hash of the git repository."""
        try:
            res = subprocess.run(
                ["git", "rev-list", "--max-parents=0", "HEAD"],
                cwd=path,
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                # In case of multiple roots, take the first line
                return res.stdout.strip().splitlines()[0]
        except Exception:
            pass
        return None

    @staticmethod
    def get_remote_url(path: str) -> Optional[str]:
        """Get the remote origin URL if configured."""
        try:
            res = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                cwd=path,
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
        return None

    @staticmethod
    def compute_file_sha256(full_path: str) -> Optional[str]:
        """Compute SHA-256 of a local file."""
        if not os.path.isfile(full_path):
            return None
        h = hashlib.sha256()
        with open(full_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def compute_snippet_sha256(snippet: str) -> str:
        """Compute normalized SHA-256 of a code excerpt."""
        clean = snippet.strip().replace("\r\n", "\n")
        return hashlib.sha256(clean.encode("utf-8")).hexdigest()

    @staticmethod
    def verify_ast_symbol(full_path: str, symbol: str) -> bool:
        """
        Targeted AST check: Verifies if a symbol (e.g. ClassName or ClassName.method)
        exists in the Python file without whole-program compilation.
        """
        if not full_path.endswith(".py") or not os.path.isfile(full_path):
            return False
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                tree = ast.parse(f.read(), filename=full_path)

            parts = symbol.split(".")
            if len(parts) == 1:
                target = parts[0]
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        if node.name == target:
                            return True
            elif len(parts) == 2:
                cls_name, func_name = parts
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef) and node.name == cls_name:
                        for item in node.body:
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == func_name:
                                return True
        except Exception:
            pass
        return False
