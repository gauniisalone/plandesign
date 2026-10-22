"""Dépendances FastAPI : store et réglages attachés à l'application."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request

from openplan.adapters.protocols import (
    DepthEstimator,
    ImageFinisher,
    ImageGenerator,
    LanguageModel,
    RoomElementDetector,
)
from openplan.adapters.registry import (
    build_depth_estimator,
    build_image_finisher,
    build_image_generator,
    build_opening_detector,
    finish_engine,
)
from openplan.api.jobs import JobRegistry
from openplan.api.settings import Settings
from openplan.scene.history import USER_ACTOR
from openplan.scene.store import ProjectStore
from openplan.schemas.scene import Project


def get_store(request: Request) -> ProjectStore:
    store: ProjectStore = request.app.state.store
    return store


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_language_model(request: Request) -> LanguageModel | None:
    model: LanguageModel | None = request.app.state.language_model
    return model


def get_depth_estimator(request: Request) -> DepthEstimator:
    """Construit l'estimateur au premier appel (poids requis) et le garde chargé."""
    state = request.app.state
    if state.depth_estimator is None:
        settings: Settings = state.settings
        state.depth_estimator = build_depth_estimator(settings.models_dir, settings.device)
    estimator: DepthEstimator = state.depth_estimator
    return estimator


def get_image_generator(request: Request) -> ImageGenerator:
    """Construit le générateur au premier appel (poids et licence acceptée requis)."""
    state = request.app.state
    if state.image_generator is None:
        settings: Settings = state.settings
        state.image_generator = build_image_generator(settings.models_dir, settings.device)
    generator: ImageGenerator = state.image_generator
    return generator


def get_image_finisher(request: Request) -> ImageFinisher:
    """Finition IA (D5), construite au premier appel (poids et licence acceptée requis)."""
    state = request.app.state
    if state.image_finisher is None:
        settings: Settings = state.settings
        state.image_finisher = build_image_finisher(
            settings.models_dir, settings.device, settings.finish_engine
        )
    finisher: ImageFinisher = state.image_finisher
    return finisher


def ridge_view_wanted(request: Request) -> bool:
    """KAN-88 : la vue « axe du faîtage » s'ajoute aux vues automatiques des combles quand la
    finition locale est Z-Image (poids installés, carte CUDA). Jamais avec les comptes : en
    ligne, les vues ne changent pas. Ne charge aucun modèle."""
    state = request.app.state
    settings: Settings = state.settings
    if settings.accounts_enabled:
        return False
    if state.image_finisher is not None:
        return getattr(state.image_finisher, "prompt_engine", "sd15") == "zimage"
    engine = finish_engine(settings.models_dir, settings.device, settings.finish_engine)
    return engine == "zimage"


def get_opening_detector(request: Request) -> RoomElementDetector:
    """Détecteur de portes et fenêtres, construit au premier appel (poids requis)."""
    state = request.app.state
    if state.opening_detector is None:
        settings: Settings = state.settings
        state.opening_detector = build_opening_detector(settings.models_dir, settings.device)
    detector: RoomElementDetector = state.opening_detector
    return detector


OpeningDetectorDep = Annotated[RoomElementDetector, Depends(get_opening_detector)]


def get_jobs(request: Request) -> JobRegistry:
    jobs: JobRegistry = request.app.state.jobs
    return jobs


def get_actor(request: Request) -> str:
    """Acteur des gestes de cette requête : `user` sur la boucle locale, `user:<nom>` pour un
    jeton nommé (posé par l'intergiciel de sécurité)."""
    actor: str = getattr(request.state, "actor", USER_ACTOR)
    return actor


ActorDep = Annotated[str, Depends(get_actor)]
JobsDep = Annotated[JobRegistry, Depends(get_jobs)]
StoreDep = Annotated[ProjectStore, Depends(get_store)]
ImageGeneratorDep = Annotated[ImageGenerator, Depends(get_image_generator)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
LanguageModelDep = Annotated[LanguageModel | None, Depends(get_language_model)]
DepthEstimatorDep = Annotated[DepthEstimator, Depends(get_depth_estimator)]


def load_project(project_id: UUID, store: StoreDep) -> Project:
    return store.load(project_id)


ProjectDep = Annotated[Project, Depends(load_project)]
