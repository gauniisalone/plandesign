"""Contrôle qualité d'une image : netteté, exposition, résolution.

Métriques sans réseau de neurones, calculées en NumPy sur la luminance. Les seuils sont
empiriques et documentés ; ils déclenchent des avertissements, jamais un refus.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from openplan.schemas.numbers import decimal

ANALYSIS_LONG_SIDE = 1024
"""La netteté est mesurée sur une version réduite : les métriques dépendent de la
résolution, on les normalise en ramenant le grand côté à cette taille."""
BLUR_RATIO_WARN = 0.30
"""Rapport de re-floutage (`reblur_ratio`) au-dessus duquel on signale un flou. Mesuré le
24 sept. 2026 sur deux photos de smartphone et le rendu de la fixture (protocole §1, qualité) :
images nettes 0,07 à 0,13 ; flou gaussien de 1,5 px (à 1024 px) 0,32 à 0,52 ; flou de bougé
de 8 px 0,32 à 0,46. La variance du laplacien, elle, dépend de la texture (80 sur le rendu
net, 2 200 sur un mur de pierre) et ne voit pas un bougé sur une photo texturée : elle est
gardée à titre indicatif seulement."""
_GAUSS_1PX = (0.0545, 0.2442, 0.4026, 0.2442, 0.0545)
"""Noyau gaussien 1D, sigma 1 px, rayon 2 (normalisé)."""
DARK_MEAN_WARN = 0.18
BRIGHT_MEAN_WARN = 0.85
CLIPPED_FRACTION_WARN = 0.25
MIN_SIDE_WARN = 800


def luminance(pixels: NDArray[np.uint8]) -> NDArray[np.float32]:
    """Luminance Rec. 601 en float32, 0–255."""
    rgb = pixels.astype(np.float32)
    gray: NDArray[np.float32] = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    return gray


def downscale(gray: NDArray[np.float32], long_side: int) -> NDArray[np.float32]:
    """Réduction par moyenne de blocs entiers (pas d'interpolation, pas de dépendance)."""
    h, w = gray.shape
    factor = max(1, int(np.ceil(max(h, w) / long_side)))
    if factor == 1:
        return gray
    hh, ww = (h // factor) * factor, (w // factor) * factor
    if hh == 0 or ww == 0:
        return gray
    blocks = gray[:hh, :ww].reshape(hh // factor, factor, ww // factor, factor)
    return blocks.mean(axis=(1, 3), dtype=np.float32)


def laplacian_variance(gray: NDArray[np.float32]) -> float:
    """Variance du laplacien 4-voisins, mesure classique de netteté."""
    if gray.shape[0] < 3 or gray.shape[1] < 3:
        return 0.0
    center = gray[1:-1, 1:-1]
    lap = 4.0 * center - gray[:-2, 1:-1] - gray[2:, 1:-1] - gray[1:-1, :-2] - gray[1:-1, 2:]
    return float(lap.var())


def _blur_1d(gray: NDArray[np.float32], axis: int) -> NDArray[np.float32]:
    """Flou gaussien 1D (sigma 1 px) le long d'un axe, bords répliqués."""
    radius = len(_GAUSS_1PX) // 2
    padded = np.pad(gray, [(radius, radius) if a == axis else (0, 0) for a in (0, 1)], "edge")
    n = gray.shape[axis]
    out = np.zeros_like(gray)
    for i, weight in enumerate(_GAUSS_1PX):
        out += weight * np.take(padded, range(i, i + n), axis=axis)
    return out


def _second_derivative_var(gray: NDArray[np.float32], axis: int) -> float:
    return float(np.diff(gray, n=2, axis=axis).var())


def reblur_ratio(gray: NDArray[np.float32]) -> float:
    """Métrique de flou indépendante de la texture (principe de Crete et al., 2007) : on
    re-floute l'image d'un pixel et on mesure ce qu'il reste de la dérivée seconde. Une image
    nette perd l'essentiel (rapport proche de 0) ; une image déjà floue ne change presque pas
    (rapport proche de 1). Calculée par direction, on garde la pire : un bougé horizontal
    laisse les bords verticaux nets et ne se voit que dans une direction."""
    if gray.shape[0] < 5 or gray.shape[1] < 5:
        return 0.0
    worst = 0.0
    for axis in (0, 1):
        base = _second_derivative_var(gray, axis)
        if base <= 0.0:
            continue
        again = _second_derivative_var(_blur_1d(gray, axis), axis)
        worst = max(worst, again / base)
    return worst


def assess_quality(pixels: NDArray[np.uint8]) -> dict[str, float]:
    gray = luminance(pixels)
    reduced = downscale(gray, ANALYSIS_LONG_SIDE)
    h, w = gray.shape
    return {
        "blur_reblur_ratio": reblur_ratio(reduced),
        "blur_laplacian_var": laplacian_variance(reduced),
        "mean_luminance": float(gray.mean() / 255.0),
        "dark_fraction": float((gray < 10.0).mean()),
        "bright_fraction": float((gray > 245.0).mean()),
        "min_side_px": float(min(h, w)),
    }


def quality_warnings(quality: dict[str, float]) -> list[str]:
    """Messages destinés à l'utilisateur ; liste vide si rien à signaler."""
    out: list[str] = []
    blur = quality.get("blur_reblur_ratio")
    if blur is not None and blur > BLUR_RATIO_WARN:
        out.append(
            f"Image probablement floue (flou {decimal(blur)} > {decimal(BLUR_RATIO_WARN)}, "
            "net vers 0,1) : les lignes détectées seront peu fiables."
        )
    mean = quality.get("mean_luminance")
    if mean is not None and mean < DARK_MEAN_WARN:
        out.append(f"Image sombre (luminance moyenne {mean:.0%}).")
    if mean is not None and mean > BRIGHT_MEAN_WARN:
        out.append(f"Image très claire (luminance moyenne {mean:.0%}).")
    dark = quality.get("dark_fraction", 0.0)
    bright = quality.get("bright_fraction", 0.0)
    if dark > CLIPPED_FRACTION_WARN:
        out.append(f"{dark:.0%} de pixels bouchés (noir pur).")
    if bright > CLIPPED_FRACTION_WARN:
        out.append(f"{bright:.0%} de pixels brûlés (blanc pur).")
    side = quality.get("min_side_px")
    if side is not None and side < MIN_SIDE_WARN:
        out.append(
            f"Résolution faible ({side:.0f} px sur le petit côté, {MIN_SIDE_WARN} conseillés)."
        )
    return out
