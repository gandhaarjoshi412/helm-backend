from __future__ import annotations
import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


@dataclass
class SymbolInfo:
    name: str
    kind: str  # function, class, method, interface, variable, type
    file_path: str
    line_start: int
    line_end: int
    docstring: Optional[str] = None
    signature: Optional[str] = None
    exported: bool = True
    dependencies: List[str] = field(default_factory=list)
    callers: List[str] = field(default_factory=list)


@dataclass
class ParsedFileResult:
    file_path: str
    language: str
    symbols: List[SymbolInfo] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    exports: List[str] = field(default_factory=list)
    lines_of_code: int = 0


class ASTParser:
    """
    Multi-language AST and symbol parser supporting Python, TypeScript/JavaScript, and Go.
    Extracts symbols, function signatures, dependencies, imports, and call relationships.
    """

    SUPPORTED_LANGUAGES = {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".go": "go",
    }

    def detect_language(self, file_path: str) -> Optional[str]:
        ext = Path(file_path).suffix.lower()
        return self.SUPPORTED_LANGUAGES.get(ext)

    def parse_file(self, file_path: str, content: str) -> ParsedFileResult:
        lang = self.detect_language(file_path)
        lines = content.splitlines()
        result = ParsedFileResult(file_path=file_path, language=lang or "unknown", lines_of_code=len(lines))

        if not lang:
            return result

        if lang == "python":
            return self._parse_python(file_path, content, result)
        elif lang in ("typescript", "javascript"):
            return self._parse_typescript(file_path, content, result)
        elif lang == "go":
            return self._parse_go(file_path, content, result)

        return result

    def _parse_python(self, file_path: str, content: str, result: ParsedFileResult) -> ParsedFileResult:
        try:
            tree = ast.parse(content)
        except Exception:
            # Fallback regex parsing if syntax error
            return self._fallback_regex_parse(file_path, content, result)

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    result.imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    result.imports.append(f"{module}.{alias.name}" if module else alias.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sym = SymbolInfo(
                    name=node.name,
                    kind="function",
                    file_path=file_path,
                    line_start=node.lineno,
                    line_end=getattr(node, "end_lineno", node.lineno),
                    docstring=ast.get_docstring(node),
                    signature=f"def {node.name}(...)",
                    exported=not node.name.startswith("_"),
                )
                # Find called functions inside body
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                        sym.dependencies.append(sub.func.id)
                result.symbols.append(sym)
            elif isinstance(node, ast.ClassDef):
                sym = SymbolInfo(
                    name=node.name,
                    kind="class",
                    file_path=file_path,
                    line_start=node.lineno,
                    line_end=getattr(node, "end_lineno", node.lineno),
                    docstring=ast.get_docstring(node),
                    signature=f"class {node.name}",
                    exported=not node.name.startswith("_"),
                )
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_sym = SymbolInfo(
                            name=f"{node.name}.{item.name}",
                            kind="method",
                            file_path=file_path,
                            line_start=item.lineno,
                            line_end=getattr(item, "end_lineno", item.lineno),
                            docstring=ast.get_docstring(item),
                            signature=f"def {item.name}(self, ...)",
                            exported=not item.name.startswith("_"),
                        )
                        result.symbols.append(method_sym)
                result.symbols.append(sym)

        return result

    def _parse_typescript(self, file_path: str, content: str, result: ParsedFileResult) -> ParsedFileResult:
        # Regex / token-based AST parser for TypeScript/JavaScript
        lines = content.splitlines()

        # Extract imports: import { a, b } from './module'
        import_pattern = re.compile(r'import\s+(?:(?:{[^}]+}|\*\s+as\s+\w+|\w+)\s+from\s+)?[\'"]([^\'"]+)[\'"]')
        for match in import_pattern.finditer(content):
            result.imports.append(match.group(1))

        # Extract functions: function foo(...), const foo = (...) =>
        fn_pattern = re.compile(
            r'^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z0-9_$]+)\s*\((.*?)\)',
            re.MULTILINE,
        )
        for match in fn_pattern.finditer(content):
            name = match.group(1)
            line_no = content[: match.start()].count("\n") + 1
            result.symbols.append(
                SymbolInfo(
                    name=name,
                    kind="function",
                    file_path=file_path,
                    line_start=line_no,
                    line_end=line_no + 10,
                    signature=match.group(0).strip(),
                    exported="export" in match.group(0),
                )
            )

        # Extract classes: class Foo, export class Foo
        class_pattern = re.compile(r'^(?:export\s+)?class\s+([A-Za-z0-9_$]+)', re.MULTILINE)
        for match in class_pattern.finditer(content):
            name = match.group(1)
            line_no = content[: match.start()].count("\n") + 1
            result.symbols.append(
                SymbolInfo(
                    name=name,
                    kind="class",
                    file_path=file_path,
                    line_start=line_no,
                    line_end=line_no + 20,
                    signature=match.group(0).strip(),
                    exported="export" in match.group(0),
                )
            )

        # Extract interfaces: interface Foo
        interface_pattern = re.compile(r'^(?:export\s+)?interface\s+([A-Za-z0-9_$]+)', re.MULTILINE)
        for match in interface_pattern.finditer(content):
            name = match.group(1)
            line_no = content[: match.start()].count("\n") + 1
            result.symbols.append(
                SymbolInfo(
                    name=name,
                    kind="interface",
                    file_path=file_path,
                    line_start=line_no,
                    line_end=line_no + 10,
                    signature=match.group(0).strip(),
                    exported="export" in match.group(0),
                )
            )

        return result

    def _parse_go(self, file_path: str, content: str, result: ParsedFileResult) -> ParsedFileResult:
        # Extract package and imports
        import_block = re.findall(r'import\s*\((.*?)\)', content, re.DOTALL)
        for block in import_block:
            for line in block.splitlines():
                clean = line.strip().strip('"')
                if clean:
                    result.imports.append(clean)
        single_imports = re.findall(r'import\s+"([^"]+)"', content)
        result.imports.extend(single_imports)

        # Functions: func Foo(...) or func (r *Receiver) Foo(...)
        func_pattern = re.compile(r'^func\s+(?:\([^)]+\)\s+)?([A-Za-z0-9_]+)\s*\((.*?)\)', re.MULTILINE)
        for match in func_pattern.finditer(content):
            name = match.group(1)
            line_no = content[: match.start()].count("\n") + 1
            is_exported = name[0].isupper() if name else False
            result.symbols.append(
                SymbolInfo(
                    name=name,
                    kind="function",
                    file_path=file_path,
                    line_start=line_no,
                    line_end=line_no + 10,
                    signature=match.group(0).strip(),
                    exported=is_exported,
                )
            )

        # Structs / Interfaces: type Foo struct / type Foo interface
        type_pattern = re.compile(r'^type\s+([A-Za-z0-9_]+)\s+(struct|interface)', re.MULTILINE)
        for match in type_pattern.finditer(content):
            name, kind = match.groups()
            line_no = content[: match.start()].count("\n") + 1
            is_exported = name[0].isupper() if name else False
            result.symbols.append(
                SymbolInfo(
                    name=name,
                    kind=kind,
                    file_path=file_path,
                    line_start=line_no,
                    line_end=line_no + 15,
                    signature=match.group(0).strip(),
                    exported=is_exported,
                )
            )

        return result

    def _fallback_regex_parse(self, file_path: str, content: str, result: ParsedFileResult) -> ParsedFileResult:
        fn_pattern = re.compile(r'^(?:def|async def)\s+([A-Za-z0-9_]+)', re.MULTILINE)
        for match in fn_pattern.finditer(content):
            name = match.group(1)
            line_no = content[: match.start()].count("\n") + 1
            result.symbols.append(
                SymbolInfo(
                    name=name,
                    kind="function",
                    file_path=file_path,
                    line_start=line_no,
                    line_end=line_no + 10,
                    exported=not name.startswith("_"),
                )
            )
        return result
