"""Fixtures communes."""

import logging
from collections.abc import Iterator
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


@pytest.fixture(autouse=True)
def _app_logging_restored() -> Iterator[None]:
    """KAN-144 : `openplan serve` (et les tests qui appellent `_run_server`) règle le logger
    `openplan` ; on rend son niveau et ses gestionnaires après chaque test, pour qu'un test n'en
    voie pas la sortie d'un autre."""
    logger = logging.getLogger("openplan")
    level, handlers = logger.level, list(logger.handlers)
    yield
    for added in [h for h in logger.handlers if h not in handlers]:
        logger.removeHandler(added)
        added.close()
    logger.setLevel(level)
