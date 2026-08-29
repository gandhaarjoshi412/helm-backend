from __future__ import annotations
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from packages.context_engine.graph.builder import CodeGraph
from packages.context_engine.retrieval.hybrid import HybridRetrievalEngine
from packages.shared.logging import logger


@dataclass
class IndexStats:
    total_files: int = 0
    total_lines: int = 0
    total_symbols: int = 0
    total_edges: int = 0
    duration_ms: int = 0


class RepositoryIndexer:
    """
    Orchestrates repository intelligence indexing:
    Parses code structure, populates the Code Graph, and initializes hybrid retrieval.
    """

    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()
        self.code_graph = CodeGraph(str(self.repo_path))
        self.retrieval_engine: Optional[HybridRetrievalEngine] = None
        self.stats = IndexStats()

    def index(self) -> IndexStats:
        start_time = time.monotonic()
        logger.info(f"Indexing repository at {self.repo_path}...")

        self.code_graph.build_graph()
        self.retrieval_engine = HybridRetrievalEngine(str(self.repo_path), code_graph=self.code_graph)

        total_files = sum(1 for n in self.code_graph.nodes.values() if n.node_type.value in ("file", "test"))
        total_symbols = sum(1 for n in self.code_graph.nodes.values() if n.node_type.value not in ("file", "test"))
        duration_ms = int((time.monotonic() - start_time) * 1000)

        self.stats = IndexStats(
            total_files=total_files,
            total_symbols=total_symbols,
            total_edges=len(self.code_graph.edges),
            duration_ms=duration_ms,
        )
        logger.info(f"Indexing complete: {self.stats}")
        return self.stats
