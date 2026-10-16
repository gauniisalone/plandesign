"""Programme d'un logement (accueil L3, note 12 § 4) : ce que le texte et les réponses guidées
disent du logement à dessiner (type, surface, chambres, cuisine, bureau, pièces d'eau…).

Lecteur à règles fixes, hors ligne (essai L3 du 8 octobre : 56 champs sur 56 sur 21 phrases) ;
le modèle distant (Gemini) le relit seulement si l'accord du projet le permet, texte seul, et
les règles reprennent la main s'il échoue. Les réponses guidées priment toujours.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Literal

from pydantic import Field, ValidationError

from openplan.adapters.gateway import ConsentRequiredError, ModelGateway, ModelUnavailableError
from openplan.adapters.protocols import LanguageModel, LlmMessage, LlmRequest
from openplan.pipeline.starter import area_in_text
from openplan.schemas.common import StrictModel
from openplan.schemas.home import GuidedAnswers
from openplan.schemas.scene import Project

Kind = Literal["studio", "appartement", "maison"]
Kitchen = Literal["ouverte", "fermee"]
Office = Literal["", "coin", "piece"]
Extra = Literal["cellier", "dressing"]
DEFAULT_AREAS = {0: 30.0, 1: 45.0, 2: 60.0, 3: 80.0}
"""Surface supposée selon le nombre de chambres, quand rien n'est dit."""
SIMPLIFIED_ABOVE_M2 = 90.0
"""Au-delà, une seule circulation donne un plan long : le bandeau le dit (« plan simplifié »)."""


class Programme(StrictModel):
    kind: Kind = "appartement"
    area_m2: float = Field(default=60.0, ge=8.0, le=400.0)
    bedrooms: int = Field(default=0, ge=0, le=6)
    kitchen: Kitchen = "ouverte"
    office: Office = ""
    bathrooms: int = Field(default=1, ge=1, le=3)
    shower_rooms: int = Field(default=0, ge=0, le=2)
    wc: bool = False
    extras: list[Extra] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    """Ce qui a été supposé (« surface supposée : 60 m² »)."""
    by: str = "phrases"


class ProgrammeReading(StrictModel):
    programme: Programme | None
    """None : le texte ne décrit pas un logement (une pièce, des combles, une ambiance)."""
    understanding: Literal["model", "phrases"] = "phrases"
    notice: str = ""


def fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn").replace("’", "'")


NUMBERS = {"un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6}
NUM = r"(\d|un|une|deux|trois|quatre|cinq|six)"
DWELLING = re.compile(
    r"\b(studio|appartement|appart|logement|maison|pavillon|longere|plain-pied|duplex|loft|"
    r"[tf][1-6]|[1-6] pieces)\b"
)
ROOMS = re.compile(NUM + r"\s+(?:seule\s+|petites?\s+|grandes?\s+)?chambres?\b")


def _num(word: str) -> int:
    return int(word) if word.isdigit() else NUMBERS[word]


def describes_dwelling(text: str, answers: GuidedAnswers | None) -> bool:
    """Un logement à dessiner (plusieurs pièces), pas une pièce seule ni des combles."""
    if answers is not None and answers.space is not None:
        return answers.space in ("appartement", "maison", "studio")
    if answers is not None and answers.bedrooms and answers.bedrooms >= 2:
        return True
    t = fold(text)
    if DWELLING.search(t):
        return True
    # « une chambre de 12 m² » est une pièce seule ; « deux chambres », un logement.
    rooms = ROOMS.search(t)
    return rooms is not None and _num(rooms.group(1)) >= 2


def read_rules(text: str, answers: GuidedAnswers | None = None) -> Programme:
    """Règles fixes ; les réponses guidées priment."""
    t = fold(text)
    kind: Kind = "appartement"
    if re.search(r"\bstudio\b", t):
        kind = "studio"
    elif re.search(r"\b(maison|pavillon|longere|plain-pied)\b", t):
        kind = "maison"
    area = area_in_text(text)
    bedrooms: int | None = None
    m = re.search(r"\b[tf]([1-6])\b", t) or re.search(r"\b([1-6]) pieces\b", t)
    if m:
        bedrooms = max(0, int(m.group(1)) - 1)
        if bedrooms == 0:
            kind = "studio"
    m = ROOMS.search(t)
    if m:
        bedrooms = _num(m.group(1))
        # « deux chambres d'enfants et une suite parentale »
        if re.search(r"suite parentale|chambre parentale", t) and re.search(
            r"chambres? (d'enfants?|enfants?)", t
        ):
            bedrooms += 1
    kitchen: Kitchen = (
        "fermee" if re.search(r"cuisine (fermee|separee|independante)", t) else "ouverte"
    )
    office: Office = ""
    if re.search(r"(coin|espace|recoin) (de )?(bureau|travail)|bureau dans le sejour", t):
        office = "coin"
    elif re.search(r"\bbureau\b", t):
        office = "piece"
    bathrooms = 1
    m = re.search(NUM + r"\s+salles? de bains?\b", t)
    if m:
        bathrooms = min(3, _num(m.group(1)))
    shower_rooms = 0
    if re.search(r"salles? d'eau", t):
        m = re.search(NUM + r"\s+salles? d'eau", t)
        shower_rooms = min(2, _num(m.group(1))) if m else 1
    wc: bool | None = (
        True if re.search(r"(wc|toilettes?) (separes?|independants?|a part)", t) else None
    )
    extras: list[Extra] = []
    for word, extra in (("cellier", "cellier"), ("buanderie", "cellier"), ("dressing", "dressing")):
        if word in t and extra not in extras:
            extras.append(extra)  # type: ignore[arg-type]
    return _complete(
        kind,
        area,
        bedrooms,
        kitchen,
        office,
        bathrooms,
        shower_rooms,
        wc,
        extras,
        answers,
        "phrases",
    )


def _complete(  # noqa: PLR0913, PLR0917 - les champs du programme
    kind: Kind,
    area: float | None,
    bedrooms: int | None,
    kitchen: Kitchen,
    office: Office,
    bathrooms: int,
    shower_rooms: int,
    wc: bool | None,
    extras: list[Extra],
    answers: GuidedAnswers | None,
    by: str,
) -> Programme:
    """Les réponses guidées par-dessus ; ce qui manque est supposé, et dit."""
    notes: list[str] = []
    if answers is not None:
        if answers.space in ("appartement", "maison", "studio"):
            kind = answers.space
        if answers.area_m2:
            area = answers.area_m2
        if answers.bedrooms is not None:
            bedrooms = answers.bedrooms
    if kind == "studio":
        bedrooms = 0
    if area is None:
        area = DEFAULT_AREAS.get(bedrooms or 0, 95.0)
        notes.append(f"surface supposée : {area:g} m²")
    if bedrooms is None:
        bedrooms = 0 if area < 35 else 1 if area < 50 else 2 if area < 70 else 3
        notes.append(
            "chambres supposées : aucune" if bedrooms == 0 else f"chambres supposées : {bedrooms}"
        )
    return Programme(
        kind=kind,
        area_m2=min(400.0, max(8.0, area)),
        bedrooms=min(6, bedrooms),
        kitchen=kitchen,
        office=office,
        bathrooms=bathrooms,
        shower_rooms=shower_rooms,
        wc=bool(wc) if wc is not None else bedrooms >= 2,
        extras=extras,
        notes=notes,
        by=by,
    )


# ------------------------------------------------------------------ modèle (case Gemini)
SYSTEM_PROMPT = (
    "Tu lis la description d'un logement et tu rends son programme en JSON strict, sans texte "
    "autour. Tu n'inventes rien : un champ non dit est absent."
)
USER_TEMPLATE = """Champs permis :
- kind : studio | appartement | maison
- area_m2 : nombre (surface habitable totale)
- bedrooms : entier de 0 à 6
- kitchen : ouverte | fermee
- office : coin | piece (coin bureau dans le séjour, ou pièce bureau)
- bathrooms : entier de 1 à 3 ; shower_rooms : entier de 0 à 2
- wc : true si les WC sont séparés
- extras : liste parmi cellier, dressing

Description : « {text} »"""


def read_with_model(
    text: str,
    answers: GuidedAnswers | None,
    *,
    model: LanguageModel,
    gateway: ModelGateway,
    project: Project,
) -> Programme:
    """Le modèle (texte seul, accord du projet) ; ce qu'il oublie, les règles le donnent."""
    send = gateway.bind(model, project)
    response = send(
        LlmRequest(
            purpose="intention",
            messages=[
                LlmMessage(role="system", content=SYSTEM_PROMPT),
                LlmMessage(role="user", content=USER_TEMPLATE.format(text=text[:300])),
            ],
            json_mode=True,
            temperature=0.0,
            max_tokens=300,
        )
    )
    raw = response.text.strip().removeprefix("```json").removesuffix("```").strip()
    data = json.loads(raw)
    if not isinstance(data, dict):
        msg = "réponse du modèle : un objet JSON est attendu"
        raise ValueError(msg)
    rules = read_rules(text)
    fields = rules.model_dump()
    for key in (
        "kind",
        "area_m2",
        "bedrooms",
        "kitchen",
        "office",
        "bathrooms",
        "shower_rooms",
        "wc",
        "extras",
    ):
        if key in data and data[key] is not None:
            fields[key] = data[key]
    if "area_m2" not in data and area_in_text(text) is None:
        fields["area_m2"] = None
    if "bedrooms" not in data and not ROOMS.search(fold(text)):
        fields["bedrooms"] = None
    checked = Programme.model_validate(
        {**fields, "area_m2": fields["area_m2"] or 60.0, "bedrooms": fields["bedrooms"] or 0}
    )
    return _complete(
        checked.kind,
        fields["area_m2"],
        fields["bedrooms"],
        checked.kitchen,
        checked.office,
        checked.bathrooms,
        checked.shower_rooms,
        checked.wc if "wc" in data else None,
        checked.extras,
        answers,
        f"llm:{model.model_id}",
    )


def read_programme(
    text: str,
    answers: GuidedAnswers | None,
    *,
    model: LanguageModel | None = None,
    gateway: ModelGateway | None = None,
    project: Project | None = None,
) -> ProgrammeReading:
    """Le programme du logement, ou None si le texte n'en décrit pas un. Modèle d'abord s'il
    est donné (accord vérifié par la passerelle), règles sinon ou en cas d'échec."""
    from openplan.adapters.remote.openai_compatible import ModelCallError

    if not describes_dwelling(text, answers):
        return ProgrammeReading(programme=None)
    if model is not None and gateway is not None and project is not None and text.strip():
        try:
            programme = read_with_model(
                text, answers, model=model, gateway=gateway, project=project
            )
            return ProgrammeReading(programme=programme, understanding="model")
        except ConsentRequiredError:
            notice = "le modèle distant attend votre accord ; programme lu par les phrases simples"
        except (
            ModelUnavailableError,
            ModelCallError,
            ValidationError,
            ValueError,
            OSError,
        ) as exc:
            notice = f"modèle indisponible ({str(exc)[:80]}) ; programme lu par les phrases simples"
        return ProgrammeReading(programme=read_rules(text, answers), notice=notice)
    return ProgrammeReading(programme=read_rules(text, answers))
