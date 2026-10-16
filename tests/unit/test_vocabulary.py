"""Vocabulaire (consigne d'augus du 16 octobre) : on ne dit plus « tableau » mais « espace de
travail » dans tout ce que la personne lit : textes du front (chaînes et JSX, libellés
accessibles et textes alternatifs compris), messages de l'API et guide. Le code garde ses noms
(`board`, `BoardCanvas`, routes `/board`) : seuls les commentaires et docstrings peuvent encore
dire « tableau »."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "apps" / "web" / "src"
WORD = re.compile(r"tableau", re.IGNORECASE)

EXCEPTIONS: dict[str, str] = {
    # "chemin:début de ligne" → raison. Vide : aucune exception dans le front aujourd'hui.
}
API_EXCEPTIONS: dict[str, str] = {
    "openplan/pipeline/say.py": "« tableaux » y désigne des objets de décor (des peintures), "
    "dans le lecteur de consignes",
    "openplan/pipeline/openings.py": "« tableau » y désigne une peinture accrochée au mur, "
    "prise à tort pour une baie",
}

_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE = re.compile(r"(?<![:\"'`])//[^\n]*")


def _strip_comments(source: str) -> str:
    """Retire les commentaires TS/TSX (bloc, ligne, et `{/* … */}` du JSX). Les `//` d'une URL
    dans une chaîne (« http:// ») sont gardés grâce au `:` qui les précède."""
    return _LINE.sub("", _BLOCK.sub("", source))


def _web_hits() -> list[str]:
    hits = []
    for path in sorted([*WEB.rglob("*.ts"), *WEB.rglob("*.tsx")]):
        if path.name.endswith(".gen.ts"):
            continue
        text = _strip_comments(path.read_text(encoding="utf-8"))
        for n, line in enumerate(text.splitlines(), 1):
            if WORD.search(line):
                key = f"{path.relative_to(ROOT).as_posix()}:{line.strip()[:60]}"
                if key not in EXCEPTIONS:
                    hits.append(f"{path.relative_to(ROOT).as_posix()}:{n}: {line.strip()}")
    return hits


def _docstrings(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
        # Les chaînes seules dans le corps (documentation d'un champ) ne sont pas lues non plus.
        body = getattr(node, "body", None)
        for child in body if isinstance(body, list) else []:
            if isinstance(child, ast.Expr) and isinstance(child.value, ast.Constant):
                out.add(id(child.value))
    return out


def _api_hits() -> list[str]:
    hits = []
    for path in sorted((ROOT / "openplan").rglob("*.py")):
        if "_bundled" in path.parts or path.relative_to(ROOT).as_posix() in API_EXCEPTIONS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = _docstrings(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in skip
                and WORD.search(node.value)
            ):
                hits.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}: {node.value[:80]}")
    return hits


def test_no_tableau_in_visible_front_text() -> None:
    assert _web_hits() == []


def test_no_tableau_in_api_messages() -> None:
    assert _api_hits() == []


def test_no_tableau_in_the_user_guide() -> None:
    guide = (ROOT / "docs" / "guide").glob("*.md")
    hits = [
        f"{p.name}:{n}"
        for p in guide
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if WORD.search(line)
    ]
    assert hits == []


def test_the_comment_stripper_keeps_strings_and_urls() -> None:
    src = 'const a = "http://x"; // tableau\n/* tableau */ <p>{/* tableau */}Vu</p>'
    out = _strip_comments(src)
    assert "http://x" in out and "Vu" in out and not WORD.search(out)
