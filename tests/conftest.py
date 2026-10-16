"""Fixtures communes."""

from pathlib import Path

import pytest

from openplan.scene.store import ProjectStore
from openplan.schemas import Project
from tests.factories import rect_room_project


@pytest.fixture
def rect_project() -> Project:
    return rect_room_project()


@pytest.fixture
def store(tmp_path: Path) -> ProjectStore:
    return ProjectStore(tmp_path / "data")
