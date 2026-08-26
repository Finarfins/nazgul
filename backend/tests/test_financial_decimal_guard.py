from __future__ import annotations

import ast
import shutil
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
REQUIRED_FINANCIAL_MODULES = {
    "billing_service.py",
    "invoice_engines.py",
    "money.py",
    "routers/transactions.py",
    "routers/workflow.py",
    "routers/work_order_labor_lines.py",
    "routers/work_order_parts.py",
}
SCOPE_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


class _DirectBody(ast.NodeVisitor):
    def __init__(self):
        self.nodes: list[ast.AST] = []

    def generic_visit(self, node: ast.AST):
        self.nodes.append(node)
        super().generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.nodes.append(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef):
        self.nodes.append(node)

    def visit_Lambda(self, node: ast.Lambda):
        self.nodes.append(node)


def _direct_nodes(scope: ast.AST) -> list[ast.AST]:
    visitor = _DirectBody()
    if isinstance(scope, ast.Lambda):
        visitor.visit(scope.body)
        return visitor.nodes
    expressions = [*scope.decorator_list, *scope.args.defaults]
    expressions.extend(default for default in scope.args.kw_defaults if default is not None)
    for node in [*expressions, *scope.body]:
        visitor.visit(node)
    return visitor.nodes


def _money_exports(root: Path) -> set[str]:
    tree = ast.parse((root / "money.py").read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _money_bindings(tree: ast.AST, exports: set[str]) -> tuple[set[str], set[str]]:
    functions: set[str] = set()
    modules: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[-1] == "money":
            functions.update(alias.asname or alias.name for alias in node.names if alias.name in exports)
        if isinstance(node, ast.Import):
            modules.update(
                alias.asname or alias.name.split(".")[-1]
                for alias in node.names
                if alias.name.split(".")[-1] == "money"
            )
    return functions, modules


def _unsupported_import_violations(path: Path, tree: ast.Module) -> list[str]:
    top_level = {id(node) for node in tree.body}
    violations = []
    for node in ast.walk(tree):
        is_local = id(node) not in top_level
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[-1] == "money" and (is_local or alias.asname is None):
                    violations.append(f"{path}:{node.lineno}: unsupported money import")
        if isinstance(node, ast.ImportFrom):
            imports_money_module = (
                any(alias.name == "money" for alias in node.names)
                and (node.module is None or node.module.split(".")[-1] == "app")
            )
            imports_star = any(alias.name == "*" for alias in node.names)
            from_money = node.module is not None and node.module.split(".")[-1] == "money"
            if (imports_money_module and not from_money) or (from_money and (is_local or imports_star)):
                violations.append(f"{path}:{node.lineno}: unsupported money import")
    return violations


def _imports_money(tree: ast.AST) -> bool:
    functions, modules = _money_bindings(tree, {"*"})
    return bool(functions or modules) or any(
        (
            isinstance(node, ast.ImportFrom)
            and (
                (node.module is not None and node.module.split(".")[-1] == "money")
                or (
                    any(alias.name == "money" for alias in node.names)
                    and (node.module is None or node.module.split(".")[-1] == "app")
                )
            )
        )
        or (
            isinstance(node, ast.Import)
            and any(alias.name.split(".")[-1] == "money" for alias in node.names)
        )
        for node in ast.walk(tree)
    )


def _financial_modules(root: Path) -> list[Path]:
    modules = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if path == root / "money.py" or _imports_money(tree):
            modules.append(path)
    return modules


def _called_names(nodes: list[ast.AST]) -> set[tuple[str, bool]]:
    called: set[tuple[str, bool]] = set()
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            called.add((node.func.id, False))
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in {"self", "cls"}
        ):
            called.add((node.func.attr, True))
    return called


def _calls_money(nodes: list[ast.AST], functions: set[str], modules: set[str], exports: set[str]) -> bool:
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in functions:
            return True
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in modules
            and node.func.attr in exports
        ):
            return True
    return False


def _financial_scopes(path: Path, root: Path) -> list[ast.AST]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    scopes = [node for node in ast.walk(tree) if isinstance(node, SCOPE_TYPES)]
    if path == root / "money.py":
        return [tree, *scopes]
    exports = _money_exports(root)
    functions, modules = _money_bindings(tree, exports)
    direct = {scope: _direct_nodes(scope) for scope in scopes}
    selected = {scope for scope, nodes in direct.items() if _calls_money(nodes, functions, modules, exports)}
    method_owner = {
        method: class_node
        for class_node in ast.walk(tree)
        if isinstance(class_node, ast.ClassDef)
        for method in class_node.body
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    by_name: dict[str, list[ast.AST]] = {}
    class_methods: dict[tuple[ast.ClassDef, str], ast.AST] = {}
    for scope in scopes:
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = method_owner.get(scope)
            if owner is None:
                by_name.setdefault(scope.name, []).append(scope)
            else:
                class_methods[(owner, scope.name)] = scope
    pending = list(selected)
    while pending:
        current = pending.pop()
        nodes = direct[current]
        for called, is_method in _called_names(nodes):
            method_target = class_methods.get((method_owner.get(current), called)) if is_method else None
            targets = [method_target] if method_target is not None else ([] if is_method else by_name.get(called, []))
            for target in targets:
                if target not in selected:
                    selected.add(target)
                    pending.append(target)
    return list(selected)


def _binary_float_violations(path: Path, root: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations = set(_unsupported_import_violations(path, tree))
    scopes = _financial_scopes(path, root)
    method_owner = {
        method: class_node
        for class_node in ast.walk(tree)
        if isinstance(class_node, ast.ClassDef)
        for method in class_node.body
        if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    class_methods = {
        (owner, method.name)
        for method, owner in method_owner.items()
    }
    for scope in scopes:
        nodes = _direct_nodes(scope) if isinstance(scope, SCOPE_TYPES) else ast.walk(scope)
        for node in nodes:
            is_literal = isinstance(node, ast.Constant) and isinstance(node.value, float)
            is_call = isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float"
            if is_literal or is_call:
                violations.add(f"{path}:{node.lineno}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"self", "cls"}
                and (method_owner.get(scope), node.func.attr) not in class_methods
            ):
                violations.add(f"{path}:{node.lineno}: unresolved financial method call")
    return sorted(violations)


def _financial_float_violations(root: Path) -> list[str]:
    return [
        violation
        for path in _financial_modules(root)
        for violation in _binary_float_violations(path, root)
    ]


def test_financial_modules_use_decimal_only():
    discovered = {path.relative_to(APP_ROOT).as_posix() for path in _financial_modules(APP_ROOT)}
    assert REQUIRED_FINANCIAL_MODULES <= discovered
    violations = _financial_float_violations(APP_ROOT)
    assert violations == [], "Binary float finansal yolda kullanılamaz:\n" + "\n".join(violations)


def test_public_guard_rejects_alias_attribute_line_amount_and_transitive_mutations(tmp_path: Path):
    root = tmp_path / "app"
    (root / "routers").mkdir(parents=True)
    shutil.copy2(APP_ROOT / "money.py", root / "money.py")
    source = APP_ROOT / "routers" / "work_order_parts.py"
    target = root / "routers" / "work_order_parts.py"
    shutil.copy2(source, target)
    assert _financial_float_violations(root) == []
    target.write_text(
        source.read_text(encoding="utf-8")
        + """
from app.money import money as aliased_money
import app.money as money_module
def unsafe_line_amount(): return line_amount(1, 1) + 0.1
def unsafe_alias(): return aliased_money(0.2)
def unsafe_attribute(): return money_module.money(0.3)
def unsafe_helper(): return 0.4
def unsafe_transitive(): return money(1) + unsafe_helper()
def unsafe_local_import():
    from app.money import money as local_money
    return local_money(0.5)
def unsafe_default(rate=0.6): return money(rate)
unsafe_lambda = lambda value: money(value) + 0.7
class UnsafeCalculator:
    def helper(self): return 0.8
    def total(self): return money(1) + self.helper()
import app.money
from app import money as imported_module
from app.money import *
""",
        encoding="utf-8",
    )
    assert len(_financial_float_violations(root)) == 12
    shutil.copy2(source, target)
    assert _financial_float_violations(root) == []


def test_nested_scopes_are_isolated_and_reported_once(tmp_path: Path):
    root = tmp_path / "app"
    root.mkdir()
    shutil.copy2(APP_ROOT / "money.py", root / "money.py")
    target = root / "nested.py"
    target.write_text(
        "from app.money import money\n"
        "def outer():\n"
        "    unrelated_ratio = 0.5\n"
        "    def inner():\n"
        "        return money(0.1)\n"
        "    return unrelated_ratio\n",
        encoding="utf-8",
    )
    violations = _financial_float_violations(root)
    assert violations == [f"{target}:5"]


def test_unsupported_import_forms_are_discovered_standalone(tmp_path: Path):
    root = tmp_path / "app"
    root.mkdir()
    shutil.copy2(APP_ROOT / "money.py", root / "money.py")
    sources = {
        "from_app.py": "from app import money as m\ndef total(): return m.money(0.1)\n",
        "unaliased.py": "import app.money\ndef total(): return app.money.money(0.1)\n",
        "star.py": "from app.money import *\ndef total(): return money(0.1)\n",
    }
    for name, source in sources.items():
        target = root / name
        target.write_text(source, encoding="utf-8")
        violations = _financial_float_violations(root)
        assert any(str(target) in violation for violation in violations)
        target.unlink()


def test_unresolved_inherited_financial_helper_fails_closed(tmp_path: Path):
    root = tmp_path / "app"
    root.mkdir()
    shutil.copy2(APP_ROOT / "money.py", root / "money.py")
    target = root / "inherited.py"
    target.write_text(
        "from app.money import money\n"
        "class Base:\n"
        "    def helper(self): return 0.1\n"
        "class Child(Base):\n"
        "    def total(self): return money(1) + self.helper()\n",
        encoding="utf-8",
    )
    assert _financial_float_violations(root) == [
        f"{target}:5: unresolved financial method call"
    ]
