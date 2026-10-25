"""Statistiques robustes (KAN-95) : médiane pondérée et rejet des aberrants par l'écart absolu
médian. Sans dépendance au reste du paquet."""

from __future__ import annotations

import numpy as np

MAD_K = 2.5
"""Rejet des aberrants : au-delà de 2,5 écarts absolus médians (normalisés) de la médiane."""


def weighted_median(values: list[float], weights: list[float]) -> float | None:
    if not values:
        return None
    v = np.asarray(values, dtype=np.float64)
    w = np.maximum(np.asarray(weights, dtype=np.float64), 0.0)
    if float(w.sum()) <= 0.0:
        w = np.ones_like(v)
    order = np.argsort(v)
    cumulative = np.cumsum(w[order])
    return float(v[order][int(np.searchsorted(cumulative, cumulative[-1] / 2.0))])


def robust_center(
    values: list[float], weights: list[float], k: float = MAD_K, min_band: float = 0.0
) -> tuple[float | None, list[int]]:
    """Médiane pondérée après rejet des valeurs à plus de k MAD (normalisés) de la médiane
    pondérée, sans jamais rejeter à moins de `min_band` (des valeurs presque égales ne sont
    pas des aberrantes) ; renvoie aussi les indices gardés."""
    center = weighted_median(values, weights)
    if center is None:
        return None, []
    deviations = np.abs(np.asarray(values, dtype=np.float64) - center)
    mad = float(np.median(deviations))
    band = max(k * 1.4826 * mad, min_band, 1e-6 + 1e-3 * abs(center))
    kept = [i for i, d in enumerate(deviations) if d <= band]
    if not kept:
        return center, list(range(len(values)))
    return weighted_median([values[i] for i in kept], [weights[i] for i in kept]), kept
