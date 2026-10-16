# 17 · Comptes Google, sessions, droits et stockage (B1)

Décisions d'augus du 16 octobre 2026 (02:04), relayées par le PO ; elles **remplacent le § 5
de la note 14** (comptes). Cette note décrit comment les appliquer. **Validée par augus le
16 octobre 2026** (route de retour et adresse administrateur comprises ; le 16 octobre vers 14:00,
augus a écarté le tableau d'exemple, § 13). Le mode local (sans compte, sur le 8000 de la machine d'augus) ne
change pas.

## 1. Les décisions

1. **Pas de projet sans compte.** Un visiteur anonyme ne voit que la page de connexion :
   « Se connecter avec Google », une courte présentation et la mention de confidentialité.
   Ni vitrine ni tableau public (décision d'augus du 16 octobre, ~14:00).
2. **Google est le seul moyen de connexion**, en OpenID Connect (portées `openid email
   profile`), dans l'application (Authlib). Pas de Keycloak, pas de mot de passe, pas de lien
   magique. Les invitations restent des liens à copier (note 14, pas de SMTP).
3. **Postgres pour les métadonnées** (`users`, `sessions`, `members`, `invitations`,
   `board_items`, `comments`, `activity`, `jobs`). **Les fichiers restent sur le disque**,
   servis par l'API après contrôle des droits, derrière une seule couche de stockage
   (`Storage`) pour pouvoir passer à un S3 plus tard. Pas de MinIO.

## 2. Le flux Google

- Côté Google (Google Cloud Console, à faire par augus, au nom de PlanDesign) : un client
  OAuth « application Web », origine `https://app.plandesign.fr`, **URI de redirection
  `https://app.plandesign.fr/api/auth/google/callback`**, écran de consentement en mode
  « Production » avec les portées `openid email profile` (non sensibles : pas de validation
  Google). L'identifiant et le secret du client vont dans le `.env` du VPS
  (`OPENPLAN_GOOGLE_CLIENT_ID`, `OPENPLAN_GOOGLE_CLIENT_SECRET`), jamais dans le dépôt.
- `GET /api/auth/google/login?next=/…` : génère `state` (32 octets aléatoires), `nonce` et un
  vérificateur **PKCE** (S256) ; les garde dans un cookie de transit chiffré (HttpOnly,
  Secure, SameSite=Lax, 10 min, Path=/api/auth) ; redirige vers Google. `next` n'accepte
  qu'un chemin relatif de l'application (pas d'URL : pas de redirection ouverte).
- `GET /api/auth/google/callback?code&state` : vérifie `state` (temps constant) contre le
  cookie de transit, échange le code avec le vérificateur PKCE, valide le jeton d'identité
  (signature par les clés publiques de Google, `iss`, `aud` = notre client, `exp`, `nonce`),
  exige **`email_verified = true`**, puis :
  - cherche l'utilisateur par **`sub`** (identifiant Google stable) ; l'adresse e-mail n'est
    qu'une donnée affichée et mise à jour, jamais la clé ;
  - absent : le crée (`sub`, e-mail, nom, avatar si fourni) ;
  - **premier compte administrateur** : l'adresse de `OPENPLAN_ADMIN_EMAIL` (le `.env` du
    VPS, déjà présent pour Let's Encrypt) devient `is_admin` à sa première connexion, et
    seulement elle ; aucun autre moyen de devenir administrateur que par un administrateur ;
  - ouvre une session (§ 3), efface le cookie de transit, redirige vers `next`.
- Échecs (state invalide, e-mail non vérifié, refus de l'utilisateur) : page « Connexion
  impossible » avec la raison en clair, rien de créé.
- Déconnexion : `POST /api/auth/logout` révoque la session et efface le cookie.

## 3. La session

- Cookie `pd_session` : **HttpOnly, Secure, SameSite=Lax, Path=/, 30 jours**, **renouvelé à
  l'usage** (glissant : prolongé au plus une fois par heure), **révocable** (ligne
  `sessions` avec `revoked_at` ; « me déconnecter partout » révoque toutes celles de
  l'utilisateur ; un administrateur peut révoquer celles d'un autre).
- Valeur : un identifiant aléatoire de 32 octets ; la base n'en garde que l'empreinte
  (SHA-256). Une fuite de la base ne donne pas de session utilisable.
- Elle **remplace le jeton unique et le cookie de lecture** (note 16) sur app.plandesign.fr :
  le Bearer reste réservé aux postes de calcul (§ 6) et au mode local exposé hérité.
- **Contrôle de l'origine sur les mutations** : POST/PUT/PATCH/DELETE exigent un en-tête
  `Origin` (ou à défaut `Referer`) égal à `https://app.plandesign.fr`, sinon 403. Avec
  SameSite=Lax, c'est la défense CSRF ; pas de jeton CSRF à gérer côté front.
- **Limitation de débit** sur `/api/auth/*` : 10 tentatives par minute et par adresse IP
  (dans l'application, et fail2ban sur les 401/403 répétés côté infra).

## 4. Les contrôles côté serveur (la seule vraie barrière)

- **Tout refusé par défaut.** Deux dépendances FastAPI :
  - `current_user` : la session valide, sinon 401 ;
  - `project_member(role)` : l'utilisateur est membre du projet de la route avec au moins
    ce rôle (`viewer` < `commenter` < `editor` < `owner`), sinon 404 (on ne révèle pas
    l'existence d'un projet) ou 403 si le projet est déjà visible.
- Chaque route `/projects/{project_id}/…` déclare son rôle minimal : lecture → `viewer`,
  commentaire → `commenter`, modification du plan, du tableau, rendus → `editor`, partage,
  suppression, invitations → `owner`.
- **Un test parcourt toutes les routes** (comme celui de `COOKIE_DENIED`, note 16) et échoue
  si une route sous `/api` n'a ni `current_user` ni une exemption listée (`/api/health`,
  `/api/auth/*`) ; et si une route `/projects/{project_id}`
  n'a pas `project_member`. Un second test, par rôle, appelle un échantillon de routes avec
  chaque rôle et vérifie les refus.
- Les fichiers (photos, rendus, pièces jointes, exports) passent par `Storage` et par la même
  dépendance : jamais de chemin statique, jamais d'URL publique.
- Le journal d'accès garde l'utilisateur (`user:<id>`), jamais l'e-mail.

## 5. Côté navigateur : du confort, pas de la sécurité

- Garde de route : sans session, la page de connexion ; une 401 renvoie vers « Se connecter avec
  Google » puis revient à la page demandée (`next`).
- Les boutons qu'un rôle ne peut pas utiliser sont masqués ou désactivés (un `viewer` ne voit
  ni outils ni « Générer »).
- **Tout cela n'est que du confort** : chaque action est de toute façon refusée par le
  serveur si le rôle ne suffit pas (§ 4). Les tests de sécurité portent sur l'API, pas sur
  l'écran.

## 6. Les postes de calcul

Inchangé (note 14, § 3.2 et § 7) : un **jeton d'agent scopé** (`agent:<nom>`), créé par un
administrateur, révocable, qui ne peut que prendre des tâches de calcul, lire les entrées
de ces tâches et déposer leurs résultats. Pas de session, pas d'accès aux comptes.

## 7. Le mode local

`openplan serve` sur la boucle locale (le 8000 de la machine d'augus) reste **sans compte,
sans base, sans session**, avec les projets en fichiers comme aujourd'hui. Le code des
comptes ne s'active que si `OPENPLAN_DATABASE_URL` est défini (le conteneur de
app.plandesign.fr). Les e2e actuels tournent en mode local et ne changent pas ; une suite
e2e « comptes » dédiée tourne contre un Postgres jetable et un faux fournisseur OIDC.

## 8. Données : Postgres et migrations

- Postgres 16 en conteneur sur le VPS (volume persistant, sauvegardé par `pg_dump` dans
  l'archive nocturne de B0), accès réseau Docker interne seulement.
- Tables (B1 puis B2) :
  - `users` (id, google_sub unique, email, name, avatar_url, is_admin, created_at,
    last_seen_at) ;
  - `sessions` (id, user_id, token_hash unique, created_at, last_used_at, expires_at,
    revoked_at, user_agent court) ;
  - `projects` (id = l'id de `project.json`, owner_id, name, created_at, storage_key) ;
  - `members` (project_id, user_id, role, added_by, added_at ; clé (project_id, user_id)) ;
  - `invitations` (id, project_id, role, token_hash, created_by, expires_at, used_by,
    used_at, revoked_at) ;
  - `board_items` (B2 : un objet du tableau par ligne, `version` et `updated_by` pour la
    dernière écriture horodatée) ; `comments` (B4) ; `activity` (B4) ; `jobs` (B3 : file
    des postes de calcul).
- Le projet lui-même (`project.json`, scène, historique) reste un fichier dans le stockage
  (`Storage` : `projects/<id>/…`) ; la base n'en garde que l'index et les droits.
- **Alembic** pour les migrations (une migration par lot, jamais modifiée une fois
  publiée ; `alembic upgrade head` au démarrage du conteneur, après `pg_dump`). SQLAlchemy
  2 (Core + ORM léger), pilote psycopg 3. Tests contre un Postgres jetable (conteneur ou
  `pytest-postgresql`), jamais contre la base du VPS.

## 9. Passage des projets actuels au compte d'augus

- Commande `openplan accounts import --owner <email> <dossier>` (sur le VPS, une fois) :
  copie les projets choisis de la machine d'augus vers le stockage, crée leurs lignes
  `projects` et `members` (augus `owner`), après sa première connexion (son compte doit
  exister). Rien n'est envoyé sans qu'augus choisisse les projets (liste affichée, puis
  confirmation). Les poids et les `runs/` lourds (scènes Blender) restent sur la machine ;
  seuls les rendus et vignettes sont copiés.
- Les projets restent en même temps sur la machine (mode local) : pas de synchronisation
  automatique dans B1 ; B2 décidera du sens de vérité si augus veut travailler aux deux
  endroits.

## 10. Place sur le disque (sous réserve de l'accord d'augus)

Mesures locales (PO, 16 octobre) : projet médian 15 Mo, 90 % des projets sous 95 Mo, et
`runs/` fait 97 % du poids. Sur le serveur :

- **Purger les scènes intermédiaires de `runs/`** après chaque calcul (fichiers Blender,
  cartes de profondeur, sorties de travail) : ne garder que les rendus finaux, leurs
  vignettes et leurs métadonnées.
- **Réduire les photos à 2 500 px de large à l'import** ; l'original n'est gardé qu'en mode
  local (sur la machine de l'utilisateur).
- **Quota par compte**, à fixer plus tard (affiché dans « Mes données », refus clair au
  dépassement).

## 11. Confidentialité

- Mention sur la page de connexion et dans le pied de page de app.plandesign.fr : « La
  connexion passe par Google : PlanDesign reçoit votre nom, votre adresse e-mail et votre
  photo de profil, rien d'autre ; Google sait que vous vous connectez à PlanDesign. Vos
  projets sont stockés sur plandesign.fr (Hostinger, France) et visibles de leurs membres. »
- Le mode local garde sa mention actuelle (« sur cette machine »).
- Page « Mes données » : exporter mes projets (archive), supprimer mon compte (révoque les
  sessions, retire les appartenances, supprime les projets dont je suis le seul
  propriétaire après confirmation).

## 12. Lots et estimations

| Lot | Contenu | Estimation |
|---|---|---|
| B1a | Postgres + Alembic + `Storage` disque ; tables users/sessions/projects/members ; mode local inchangé (activation par `OPENPLAN_DATABASE_URL`) | 1 journée |
| B1b | Flux Google OIDC (Authlib, state/nonce/PKCE, e-mail vérifié, `sub`, premier administrateur), sessions 30 j glissantes et révocables, contrôle d'origine, limitation de débit, faux fournisseur OIDC pour les tests | 1 ½ journée |
| B1c | Dépendances `current_user` / `project_member(role)` sur toutes les routes, test qui parcourt les routes, tests par rôle, fichiers via `Storage` | 1 journée |
| B1d | Front : page de connexion (présentation courte, mention de confidentialité), connexion, garde de route, « mes projets » (la galerie de T5), boutons selon le rôle, mention de confidentialité, « Mes données » | 1 journée |
| B1e | Invitations par lien (rôle, expiration, révocation), menu Partager, import des projets d'augus | 1 journée |

Total B1 : 5 ½ journées. Dépendances : T5 (galerie) avant B1d ; infra (D2/D3) pour le
conteneur Postgres, le `.env` Google et la sauvegarde `pg_dump`. Coût : 0 € (Google OIDC
gratuit, Postgres et Authlib libres).

## 13. Ce qu'augus doit faire ou décider

1. Créer le client OAuth Google (ou nous laisser le guider pas à pas) et mettre l'identifiant
   et le secret dans le `.env` du VPS (par l'infra, jamais dans un message).
2. ~~Valider l'URI de redirection `https://app.plandesign.fr/api/auth/google/callback`.~~
   Validée le 16 octobre.
3. ~~Confirmer : l'adresse administrateur = celle déjà dans le `.env` du VPS.~~ Confirmée le
   16 octobre.
4. ~~Choisir le tableau d'exemple montré aux anonymes.~~ Tranché le 16 octobre (~14:00) :
   aucun tableau d'exemple ni vitrine pour l'instant ; sans session, seulement la page de
   connexion (B1d).
5. ~~Valider cette note.~~ Validée le 16 octobre.
