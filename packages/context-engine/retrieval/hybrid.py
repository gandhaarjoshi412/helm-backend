from __future__ import annotations
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import git

from packages.context_engine.graph.builder import CodeGraph
from packages.context_engine.graph.models import NodeType
from packages.shared.logging import logger


@dataclass
class SearchResultItem:
    file_path: str
    line_number: int
    matched_text: str
    score: float = 1.0
    context_type: str = "keyword"  # keyword, symbol, graph, git


@dataclass
class RetrievedContext:
    query: str
    items: List[SearchResultItem] = field(default_factory=list)
    relevant_files: List[str] = field(default_factory=list)
    relevant_symbols: List[Dict[str, Any]] = field(default_factory=list)
    relevant_commits: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""


class HybridRetrievalEngine:
    """
    Hybrid repository context retriever.
    Combines:
    1. Keyword/text search
    2. AST Symbol index lookup
    3. CodeGraph dependency and caller traversal
    4. Git history & commit diff analysis
    """

    def __init__(self, repo_path: str, code_graph: Optional[CodeGraph] = None):
        self.repo_path = Path(repo_path).resolve()
        self.code_graph = code_graph or CodeGraph(str(self.repo_path))
        if not self.code_graph.nodes:
            self.code_graph.build_graph()

    def search_keyword(self, query: str, max_results: int = 20) -> List[SearchResultItem]:
        """Search repository files for keywords or regex."""
        results: List[SearchResultItem] = []
        pattern = re.compile(re.escape(query), re.IGNORECASE)

        for root, dirs, files in os.walk(self.repo_path):
            dirs[:] = [d for d in dirs if d not in [".git", "node_modules", "__pycache__", ".venv", "dist", "build"]]
            for f in files:
                ext = Path(f).suffix.lower()
                if ext in [".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".json", ".md", ".yaml", ".yml"]:
                    full_p = Path(root) / f
                    rel_p = str(full_p.relative_to(self.repo_path))
                    try:
                        lines = full_p.read_text(encoding="utf-8", errors="replace").splitlines()
                        for i, line in enumerate(lines, 1):
                            if pattern.search(line):
                                results.append(
                                    SearchResultItem(
                                        file_path=rel_p,
                                        line_number=i,
                                        matched_text=line.strip()[:200],
                                        score=1.0,
                                        context_type="keyword",
                                    )
                                )
                                if len(results) >= max_results:
                                    return results
                    except Exception:
                        continue
        return results

    def search_symbols(self, query: str) -> List[Dict[str, Any]]:
        """Search code graph nodes for matching symbol names."""
        results = []
        q_lower = query.lower()
        for node in self.code_graph.nodes.values():
            if node.node_type != NodeType.FILE and q_lower in node.name.lower():
                results.append({
                    "name": node.name,
                    "kind": node.node_type.value,
                    "file_path": node.file_path,
                    "lines": f"{node.line_start}-{node.line_end}",
                    "signature": node.signature,
                    "docstring": node.docstring,
                })
        return results

    def get_git_history(self, max_commits: int = 10) -> List[Dict[str, Any]]:
        """Retrieve recent Git commits and touched files."""
        commits_data = []
        try:
            repo = git.Repo(str(self.repo_path))
            for c in list(repo.iter_commits(max_count=max_commits)):
                commits_data.append({
                    "hexsha": c.hexsha[:8],
                    "author": str(c.author),
                    "message": c.message.strip(),
                    "date": c.committed_datetime.isoformat(),
                    "files": list(c.stats.files.keys()) if hasattr(c, "stats") else [],
                })
        except Exception:
            pass
        return commits_data

    def retrieve(self, query: str, max_files: int = 10) -> RetrievedContext:
        """Perform hybrid retrieval to gather scoped, relevant context for an engineering task."""
        keyword_hits = self.search_keyword(query, max_results=15)
        symbol_hits = self.search_symbols(query)
        git_commits = self.get_git_history(max_commits=5)

        file_set = set()
        for item in keyword_hits:
            file_set.add(item.file_path)
        for sym in symbol_hits:
            file_set.add(sym["file_path"])

        for sym in symbol_hits[:3]:
            callers = self.code_graph.find_callers(sym["name"])
            for c in callers:
                file_set.add(c.file_path)

        relevant_files = sorted(list(file_set))[:max_files]

        summary = (
            f"Retrieved {len(relevant_files)} relevant files, "
            f"{len(symbol_hits)} symbols, and {len(keyword_hits)} keyword matches for query: '{query}'"
        )

        return RetrievedContext(
            query=query,
            items=keyword_hits,
            relevant_files=relevant_files,
            relevant_symbols=symbol_hits,
            relevant_commits=git_commits,
            summary=summary,
        )
