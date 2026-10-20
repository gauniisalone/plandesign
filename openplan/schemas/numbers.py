"""Nombres affichés (KAN-71) : virgule décimale partout où un texte est lu par une personne.

Une seule règle, la même que le front (`apps/web/src/format.ts`, mêmes noms, mêmes cas de
test) : arrondi au nombre de décimales demandé, virgule décimale, unité après une espace
(« 1,20 m », « 12,5 m² », « 50 cm », « 12,5 % »), « -0 » écrit « 0 ».

Les formats techniques n'y passent jamais : JSON, DXF, attributs numériques SVG, noms de
fichiers, journaux de machine et consignes envoyées à un modèle restent au point.
"""

from __future__ import annotations

import math

__all__ = ["area", "centimetres", "decimal", "length", "metres", "percent"]


def decimal(value: float, digits: int = 2, *, signed: bool = False) -> str:
    """« 1,20 » ; `signed` : « +0,30 » / « -0,30 » (le signe d'avant, `:+.2f`)."""
    if not math.isfinite(value):
        return "—"
    text = f"{abs(value):.{digits}f}"
    negative = value < 0 and any(c not in "0." for c in text)
    sign = "-" if negative else ("+" if signed else "")
    return sign + text.replace(".", ",")


def metres(value_m: float, digits: int = 2, *, signed: bool = False) -> str:
    """« 1,20 m »."""
    return f"{decimal(value_m, digits, signed=signed)} m"


def centimetres(value_m: float, digits: int = 0, *, signed: bool = False) -> str:
    """Une longueur en mètres, écrite en centimètres : « 50 cm », « 2,5 cm »."""
    return f"{decimal(value_m * 100.0, digits, signed=signed)} cm"


def area(value_m2: float, digits: int = 1) -> str:
    """« 12,5 m² »."""
    return f"{decimal(value_m2, digits)} m²"


def percent(fraction: float, digits: int = 0, *, signed: bool = False) -> str:
    """Une fraction (0,125) écrite en pourcentage : « 12,5 % » (espace avant %)."""
    return f"{decimal(fraction * 100.0, digits, signed=signed)} %"


def length(value_m: float, units: str = "m") -> str:
    """L'unité d'affichage du projet (`units_display`) : « 1,20 m », « 120 cm », « 1200 mm »."""
    if units == "cm":
        return centimetres(value_m)
    if units == "mm":
        return f"{decimal(value_m * 1000.0, 0)} mm"
    return metres(value_m)
