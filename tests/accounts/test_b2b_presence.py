"""B2b : curseurs, sélections et « Suivre » par le WebSocket du projet. Éphémère, sans écriture :
le serveur valide, relaie aux autres personnes (jamais à ses propres onglets) et n'envoie la vue
qu'à ceux qui suivent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.accounts.test_b2a_live import _team, _ws


def _ping(ws: Any) -> None:
    """Rien d'autre n'était en attente : le pong arrive tout de suite."""
    ws.send_text('{"type":"ping"}')
    assert ws.receive_json() == {"type": "pong"}


def test_cursor_and_selection_reach_the_others_only(tmp_path: Path, migrated_url: str) -> None:
    owner, guest, _, pid = _team(tmp_path, migrated_url)
    with _ws(owner, pid) as mine, _ws(guest, pid) as hers:
        me = mine.receive_json()["you"]["user_id"]
        hers.receive_json()
        mine.receive_json()  # arrivée de l'invitée
        with _ws(owner, pid) as my_tab:  # un second onglet du propriétaire
            my_tab.receive_json()
            mine.send_json({"type": "cursor", "x": 120.5, "y": -40})
            assert hers.receive_json() == {"type": "cursor", "user_id": me, "x": 120.5, "y": -40.0}
            mine.send_json({"type": "cursor", "x": None})
            assert hers.receive_json() == {"type": "cursor", "user_id": me, "x": None}
            mine.send_json({"type": "select", "ids": ["a", "b", "a"]})
            assert hers.receive_json() == {"type": "select", "user_id": me, "ids": ["a", "b"]}
            _ping(mine)
            _ping(my_tab)  # ses propres onglets n'entendent rien
        # Invalides : ignorés, la connexion reste ouverte.
        for bad in (
            {"type": "cursor", "x": "1", "y": 2},
            {"type": "cursor", "x": 1e12, "y": 2},
            {"type": "cursor", "x": True, "y": 2},
            {"type": "select", "ids": ["x" * 65]},
            {"type": "select", "ids": [str(i) for i in range(101)]},
            {"type": "inconnu"},
            [1, 2],
        ):
            mine.send_json(bad)
        mine.send_text("pas du json")
        _ping(hers)
        _ping(mine)


def test_newcomers_get_the_current_selections(tmp_path: Path, migrated_url: str) -> None:
    owner, guest, _, pid = _team(tmp_path, migrated_url)
    with _ws(owner, pid) as mine:
        me = mine.receive_json()["you"]["user_id"]
        mine.send_json({"type": "select", "ids": ["note-1"]})
        _ping(mine)
        with _ws(guest, pid) as hers:
            hello = hers.receive_json()
            assert hello["selections"] == {me: ["note-1"]}
            assert hello["followed"] == 0


def test_follow_sends_the_view_to_followers_only(tmp_path: Path, migrated_url: str) -> None:
    owner, guest, _, pid = _team(tmp_path, migrated_url)
    with _ws(owner, pid) as mine, _ws(guest, pid) as hers:
        me = mine.receive_json()["you"]["user_id"]
        her_id = hers.receive_json()["you"]["user_id"]
        mine.receive_json()  # arrivée de l'invitée
        # Personne ne me suit : ma vue ne part nulle part.
        mine.send_json({"type": "view", "cx": 10, "cy": 20, "zoom": 1.5})
        _ping(hers)
        # Elle me suit : on me dit combien me suivent, et ma vue lui arrive.
        hers.send_json({"type": "follow", "user_id": me})
        assert mine.receive_json() == {"type": "followed", "count": 1}
        mine.send_json({"type": "view", "cx": 10, "cy": 20, "zoom": 1.5})
        assert hers.receive_json() == {
            "type": "view",
            "user_id": me,
            "cx": 10.0,
            "cy": 20.0,
            "zoom": 1.5,
        }
        # Zoom hors limites, se suivre soi-même, suivre un absent : ignorés.
        mine.send_json({"type": "view", "cx": 0, "cy": 0, "zoom": 100})
        hers.send_json({"type": "follow", "user_id": her_id})
        hers.send_json({"type": "follow", "user_id": "00000000-0000-0000-0000-000000000000"})
        _ping(hers)
        _ping(mine)
        # Elle arrête : compteur à 0, plus de vue.
        hers.send_json({"type": "follow", "user_id": None})
        assert mine.receive_json() == {"type": "followed", "count": 0}
        mine.send_json({"type": "view", "cx": 1, "cy": 2, "zoom": 1})
        _ping(hers)
        # Elle me suit puis part : le compteur redescend.
        hers.send_json({"type": "follow", "user_id": me})
        assert mine.receive_json() == {"type": "followed", "count": 1}
    with _ws(owner, pid) as again:
        assert again.receive_json()["followed"] == 0
