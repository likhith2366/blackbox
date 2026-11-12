"""blackbox/ must never reach demo/, scenarios/, evaluator_private/ or eval/ (CONTRACT s3).

- No import (static `import`/`from`, or importlib.import_module/__import__ with a literal
  module name) of those packages from any .py file under blackbox/.
- No reference to "evaluator_private" anywhere under blackbox/. For .py files, comments
  and docstrings are exempt (schemas.py/interface.py document the rule in prose); any
  occurrence in code or a non-docstring string literal fails. Non-.py files (prompts,
  data) may not mention it at all.
"""
import ast
import io
import sys
import tokenize
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "blackbox"
FORBIDDEN = ("demo", "scenarios", "evaluator_private", "eval")
SKIP_DIRS = {"__pycache__"}


def _files(suffix=None):
    for p in PKG.rglob("*"):
        if p.is_file() and not (SKIP_DIRS & set(p.parts)):
            if suffix is None or p.suffix == suffix:
                yield p


def _top(mod: str) -> str:
    return mod.split(".")[0]


def _forbidden_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad += [a.name for a in node.names if _top(a.name) in FORBIDDEN]
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and _top(node.module) in FORBIDDEN:
                bad.append(node.module)
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in ("import_module", "__import__") and node.args:
                a0 = node.args[0]
                if isinstance(a0, ast.Constant) and isinstance(a0.value, str) \
                        and _top(a0.value) in FORBIDDEN:
                    bad.append(a0.value)
    return bad


def _code_without_comments_and_docstrings(src: str) -> str:
    tree = ast.parse(src)
    doc_lines: set[tuple[int, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                doc_lines.add((body[0].lineno, body[0].end_lineno))
    keep = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.type == tokenize.STRING and any(a <= tok.start[0] <= b for a, b in doc_lines):
            continue
        keep.append(tok.string)
    return " ".join(keep)


def test_blackbox_package_exists():
    assert PKG.is_dir()


def test_no_forbidden_imports():
    offenders = {}
    for p in _files(".py"):
        bad = _forbidden_imports(p)
        if bad:
            offenders[str(p.relative_to(REPO))] = bad
    assert not offenders, f"blackbox/ imports forbidden packages: {offenders}"


def test_no_evaluator_private_reference():
    offenders = []
    for p in _files():
        if p.suffix == ".pyc":
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = p.read_bytes().decode("latin-1")
        if "evaluator_private" not in text:
            continue
        if p.suffix == ".py":
            if "evaluator_private" in _code_without_comments_and_docstrings(text):
                offenders.append(str(p.relative_to(REPO)))
        else:
            offenders.append(str(p.relative_to(REPO)))
    assert not offenders, f"files under blackbox/ reference evaluator_private: {offenders}"


def test_checker_catches_violations(tmp_path):
    """Self-test of the checker on synthetic sources."""
    f = tmp_path / "x.py"
    f.write_text("import eval.metrics\nfrom scenarios import build\n"
                 "import importlib\nimportlib.import_module('evaluator_private.keys')\n"
                 "from .local import y\nimport evaluation_tools\n")
    assert _forbidden_imports(f) == ["eval.metrics", "scenarios", "evaluator_private.keys"]
    src = '"""mentions evaluator_private in a docstring"""\n# evaluator_private comment\nP = "evaluator_private/x"\n'
    assert "evaluator_private" in _code_without_comments_and_docstrings(src)
    src_ok = '"""mentions evaluator_private"""\n# evaluator_private\nP = 1\n'
    assert "evaluator_private" not in _code_without_comments_and_docstrings(src_ok)
