"""Réglages lus dans l'environnement (voir .env.example). Tous ont un défaut sûr et local.

Un fichier `.env` à la racine (jamais commité) est lu s'il existe ; les variables déjà
présentes dans l'environnement ont priorité. Aucune dépendance : format KEY=VALUE, lignes
vides et commentaires `#` ignorés, guillemets simples ou doubles retirés.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from openplan.adapters.registry import AiConfig

DOTENV = Path(".env")
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
DEFAULT_PUBLIC_URL = "https://app.plandesign.fr"
DEFAULT_MAIL_FROM = "connexion@plandesign.fr"
DOTENV_VAR = "OPENPLAN_DOTENV"
"""Chemin du fichier `.env` à lire ; vide = n'en lire aucun (tests, serveurs jetables), pour
qu'une configuration personnelle (clé d'API distante) ne s'invite pas dans un environnement
qui n'est pas le sien."""


def dotenv_path(env: Mapping[str, str]) -> Path | None:
    if DOTENV_VAR not in env:
        return DOTENV
    value = env[DOTENV_VAR].strip()
    return Path(value) if value else None


def load_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("./data")
    host: str = "127.0.0.1"
    port: int = 8000
    blender_bin: str = "blender"
    device: str = "auto"
    """`auto` : cuda dès que torch voit un GPU, sinon cpu (`adapters.registry.resolve_device`)."""
    models_dir: Path = Path("./models/weights")
    materials_dir: Path = Path("./models/materials")
    """Matières de rendu téléchargées à la demande (D2), hors du dépôt."""
    ai: AiConfig = field(default_factory=AiConfig)
    api_token: str | None = None
    """Jeton unique (OPENPLAN_API_TOKEN), gardé pour compatibilité : acteur `user:invité`."""
    api_tokens: dict[str, str] = field(default_factory=dict)
    """Jetons nommés (OPENPLAN_API_TOKENS="augus:…, marie:…") exigés en en-tête
    `Authorization: Bearer` dès que `host` n'est pas la boucle locale ; le nom devient l'acteur
    `user:<nom>` de l'historique. Sur 127.0.0.1 l'API reste sans jeton. Jamais dans une URL ni
    dans les journaux."""
    web_dir: Path | None = None
    """Dossier du front construit (`apps/web/dist`) à servir sur `/` par la même API, même
    port : `openplan serve --web` ou OPENPLAN_WEB_DIR. None = API seule."""
    auto_path: bool = True
    """Chemin automatique après un dépôt pour les projets créés par ce serveur (V1, point 6) ;
    OPENPLAN_AUTO_PATH=0 le coupe par défaut (serveur jetable des tests de bout en bout)."""
    database_url: str | None = None
    """Base des comptes (note 17) : OPENPLAN_DATABASE_URL (`postgresql://…`). Absente = mode
    local inchangé, sans compte, sans base, sans session ; présente = comptes, sessions et
    droits par projet, projets sous `<data>/projects/`. Jamais affichée ni journalisée."""
    admin_email: str | None = None
    """OPENPLAN_ADMIN_EMAIL : la seule adresse qui devient administrateur, à sa première
    connexion ; elle peut toujours se connecter, même sur invitation (note 17, § 2)."""
    signup: str = "invite"
    """OPENPLAN_SIGNUP : `invite` (défaut : une adresse inconnue ne crée pas de compte) ou
    `open` ; « invitation » et « ouvert » sont acceptés."""
    mailer: str = "console"
    """OPENPLAN_MAILER : `console` (le lien de connexion va dans le journal du serveur, pour le
    développement), `fake` (boîte en mémoire, tests) ou `smtp_api` (Brevo, pas encore branché)."""
    public_url: str = DEFAULT_PUBLIC_URL
    """OPENPLAN_PUBLIC_URL : adresse publique de l'application ; base des liens de connexion et
    seule origine acceptée sur les mutations quand les comptes sont actifs."""
    mail_from: str = DEFAULT_MAIL_FROM
    """OPENPLAN_MAIL_FROM : expéditeur des liens de connexion."""

    @property
    def local_only(self) -> bool:
        return self.host in LOCAL_HOSTS

    @property
    def accounts_enabled(self) -> bool:
        return bool(self.database_url)

    @property
    def tokens(self) -> dict[str, str]:
        """Tous les jetons acceptés, par nom (le jeton unique s'appelle « invité »)."""
        out = dict(self.api_tokens)
        if self.api_token:
            out.setdefault(LEGACY_TOKEN_NAME, self.api_token)
        return out

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, dotenv: Path | None = DOTENV
    ) -> Settings:
        """`dotenv` : fichier lu avant l'environnement ; la variable OPENPLAN_DOTENV de
        l'environnement le remplace (vide = aucun fichier)."""
        source: Mapping[str, str] = os.environ if env is None else env
        if DOTENV_VAR in source:
            dotenv = dotenv_path(source)
        merged: dict[str, str] = {}
        if dotenv is not None:
            merged.update(load_env_file(dotenv))
        merged.update(source)
        return cls(
            data_dir=Path(merged.get("OPENPLAN_DATA_DIR", "./data")),
            host=merged.get("OPENPLAN_HOST", "127.0.0.1"),
            port=int(merged.get("OPENPLAN_PORT", "8000")),
            blender_bin=merged.get("OPENPLAN_BLENDER_BIN", "blender"),
            device=merged.get("OPENPLAN_DEVICE") or "auto",
            models_dir=Path(merged.get("OPENPLAN_MODELS_DIR", "./models/weights")),
            materials_dir=Path(merged.get("OPENPLAN_MATERIALS_DIR", "./models/materials")),
            ai=AiConfig.from_env(merged),
            api_token=merged.get("OPENPLAN_API_TOKEN") or None,
            api_tokens=parse_tokens(merged.get("OPENPLAN_API_TOKENS")),
            web_dir=Path(merged["OPENPLAN_WEB_DIR"]) if merged.get("OPENPLAN_WEB_DIR") else None,
            auto_path=merged.get("OPENPLAN_AUTO_PATH", "1").strip().lower()
            not in ("0", "false", "non", "no"),
            database_url=merged.get("OPENPLAN_DATABASE_URL", "").strip() or None,
            admin_email=merged.get("OPENPLAN_ADMIN_EMAIL", "").strip() or None,
            signup=parse_signup(merged.get("OPENPLAN_SIGNUP")),
            mailer=parse_mailer(merged.get("OPENPLAN_MAILER")),
            public_url=(merged.get("OPENPLAN_PUBLIC_URL") or DEFAULT_PUBLIC_URL).rstrip("/"),
            mail_from=merged.get("OPENPLAN_MAIL_FROM") or DEFAULT_MAIL_FROM,
        )

    @property
    def public_origin(self) -> str:
        """`https://hôte[:port]` de l'adresse publique (contrôle de l'en-tête Origin)."""
        scheme, _, rest = self.public_url.partition("://")
        return f"{scheme.lower()}://{rest.split('/', 1)[0].lower()}"


ACCOUNTS_PROJECTS_DIR = "projects"
SIGNUP_VALUES = {"invite": "invite", "invitation": "invite", "open": "open", "ouvert": "open"}
MAILERS = frozenset({"console", "fake", "smtp_api"})


def parse_signup(text: str | None) -> str:
    value = (text or "invite").strip().lower()
    if value not in SIGNUP_VALUES:
        msg = "OPENPLAN_SIGNUP : « invite » (ou « invitation ») ou « open » (ou « ouvert »)"
        raise ValueError(msg)
    return SIGNUP_VALUES[value]


def parse_mailer(text: str | None) -> str:
    value = (text or "console").strip().lower()
    if value not in MAILERS:
        msg = "OPENPLAN_MAILER : « console », « fake » ou « smtp_api »"
        raise ValueError(msg)
    return value


def project_root(settings: Settings) -> Path:
    """Dossier des projets : le dossier des données en mode local (inchangé), `<data>/projects`
    avec les comptes (clés de stockage `projects/<id>/…`, note 17 § 8)."""
    if settings.accounts_enabled:
        return settings.data_dir / ACCOUNTS_PROJECTS_DIR
    return settings.data_dir


LEGACY_TOKEN_NAME = "invité"
MIN_TOKEN_LENGTH = 16
_TOKEN_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")


def parse_tokens(text: str | None) -> dict[str, str]:
    """`"augus:abc…, marie:def…"` (séparateurs : virgule, espace, retour à la ligne) → {nom :
    jeton}. Un nom vide, un jeton court (< 16) ou une forme sans « : » est une erreur dite."""
    out: dict[str, str] = {}
    for chunk in re.split(r"[,\s]+", text or ""):
        if not chunk:
            continue
        name, sep, token = chunk.partition(":")
        name, token = name.strip(), token.strip()
        if not sep or not _TOKEN_NAME.match(name) or len(token) < MIN_TOKEN_LENGTH:
            msg = (
                f"OPENPLAN_API_TOKENS : entrée invalide « {chunk[:40]} » ; forme attendue "
                f"nom:jeton (nom de 1 à 32 lettres, chiffres, . _ -, jeton d'au moins "
                f"{MIN_TOKEN_LENGTH} caractères) ; `openplan token <nom>` en génère un"
            )
            raise ValueError(msg)
        out[name] = token
    return out
