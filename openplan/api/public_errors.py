"""Ce qu'une erreur dit au client quand l'API est exposée (contrôle de sécurité D2) : jamais un
chemin du serveur (« /app/models/weights/… »). Un calcul indisponible (poids absents, pas de
GPU) devient une phrase fixe ; tout autre message garde son sens, chemins masqués. Le message
complet reste dans le journal du serveur. Sur la boucle locale, rien ne change : le message
avec la commande à lancer sert à l'utilisateur, qui est l'administrateur de sa machine."""

from __future__ import annotations

import logging
import re

from openplan.adapters.protocols import AdapterUnavailableError

UNAVAILABLE = "Calcul indisponible sur ce serveur : nécessite un poste de calcul GPU."
_PATH = re.compile(r"(?:[A-Za-z]:)?(?:[\\/][A-Za-z_.][\w.\-]*){2,}[\\/]?")
"""Un chemin de fichier : au moins deux segments commençant par une lettre (« /app/models »,
« C:\\data\\x ») ; une date « 14/10/2026 » n'en est pas un."""
PATH_MASK = "[chemin]"
log = logging.getLogger("openplan.api")


def public_detail(exc: BaseException, *, exposed: bool) -> str:
    """Le message montré au client pour `exc`."""
    text = str(exc) or type(exc).__name__
    if not exposed:
        return text
    masked = _PATH.sub(PATH_MASK, text)
    if isinstance(exc, AdapterUnavailableError):
        log.warning("calcul indisponible : %s", text)
        return UNAVAILABLE
    if masked != text:
        log.warning("message d'erreur masqué : %s", text)
    return masked
