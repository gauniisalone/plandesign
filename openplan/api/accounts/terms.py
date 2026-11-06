"""KAN-133 : les conditions d'utilisation (CGU), page du site plandesign.fr/cgu/.

`users.terms_version` (kan104_0012) garde la DERNIÈRE version acceptée, par l'une des trois
voies : l'inscription par mot de passe (signup/start, case `cgu`), la connexion par code
(email/start, case `cgu` : exigée pour créer un compte, et enregistrée à la vérification du
code), ou l'écran de nouvelle acceptation (POST /api/auth/terms/accept), proposé à la connexion
quand la version en base n'est pas TERMS_VERSION.

`users.accepted_terms_at` garde son sens de kan104_0012 : la date de l'inscription par mot de
passe. C'est aussi le repère des comptes inscrits, aux quotas plus serrés
(`AccountsRepo.self_signed_up`) ; les autres voies n'y touchent donc pas, pour ne pas changer
les quotas d'un compte créé ou confirmé par code.

Changer TERMS_VERSION, c'est demander à chaque compte une nouvelle acceptation : à réserver aux
changements importants (article 9 du texte), et la page du site change dans le même lot.
"""

from __future__ import annotations

TERMS_VERSION = "2026-11"
"""Version en vigueur des conditions (affichée en tête de la page)."""

TERMS_URL = "https://plandesign.fr/cgu/"
"""La page des conditions (site public, construit avec PUBLIC_LOGIN_WINDOW=1)."""
