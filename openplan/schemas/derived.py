"""Grandeurs dérivées d'un niveau, calculées à la demande, jamais stockées dans le projet."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from openplan.schemas.common import Point2, StrictModel


class DerivedWall(StrictModel):
    length_m: float
    polygon: list[Point2]
    """Emprise avec épaisseur, antihoraire."""
    midpoint: Point2
    normal: Point2
    """Normale gauche unitaire de l'axe start→end."""


class DerivedRoom(StrictModel):
    area_m2: float
    perimeter_m: float
    polygon: list[Point2]
    """Polygone intérieur (côté intérieur des murs)."""
    centroid: Point2
    usable_area_m2: float | None = None
    """Surface où la hauteur sous pente atteint 1,80 m ; None sans pente dans la pièce."""


class Bounds(StrictModel):
    min: Point2
    max: Point2


class DerivedRoof(StrictModel):
    """Pente vue en plan : ligne d'égout, sens de montée, course jusqu'au faîtage et
    distance de l'égout à laquelle on tient debout (1,80 m)."""

    eave: list[Point2] = Field(min_length=2, max_length=2)
    inward: Point2
    """Normale unitaire à l'égout, vers l'intérieur (sens de montée)."""
    run_m: float
    usable_offset_m: float | None
    """None : le faîtage lui-même est sous 1,80 m."""
    pitch_deg: float
    eave_height: float = 0.0
    pitch: float = 0.0
    """Radians (copie de l'élément, pour les règles d'aménagement)."""


class DerivedStairs(StrictModel):
    """Escalier vu en plan (S1) : symbole normalisé (contour de trémie, marches, ligne de
    foulée et flèche de montée, ligne de coupe, garde-corps) et grandeurs de Blondel."""

    outline: list[Point2]
    treads: list[list[Point2]]
    walk: list[Point2]
    arrow: list[Point2]
    cut: list[Point2]
    arrival: list[Point2]
    railings: list[list[Point2]] = Field(default_factory=list)
    risers: int
    riser_m: float
    going_m: float
    blondel_m: float
    headroom_m: float | None = None
    """Échappée la plus faible au-dessus des marches."""


class DerivedGable(StrictModel):
    """KAN-94 : un pignon, mur en travers du faîtage ; une distance se mesure depuis sa face
    intérieure."""

    wall_id: UUID
    name: str
    face: Point2
    """Le point de l'axe du faîtage au droit de la face intérieure du mur."""


class DerivedRidge(StrictModel):
    """KAN-94 : l'axe du faîtage en plan, d'un pignon à l'autre."""

    start: Point2
    end: Point2
    gables: list[DerivedGable] = Field(default_factory=list)


class DerivedTruss(StrictModel):
    """KAN-94 : une ferme intermédiaire posée sur le faîtage du moment (poteaux et poutre
    calculés), ou la raison pour laquelle elle ne l'est plus (`problem`)."""

    posts: list[list[Point2]] = Field(default_factory=list)
    beam: list[Point2] = Field(default_factory=list)
    beam_height: float | None = None
    lowered: bool = False
    distances: dict[UUID, float] = Field(default_factory=dict)
    """Distance du centre de la ferme à la face intérieure de chaque pignon (mètres)."""
    problem: str | None = None


class DerivedLevel(StrictModel):
    level_id: UUID
    walls: dict[UUID, DerivedWall] = Field(default_factory=dict)
    rooms: dict[UUID, DerivedRoom] = Field(default_factory=dict)
    roofs: dict[UUID, DerivedRoof] = Field(default_factory=dict)
    stairs: dict[UUID, DerivedStairs] = Field(default_factory=dict)
    ridge: DerivedRidge | None = None
    """KAN-94 : le faîtage, s'il y a deux pentes qui se rejoignent."""
    truss_blocked: str | None = None
    """KAN-94 : pourquoi « Ajouter une ferme intermédiaire » est grisé (None : possible)."""
    posts: dict[UUID, list[Point2]] = Field(default_factory=dict)
    """KAN-94 : emprise de chaque poteau seul."""
    trusses: dict[UUID, DerivedTruss] = Field(default_factory=dict)
    loops: list[list[UUID]] = Field(default_factory=list)
    """Boucles fermées détectées, qu'une pièce les représente déjà ou non."""
    bounds: Bounds | None = None
    """Étendue des sommets ; None si le niveau est vide."""
