"""KAN-105 (L1) : le registre des politiques déclare chaque route (cas d'usage, règle, maillons) ;
la garde compose la chaîne de chaque route depuis lui, et les tables d'avant en sont dérivées.

La preuve qu'aucun droit n'a changé est la matrice figée (`test_kan105_access_matrix`),
identique avant et après le registre ; ce fichier vérifie le registre lui-même."""

from __future__ import annotations

import re
from pathlib import Path

from openplan.api import guards, security, worker_auth
from openplan.api.accounts import extension as extension_module
from openplan.api.accounts import lease, quota
from openplan.api.policy import chain, registry
from openplan.api.policy.registry import BY_KEY, REGISTRY, L, Link, P
from tests.accounts.access_matrix import build
from tests.accounts.test_b1c_guards import _walk

NAME = re.compile(r"[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)*:(get|head|post|put|patch|delete)")


def test_every_api_route_has_exactly_one_use_case(tmp_path: Path, migrated_url: str) -> None:
    """Chaque route `/api` des deux applications (locale, comptes) a sa ligne, et chaque ligne a
    sa route ; un nom de cas d'usage ne sert qu'une fois."""
    apps = build(tmp_path, migrated_url)
    routes: set[tuple[str, str]] = set()
    for app in apps.values():
        for path, route, _inherited in _walk(list(app.routes), "", []):
            if path.startswith("/api"):
                routes |= {(method, path) for method in route.methods or ()}
    apps["comptes"].state.accounts.engine.dispose()
    assert len(BY_KEY) == len(REGISTRY)
    assert sorted(routes - set(BY_KEY)) == [], "route sans cas d'usage déclaré"
    assert sorted(set(BY_KEY) - routes) == [], "cas d'usage sans route"
    names = [entry.name for entry in REGISTRY]
    assert len(set(names)) == len(names)
    assert [n for n in names if not NAME.fullmatch(n)] == []


def test_the_old_tables_are_derived_from_the_registry() -> None:
    """Plus aucune table de droits n'est écrite à la main : chacune est celle du registre."""
    assert guards.PROJECT_ROUTES is registry.PROJECT_ROUTES
    assert guards.GLOBAL_ROUTES is registry.GLOBAL_ROUTES
    assert guards.EXTENSION_ROLES is registry.EXTENSION_ROLES
    assert lease.LEASED_ROUTES is registry.LEASED_ROUTES
    assert lease.IMAGE_LEASED is registry.IMAGE_LEASED
    assert lease.PROJECT_WIDE is registry.PROJECT_WIDE
    assert quota.UPLOADS is registry.UPLOADS
    assert quota.LINKS is registry.LINKS
    assert quota.LAUNCHES is registry.LAUNCHES
    assert worker_auth.RATE_LIMITED is registry.RATE_LIMITED
    assert security.ACCOUNTS_PUBLIC is registry.PUBLIC_PATHS
    assert guards.route_guard is chain.route_guard
    # Une route est de projet OU globale, jamais les deux.
    assert not set(registry.PROJECT_ROUTES) & set(registry.GLOBAL_ROUTES)
    assert len(registry.PROJECT_ROUTES) + len(registry.GLOBAL_ROUTES) == len(REGISTRY)


def test_links_are_declared_where_their_check_runs() -> None:
    """Bail et place ne suivent qu'un rôle sur un projet ; les débits déclarés sont ceux que la
    périphérie applique (porte du poste, intergiciel de session)."""
    for entry in REGISTRY:
        links = set(entry.links)
        if links & (registry.LEASES | registry.SPACES):
            assert entry.access.role is not None, entry.name
        if links & registry.LEASES:
            assert entry.access.policy is None, entry.name
        if entry.access.policy == "extension" and entry.access.role is not None:
            assert Link.SPACE_LINK in links, entry.name
        if Link.WORKER_RATE in links:
            assert entry.access.policy == "worker", entry.name
            assert worker_auth.WORKER_PATH.fullmatch(entry.path), entry.name
        # KAN-105 L2 : seules les écritures de `/api/auth/*` comptent comme des tentatives.
        limited = security.counts_as_attempt(entry.method, entry.path)
        assert (Link.AUTH_RATE in links) == limited, entry.name
        if entry.access.policy == "extension":
            sample = entry.path.replace("{project_id}", "00000000-0000-0000-0000-000000000000")
            assert extension_module.EXTENSION_PATH.fullmatch(sample), entry.name
    public = {e.path for e in REGISTRY if e.access.policy == "public" and "{" not in e.path}
    assert public == registry.PUBLIC_PATHS


def _labels(method: str, path: str) -> list[str]:
    return [step.label for step in chain.CHAINS[(method, path)]]


def test_each_route_chain_is_composed_in_order() -> None:
    """Règle d'accès d'abord, puis bail (il PREND le bail), place, et la trace d'activité."""
    active = "activité du jour"
    assert _labels("GET", P) == ["membre ≥ viewer", active]
    assert _labels("POST", L + "/ops") == ["membre ≥ editor", "bail du plan", active]
    assert _labels("POST", L + "/render") == ["membre ≥ editor", "place du propriétaire", active]
    assert _labels("PUT", L + "/images/{image_id}/depth") == [
        "membre ≥ editor",
        "bail du plan",
        "place du propriétaire",
        active,
    ]
    assert _labels("POST", P + "/undo") == ["membre ≥ editor", "bail du plan", active]
    assert _labels("DELETE", P) == ["membre ≥ owner", active]
    assert _labels("GET", "/api/health") == []
    assert _labels("GET", "/api/projects") == ["session", active]
    assert _labels("GET", "/api/doctor") == ["session", "administrateur", active]
    assert _labels("POST", "/api/worker/claim") == ["jeton du poste"]
    assert _labels("POST", "/api/session") == ["refus : aucune règle"]
    assert _labels("POST", "/api/extension/projects/{project_id}/images") == [
        "jeton du module",
        "membre ≥ editor (jeton du module)",
        "place du propriétaire",
        active,
    ]
    assert _labels("GET", "/api/extension/targets") == ["jeton du module", active]
    assert set(chain.CHAINS) == set(BY_KEY)
