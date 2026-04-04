import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


SUPPORTED_SUFFIXES = {".py", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh", ".hxx"}


@dataclass
class OutlineSymbol:
    kind: str
    name: str
    qualname: str
    start_line: int
    end_line: int
    parent: Optional[str]
    display: str


class BaseOutlineProvider:
    def supports(self, path: Path) -> bool:
        raise NotImplementedError

    def extract(self, text: str, path: Path) -> List[OutlineSymbol]:
        raise NotImplementedError


class PythonAstOutlineProvider(BaseOutlineProvider):
    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".py"

    def extract(self, text: str, path: Path) -> List[OutlineSymbol]:
        tree = ast.parse(text, filename=str(path))
        collector = _PythonAstCollector()
        collector.visit(tree)
        return collector.symbols


class RegexOutlineProvider(BaseOutlineProvider):
    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in SUPPORTED_SUFFIXES

    def extract(self, text: str, path: Path) -> List[OutlineSymbol]:
        if path.suffix.lower() == ".py":
            return self._python_outline(text)
        return self._cpp_outline(text)

    def _python_outline(self, text: str) -> List[OutlineSymbol]:
        items: List[OutlineSymbol] = []
        for line_no, raw_line in enumerate(text.splitlines(), start=1):
            stripped = raw_line.lstrip()
            indent = len(raw_line) - len(stripped)
            prefix = "  " * (indent // 4)
            if stripped.startswith("class "):
                match = re.match(r"class\s+([A-Za-z_][A-Za-z0-9_]*)", stripped)
                if match:
                    name = match.group(1)
                    items.append(
                        OutlineSymbol(
                            kind="class",
                            name=name,
                            qualname=name,
                            start_line=line_no,
                            end_line=line_no,
                            parent=None,
                            display="{0}class {1}".format(prefix, name),
                        )
                    )
            elif stripped.startswith("def ") or stripped.startswith("async def "):
                match = re.match(r"(async\s+def|def)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)", stripped)
                if match:
                    name = match.group(2)
                    signature = _trim_signature("{0} {1}({2}".format(match.group(1), name, match.group(3)))
                    items.append(
                        OutlineSymbol(
                            kind="function",
                            name=name,
                            qualname=name,
                            start_line=line_no,
                            end_line=line_no,
                            parent=None,
                            display="{0}{1}".format(prefix, signature),
                        )
                    )
        return items

    def _cpp_outline(self, text: str) -> List[OutlineSymbol]:
        items: List[OutlineSymbol] = []
        for line_no, raw_line in enumerate(text.splitlines(), start=1):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith(("//", "/*", "*", "#")):
                continue
            if re.match(r"(class|struct|enum)\s+[A-Za-z_][A-Za-z0-9_:<>,]*", stripped):
                display = _trim_signature(stripped.rstrip("{").strip())
                name = display.split()[1] if len(display.split()) > 1 else display
                items.append(
                    OutlineSymbol(
                        kind="type",
                        name=name,
                        qualname=name,
                        start_line=line_no,
                        end_line=line_no,
                        parent=None,
                        display=display,
                    )
                )
                continue
            if "(" not in stripped or ")" not in stripped:
                continue
            if stripped.endswith(";") or stripped.endswith("{"):
                if "::" in stripped or re.search(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\(", stripped):
                    display = _trim_signature(stripped.rstrip("{").rstrip(";").strip())
                    name_match = re.search(r"([A-Za-z_][A-Za-z0-9_:]*)\s*\(", display)
                    name = name_match.group(1) if name_match else display
                    items.append(
                        OutlineSymbol(
                            kind="function",
                            name=name,
                            qualname=name,
                            start_line=line_no,
                            end_line=line_no,
                            parent=None,
                            display=display,
                        )
                    )
        return items


class OutlineProviderChain:
    def __init__(self, providers: List[BaseOutlineProvider]):
        self.providers = list(providers)

    def extract(self, text: str, path: Path) -> List[OutlineSymbol]:
        last_error: Optional[Exception] = None
        for provider in self.providers:
            if not provider.supports(path):
                continue
            try:
                symbols = provider.extract(text, path)
            except (SyntaxError, ValueError) as exc:
                last_error = exc
                continue
            if symbols:
                return symbols
        if last_error is not None:
            return []
        return []


class _PythonAstCollector(ast.NodeVisitor):
    def __init__(self):
        self.symbols: List[OutlineSymbol] = []
        self._stack: List[Tuple[str, str]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._append_symbol("class", node.name, node)
        self._stack.append(("class", node.name))
        self.generic_visit(node)
        self._stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function("def", node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function("async def", node)

    def _visit_function(self, prefix: str, node: ast.AST) -> None:
        self._append_symbol("method" if self._current_parent_kind() == "class" else "function", getattr(node, "name"), node, prefix)
        self._stack.append(("function", getattr(node, "name")))
        self.generic_visit(node)
        self._stack.pop()

    def _append_symbol(self, kind: str, name: str, node: ast.AST, prefix: Optional[str] = None) -> None:
        qualname = ".".join([item_name for _, item_name in self._stack] + [name]) if self._stack else name
        parent = self._stack[-1][1] if self._stack else None
        display_prefix = "  " * len(self._stack)
        if kind == "class":
            display = "{0}class {1}".format(display_prefix, name)
        else:
            rendered_prefix = prefix or "def"
            signature = _trim_signature("{0} {1}(...)".format(rendered_prefix, name))
            display = "{0}{1}".format(display_prefix, signature)
        self.symbols.append(
            OutlineSymbol(
                kind=kind,
                name=name,
                qualname=qualname,
                start_line=int(getattr(node, "lineno", 0) or 0),
                end_line=int(getattr(node, "end_lineno", getattr(node, "lineno", 0)) or 0),
                parent=parent,
                display=display,
            )
        )

    def _current_parent_kind(self) -> str:
        if not self._stack:
            return ""
        return self._stack[-1][0]


def default_outline_provider_chain() -> OutlineProviderChain:
    return OutlineProviderChain(
        [
            PythonAstOutlineProvider(),
            RegexOutlineProvider(),
        ]
    )


def _trim_signature(text: str, max_length: int = 120) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[:max_length] + " ..."
