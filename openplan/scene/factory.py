"""Création de projets neufs."""

from __future__ import annotations

from openplan.schemas.scene import Level, Project


def new_project(name: str, *, level_name: str = "Rez-de-chaussée") -> Project:
    """Projet vide avec un seul niveau, sans image ni mesure."""
    level = Level(name=level_name)
    return Project(name=name, levels={level.id: level})
