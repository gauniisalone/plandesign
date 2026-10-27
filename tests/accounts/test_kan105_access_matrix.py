"""KAN-105 (L0) : la matrice des droits de l'API est figée. Toute route nouvelle, règle changée
ou décision ajoutée dans le corps d'une route change la matrice : le test échoue tant que le
fichier de référence n'est pas régénéré, et le changement se lit alors en revue."""

from __future__ import annotations

import difflib
from pathlib import Path

from tests.accounts.access_matrix import REFERENCE, build, render


def test_the_access_matrix_matches_its_reference(tmp_path: Path, migrated_url: str) -> None:
    apps = build(tmp_path, migrated_url)
    current = render(apps)
    apps["comptes"].state.accounts.engine.dispose()
    expected = REFERENCE.read_text(encoding="utf-8")
    if current != expected:
        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                current.splitlines(keepends=True),
                "access_matrix.md (référence)",
                "matrice actuelle",
                n=0,
            )
        )
        raise AssertionError(
            "Les droits de l'API ont changé. Si c'est voulu, régénérer la référence :\n"
            "  uv run python -m tests.accounts.access_matrix > tests/accounts/access_matrix.md\n"
            + diff
        )
