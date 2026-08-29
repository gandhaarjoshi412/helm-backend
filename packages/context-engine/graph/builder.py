from __future__ import annotations
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from packages.context_engine.graph.models import CodeEdge, CodeNode, EdgeType, NodeType
from packages.context_engine.parser.ast_parser import ASTParser, SymbolInfo
from packages.shared.logging import logger


class CodeGraph:
    """
    In-memory and relational code graph supporting dependency queries,
    caller/callee resolution, test mapping, and symbol references.
    """

    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root).resolve()
        self.nodes: Dict[str, CodeNode] = {}
        self.edges: List[CodeEdge] = []
        self._symbol_to_node: Dict[str, List[str]] = {}
        self._file_to_nodes: Dict[str, List[str]] = {}
        self.parser = ASTParser()

    def build_graph(self) -> None:
        """Scan repository files, parse AST, and construct code graph nodes & edges."""
        self.nodes.clear()
        self.edges.clear()
        self._symbol_to_node.clear()
        self._file_to_nodes.clear()

        source_files: List[Path] = []
        for root, dirs, files in os.walk(self.repo_root):
            dirs[:] = [d for d in dirs if d not in [".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", "target", ".cache", ".gemini", ".hermes", "website"]]
            for f in files:
                ext = Path(f).suffix.lower()
                if ext in ASTParser.SUPPORTED_LANGUAGES:
                    source_files.append(Path(root) / f)

        # 1. First pass: Create file nodes and symbol nodes
        for file_path in source_files:
            rel_path = str(file_path.relative_to(self.repo_root))
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            file_node_id = f"file:{rel_path}"
            is_test_file = "test" in rel_path.lower()
            file_node = CodeNode(
                id=file_node_id,
                name=rel_path,
                node_type=NodeType.TEST if is_test_file else NodeType.FILE,
                file_path=rel_path,
                line_start=1,
                line_end=len(content.splitlines()),
            )
            self._add_node(file_node)

            parsed = self.parser.parse_file(rel_path, content)

            # Record imports
            for imp in parsed.imports:
                self.edges.append(
                    CodeEdge(
                        source_id=file_node_id,
                        target_id=f"import:{imp}",
                        edge_type=EdgeType.IMPORTS,
                        metadata={"module": imp},
                    )
                )

            # Record symbols
            for sym in parsed.symbols:
                node_type = NodeType.FUNCTION
                if sym.kind == "class":
                    node_type = NodeType.CLASS
                elif sym.kind == "method":
                    node_type = NodeType.METHOD
                elif sym.kind == "interface":
                    node_type = NodeType.INTERFACE

                sym_node_id = f"sym:{rel_path}:{sym.name}"
                sym_node = CodeNode(
                    id=sym_node_id,
                    name=sym.name,
                    node_type=node_type,
                    file_path=rel_path,
                    line_start=sym.line_start,
                    line_end=sym.line_end,
                    docstring=sym.docstring,
                    signature=sym.signature,
                    metadata={"exported": sym.exported, "dependencies": sym.dependencies},
                )
                self._add_node(sym_node)

                # Edge: File DEFINES Symbol
                self.edges.append(
                    CodeEdge(
                        source_id=file_node_id,
                        target_id=sym_node_id,
                        edge_type=EdgeType.DEFINES,
                    )
                )

        # 2. Second pass: Resolve symbol calls and test coverage edges
        for node_id, node in list(self.nodes.items()):
            if node.node_type in (NodeType.FUNCTION, NodeType.METHOD):
                deps = node.metadata.get("dependencies", [])
                for dep in deps:
                    target_nodes = self._symbol_to_node.get(dep, [])
                    for tgt_id in target_nodes:
                        if tgt_id != node_id:
                            self.edges.append(
                                CodeEdge(
                                    source_id=node_id,
                                    target_id=tgt_id,
                                    edge_type=EdgeType.CALLS,
                                )
                            )

            # Link test files to tested modules
            if node.node_type == NodeType.TEST:
                base_name = Path(node.file_path).stem.replace("test_", "").replace("_test", "")
                for other_node_id, other_node in self.nodes.items():
                    if other_node.node_type == NodeType.FILE and base_name in Path(other_node.file_path).stem:
                        self.edges.append(
                            CodeEdge(
                                source_id=node_id,
                                target_id=other_node_id,
                                edge_type=EdgeType.TESTS,
                            )
                        )

        logger.info(f"Built CodeGraph: {len(self.nodes)} nodes, {len(self.edges)} edges.")

    def _add_node(self, node: CodeNode) -> None:
        self.nodes[node.id] = node
        self._symbol_to_node.setdefault(node.name, []).append(node.id)
        self._file_to_nodes.setdefault(node.file_path, []).append(node.id)

    def find_callers(self, symbol_name: str) -> List[CodeNode]:
        """Find all functions/classes that call this symbol."""
        target_node_ids = set(self._symbol_to_node.get(symbol_name, []))
        caller_ids = set()
        for edge in self.edges:
            if edge.edge_type == EdgeType.CALLS and edge.target_id in target_node_ids:
                caller_ids.add(edge.source_id)
        return [self.nodes[cid] for cid in caller_ids if cid in self.nodes]

    def find_callees(self, symbol_name: str) -> List[CodeNode]:
        """Find all symbols called by this symbol."""
        source_node_ids = set(self._symbol_to_node.get(symbol_name, []))
        callee_ids = set()
        for edge in self.edges:
            if edge.edge_type == EdgeType.CALLS and edge.source_id in source_node_ids:
                callee_ids.add(edge.target_id)
        return [self.nodes[cid] for cid in callee_ids if cid in self.nodes]

    def find_references(self, symbol_name: str) -> List[Dict[str, Any]]:
        """Find everywhere a symbol is defined or referenced across the graph."""
        refs: List[Dict[str, Any]] = []
        for nid in self._symbol_to_node.get(symbol_name, []):
            node = self.nodes.get(nid)
            if node:
                refs.append({
                    "id": node.id,
                    "name": node.name,
                    "kind": node.node_type.value,
                    "file": node.file_path,
                    "lines": f"{node.line_start}-{node.line_end}",
                    "signature": node.signature,
                })
        return refs

    def find_dependents(self, file_path: str) -> List[str]:
        """Find all files that import or depend on the given file/module."""
        stem = Path(file_path).stem
        dependents: Set[str] = set()
        for edge in self.edges:
            if edge.edge_type == EdgeType.IMPORTS:
                if stem in edge.metadata.get("module", ""):
                    source_node = self.nodes.get(edge.source_id)
                    if source_node:
                        dependents.add(source_node.file_path)
        return sorted(list(dependents))

    def get_tests_for_file(self, file_path: str) -> List[str]:
        """Find tests covering the given file."""
        file_node_id = f"file:{file_path}"
        tests = []
        for edge in self.edges:
            if edge.edge_type == EdgeType.TESTS and edge.target_id == file_node_id:
                src = self.nodes.get(edge.source_id)
                if src:
                    tests.append(src.file_path)
        return tests
