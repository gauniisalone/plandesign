"""Application FastAPI locale. N'écoute que sur 127.0.0.1 par défaut (voir cli.serve)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from openplan import __version__
from openplan.adapters.gateway import ConsentRequiredError, ModelUnavailableError
from openplan.adapters.neural.weights import is_installed
from openplan.adapters.protocols import (
    AdapterUnavailableError,
    DepthEstimator,
    ImageFinisher,
    ImageGenerator,
    LanguageModel,
    RoomElementDetector,
)
from openplan.adapters.registry import build_language_model, load_registry, resolve_device
from openplan.adapters.remote.critique import CritiqueParseError
from openplan.adapters.remote.intention import IntentionParseError
from openplan.adapters.remote.openai_compatible import ModelCallError
from openplan.adapters.remote.vlm_detector import VlmParseError
from openplan.api.doctor import DoctorReport, diagnose
from openplan.api.guards import route_guard
from openplan.api.jobs import (
    JobBusyError,
    JobNotCancellableError,
    JobNotFoundError,
    JobRegistry,
)
from openplan.api.public_errors import public_detail
from openplan.api.routes import (
    ai,
    assistant,
    autoplan,
    board,
    calibration,
    catalog,
    contextual,
    detection,
    exports,
    gallery,
    images,
    layouts,
    measurements,
    models,
    plans,
    projects,
    scene,
    scene3d,
)
from openplan.api.routes import settings as settings_routes
from openplan.api.routes.gallery import purge_all
from openplan.api.security import TokenMissingError, install_security
from openplan.api.settings import Settings, project_root
from openplan.brand import APP_NAME
from openplan.geom.scale import CalibrationError
from openplan.geom.wallgraph import GeometryError
from openplan.monitoring import init_monitoring
from openplan.monitoring import is_active as monitoring_active
from openplan.pipeline.blender_bridge import BlenderError, find_blender
from openplan.pipeline.editing import InvalidEditError
from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.ingestion import ImageNotFoundError, IngestionError
from openplan.pipeline.render import NoCameraError
from openplan.scene.catalog import CatalogError
from openplan.scene.history import HistoryError, PatchRejectedError
from openplan.scene.migrate import UnsupportedSchemaVersionError
from openplan.scene.ops import OpError
from openplan.scene.storage import DiskStorage
from openplan.scene.store import ProjectNotFoundError, ProjectStore

_DEV_ORIGINS = [
    f"http://{host}:{port}" for host in ("127.0.0.1", "localhost") for port in (5173, 4173)
]


__all__ = ["TokenMissingError", "WebBuildMissingError", "create_app"]


@lru_cache(maxsize=4)
def _device_label(requested: str) -> tuple[str, str]:
    """Périphérique effectif (une fois par réglage : `resolve_device` importe torch)."""
    return resolve_device(requested)


class WebBuildMissingError(RuntimeError):
    """`web_dir` ne contient pas de front construit (index.html absent)."""


def _mount_web(app: FastAPI, web_dir: Path) -> None:
    """Sert le front construit sur `/` (même origine que l'API : pas de CORS, pas de second
    port, pas de second terminal). Les routes `/api/*` sont déclarées avant : elles gardent
    la priorité. Le dossier doit contenir `index.html` (Vite : `npm run build`)."""
    if not (web_dir / "index.html").is_file():
        msg = (
            f"front non construit : {web_dir} ne contient pas index.html "
            "(lancer `make start`, ou `cd apps/web && npm run build`)"
        )
        raise WebBuildMissingError(msg)
    app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Corbeille des rendus (US2) : vidée au démarrage et à l'arrêt du serveur."""
    purge_all(app.state.store)
    yield
    purge_all(app.state.store)


def create_app(
    store: ProjectStore | None = None,
    settings: Settings | None = None,
    language_model: LanguageModel | None = None,
    depth_estimator: DepthEstimator | None = None,
    image_generator: ImageGenerator | None = None,
    opening_detector: RoomElementDetector | None = None,
    image_finisher: ImageFinisher | None = None,
) -> FastAPI:
    """`language_model`, `depth_estimator` et `image_generator` remplacent les adaptateurs
    construits depuis les réglages (tests). Les modèles locaux sont sinon chargés au premier
    appel."""
    settings = settings or Settings.from_env()
    # Suivi des erreurs (D4) : seulement avec OPENPLAN_SENTRY_DSN ; sinon rien n'est importé.
    init_monitoring()
    app = FastAPI(title=f"{APP_NAME} API", version=__version__, lifespan=_lifespan)
    app.state.settings = settings
    # Une seule porte pour les fichiers (note 17) : le disque sous le dossier des données.
    app.state.storage = DiskStorage(settings.data_dir)
    app.state.accounts = None
    if settings.accounts_enabled:
        # Comptes (note 17) : importés seulement ici ; le mode local n'en charge rien.
        from openplan.api.accounts.setup import open_accounts

        app.state.accounts = open_accounts(settings)
    app.state.store = store or ProjectStore(project_root(settings), storage=app.state.storage)
    app.state.language_model = language_model or build_language_model(settings.ai)
    app.state.depth_estimator = depth_estimator
    app.state.image_generator = image_generator
    app.state.opening_detector = opening_detector
    app.state.image_finisher = image_finisher
    app.state.jobs = JobRegistry(exposed=not settings.local_only)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_DEV_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Jetons nommés hors boucle locale, journal des accès, en-têtes de sécurité (4.6).
    install_security(app, settings)

    @app.exception_handler(JobBusyError)
    async def _busy(_: Request, exc: JobBusyError) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={"detail": str(exc)},
            headers={"Retry-After": str(int(exc.retry_after_s) + 1)},
        )

    @app.exception_handler(JobNotCancellableError)
    async def _not_cancellable(_: Request, exc: JobNotCancellableError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ProjectNotFoundError)
    async def _not_found(_: Request, exc: ProjectNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": f"projet inconnu : {exc}"})

    @app.exception_handler(InvalidEditError)
    async def _invalid_edit(_: Request, exc: InvalidEditError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": str(exc), "report": exc.report.model_dump(mode="json")},
        )

    @app.exception_handler(ImageNotFoundError)
    async def _image_not_found(_: Request, exc: ImageNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(JobNotFoundError)
    async def _job_not_found(_: Request, exc: JobNotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"detail": f"tâche inconnue (ou serveur redémarré depuis) : {exc}"},
        )

    @app.exception_handler(IngestionError)
    async def _bad_image(_: Request, exc: IngestionError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(ConsentRequiredError)
    async def _consent(_: Request, exc: ConsentRequiredError) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    for upstream in (
        ModelCallError,
        VlmParseError,
        IntentionParseError,
        CritiqueParseError,
        ModelUnavailableError,
    ):

        @app.exception_handler(upstream)
        async def _bad_gateway(_: Request, exc: Exception) -> JSONResponse:
            return JSONResponse(status_code=502, content={"detail": str(exc)})

    for exc_type in (
        OpError,
        GeometryError,
        CalibrationError,
        HistoryError,
        PatchRejectedError,
        UnsupportedSchemaVersionError,
        BlenderError,
        DetectionError,
        AdapterUnavailableError,
        NoCameraError,
        CatalogError,
    ):

        @app.exception_handler(exc_type)
        async def _conflict(_: Request, exc: Exception) -> JSONResponse:
            # API exposée : ni chemin du serveur, ni détail d'installation (contrôle D2).
            detail = public_detail(exc, exposed=not settings.local_only)
            return JSONResponse(status_code=409, content={"detail": detail})

    # Droits route par route (note 17, § 4) : sans comptes, la garde ne refuse rien.
    guarded = [Depends(route_guard)]

    @app.get("/api/health", dependencies=guarded)
    def health() -> dict[str, object]:
        """Public, sans jeton : ce qui marche sur cette installation, sans rien de sensible
        (ni noms de jetons, ni chemins, ni adresses)."""
        device, device_label = _device_label(settings.device)
        weights = [
            e.id
            for e in load_registry()
            if e.kind == "weights" and is_installed(e.id, settings.models_dir, e.sentinel)
        ]
        return {
            "status": "ok",
            "version": __version__,
            "device": device,
            "device_label": device_label,
            "blender": find_blender(settings.blender_bin) is not None,
            "weights": weights,
            "exposed": not settings.local_only,
            # T6 : les mentions du front disent si des rapports d'erreur (filtrés) partent.
            "error_reports": monitoring_active(),
            "tokens": len(settings.tokens),
            "web": settings.web_dir is not None,
        }

    @app.get("/api/doctor", dependencies=guarded)
    def doctor() -> DoctorReport:
        """Bilan de l'installation (même contenu que `openplan doctor`) : ce qui marche et la
        commande pour chaque manque. Derrière le jeton quand l'API est exposée."""
        return diagnose(settings, web_dir=settings.web_dir, probe_ai=True)

    for router in (
        projects.router,
        scene.router,
        measurements.router,
        calibration.router,
        exports.router,
        settings_routes.router,
        scene3d.router,
        images.router,
        detection.router,
        autoplan.router,
        ai.router,
        assistant.router,
        models.router,
        catalog.router,
        layouts.router,
        plans.router,
        gallery.router,
        board.router,
        contextual.router,
    ):
        app.include_router(router, prefix="/api", dependencies=guarded)
    if app.state.accounts is not None:
        from openplan.api.accounts.routes import router as auth_router

        app.include_router(auth_router, prefix="/api", dependencies=guarded)
    if settings.web_dir is not None:
        _mount_web(app, settings.web_dir)
    return app
