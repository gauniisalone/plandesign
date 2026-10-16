# 17 · Comptes, sessions, droits et stockage (B1)

Décisions d'augus du 16 octobre 2026 (02:04), relayées par le PO ; elles **remplacent le § 5
de la note 14** (comptes). Cette note décrit comment les appliquer. **Validée par augus le
16 octobre 2026** (route de retour et adresse administrateur comprises ; le 16 octobre vers 14:00,
augus a écarté le tableau d'exemple, § 13). Le mode local (sans compte, sur le 8000 de la machine d'augus) ne
change pas.

**Avenant du 16 octobre (~15:10) : e-mail seulement.** Décision d'augus relayée par le
PO : la connexion se fait par un lien envoyé par e-mail, sans Google pour l'instant. Le § 2
est réécrit, le lot B1b et le § 13 sont mis à jour ; session, contrôles côté serveur,
Postgres, `Storage`, rôles et invitations ne changent pas. La note reste validée. Le nom
du fichier garde « google » pour ne pas casser les liens.

## 1. Les décisions

1. **Pas de projet sans compte.** Un visiteur anonyme ne voit que la page de connexion :
   « Recevoir un lien de connexion » (e-mail), une courte présentation et la mention de
   confidentialité.
   Ni vitrine ni tableau public (décision d'augus du 16 octobre, ~14:00).
2. **Connexion par e-mail seulement** (avenant du 16 octobre) : un lien magique, sans mot
   de passe, envoyé par SMTP. Pas de Keycloak, pas de Google pour l'instant (rattachement
   possible plus tard). Les invitations restent des liens à copier ; elles peuvent aussi
   partir par e-mail une fois le SMTP en place.
3. **Postgres pour les métadonnées** (`users`, `sessions`, `members`, `invitations`,
   `board_items`, `comments`, `activity`, `jobs`). **Les fichiers restent sur le disque**,
   servis par l'API après contrôle des droits, derrière une seule couche de stockage
   (`Storage`) pour pouvoir passer à un S3 plus tard. Pas de MinIO.

## 2. La connexion par e-mail (avenant du 16 octobre)

- **Lien de connexion envoyé par e-mail, sans mot de passe.** Page de connexion : un seul
  champ « Votre adresse e-mail » et « Recevoir un lien de connexion ».
- `POST /api/auth/email/start` (`email`, `next`) : normalise l'adresse (minuscules, espaces
  retirés), crée un jeton aléatoire de **32 octets**, **à usage unique**, **valable 15 min**,
  dont la base ne garde que l'empreinte (SHA-256, table `login_tokens`), et envoie le lien
  `https://app.plandesign.fr/api/auth/email/callback?token=…`. **La réponse est identique que
  l'adresse ait un compte ou non** (« Si cette adresse peut se connecter, un lien vient de
  partir ») : la page ne révèle jamais qui a un compte. `next` n'accepte qu'un chemin relatif
  de l'application, gardé avec le jeton (pas de redirection ouverte).
- `GET /api/auth/email/callback?token=…` : cherche l'empreinte (temps constant), refuse un
  jeton expiré, déjà utilisé ou révoqué, le marque utilisé **avant** d'ouvrir la session, puis :
  - cherche l'utilisateur par son **adresse normalisée** (clé unique) ; cliquer le lien prouve
    que la personne lit cette boîte ;
  - absent : le crée **seulement si l'inscription est ouverte** ou si une invitation en
    attente vise cette adresse (B1e) ; sinon « Connexion impossible : PlanDesign est pour
    l'instant sur invitation », rien de créé. Réglage `OPENPLAN_SIGNUP` = `invite` (défaut,
    tant que B1 n'est pas éprouvé) ou `open` ;
  - **premier administrateur** : l'adresse de `OPENPLAN_ADMIN_EMAIL` (le `.env` du VPS) peut
    toujours se connecter et devient `is_admin` à sa première connexion, et seulement elle ;
    aucun autre moyen de devenir administrateur que par un administrateur ;
  - ouvre une session (§ 3) et redirige vers `next`.
- Un lien ouvert par un aperçu de messagerie (robot qui suit les liens) ne doit pas consommer
  le jeton : le `GET` affiche « Se connecter à PlanDesign » avec un bouton, et c'est le `POST`
  de ce bouton qui consomme le jeton et ouvre la session.
- **Limitation de débit** sur l'envoi : 3 liens par adresse et par 15 min, 10 demandes par IP
  et par heure, en plus de la limite générale de `/api/auth/*` (§ 3). Au-delà, la même réponse
  neutre, sans envoi.
- **Envoi** : SMTP d'un service gratuit (Brevo pressenti, 300 e-mails par jour), expéditeur
  `connexion@plandesign.fr` ; hôte, port, identifiant et clé dans le `.env` du VPS
  (`OPENPLAN_SMTP_HOST`, `OPENPLAN_SMTP_PORT`, `OPENPLAN_SMTP_USER`, `OPENPLAN_SMTP_PASSWORD`,
  `OPENPLAN_MAIL_FROM`), jamais dans le dépôt. Le message ne contient que le lien, sa durée
  et « si vous n'avez rien demandé, ignorez ce message » ; il est en texte et en HTML simple.
  SPF, DKIM et DMARC de plandesign.fr : côté infra.
- **Tests et e2e** : une **fausse boîte aux lettres locale** (classe `Mailer` remplacée, les
  messages gardés en mémoire et lisibles par le test) ; aucun envoi réel hors du VPS.
- Échecs (lien expiré, déjà utilisé, inscription fermée) : page « Connexion impossible » avec
  la raison en clair et « Recevoir un nouveau lien ».
- Déconnexion : `POST /api/auth/logout` révoque la session et efface le cookie.
- **Plus tard** : rattacher un compte Google à un compte existant (même adresse vérifiée),
  sans changer les sessions ni les droits.

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

- Garde de route : sans session, la page de connexion ; une 401 renvoie vers la page de connexion
  (« Recevoir un lien de connexion ») puis revient à la page demandée (`next`).
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
e2e « comptes » dédiée tourne contre un Postgres jetable et une fausse boîte aux lettres locale.

## 8. Données : Postgres et migrations

- Postgres 16 en conteneur sur le VPS (volume persistant, sauvegardé par `pg_dump` dans
  l'archive nocturne de B0), accès réseau Docker interne seulement.
- Tables (B1 puis B2) :
  - `users` (id, email unique normalisé, name, is_admin, created_at, last_seen_at) ;
  - `login_tokens` (id, email, token_hash unique, next, created_at, expires_at, used_at,
    ip court) : purgés après 24 h ;
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
  connexion se fait par un lien envoyé à votre adresse e-mail ; PlanDesign ne garde que cette
  adresse (et le nom que vous donnez), et l'e-mail part par notre service d'envoi (Brevo). Vos
  projets sont stockés sur plandesign.fr (Hostinger, France) et visibles de leurs membres. »
- Le mode local garde sa mention actuelle (« sur cette machine »).
- Page « Mes données » : exporter mes projets (archive), supprimer mon compte (révoque les
  sessions, retire les appartenances, supprime les projets dont je suis le seul
  propriétaire après confirmation).

## 12. Lots et estimations

| Lot | Contenu | Estimation |
|---|---|---|
| B1a | Postgres + Alembic + `Storage` disque ; tables users/sessions/projects/members ; mode local inchangé (activation par `OPENPLAN_DATABASE_URL`) | 1 journée |
| B1b | Connexion par lien e-mail (jeton 32 octets, usage unique, 15 min, empreinte ; réponse neutre ; page de confirmation contre les aperçus de messagerie ; inscription `invite`/`open` ; premier administrateur), envoi SMTP, sessions 30 j glissantes et révocables, contrôle d'origine, limitation de débit par adresse et par IP, fausse boîte aux lettres pour les tests | 1 ½ journée |
| B1c | Dépendances `current_user` / `project_member(role)` sur toutes les routes, test qui parcourt les routes, tests par rôle, fichiers via `Storage` | 1 journée |
| B1d | Front : page de connexion (présentation courte, mention de confidentialité), connexion, garde de route, « mes projets » (la galerie de T5), boutons selon le rôle, mention de confidentialité, « Mes données » | 1 journée |
| B1e | Invitations par lien (rôle, expiration, révocation), menu Partager, import des projets d'augus | 1 journée |

Total B1 : 5 ½ journées. Dépendances : T5 (galerie) avant B1d ; infra (D2/D3) pour le
conteneur Postgres, le `.env` SMTP, les enregistrements SPF/DKIM/DMARC et la sauvegarde
`pg_dump`. Coût : 0 € (palier gratuit de Brevo, 300 e-mails par jour ; Postgres libre).

## 13. Ce qu'augus doit faire ou décider

1. Créer le compte du service d'envoi (Brevo pressenti, gratuit) au nom de PlanDesign, ou
   nous laisser le guider pas à pas ; la clé SMTP va dans le `.env` du VPS (par l'infra,
   jamais dans un message). L'infra ajoute SPF, DKIM et DMARC au domaine.
2. Choisir l'inscription : sur invitation seulement (défaut proposé tant que B1 n'est pas
   éprouvé) ou ouverte. ~~Client OAuth Google et URI de redirection~~ : sans objet depuis
   l'avenant du 16 octobre.
3. ~~Confirmer : l'adresse administrateur = celle déjà dans le `.env` du VPS.~~ Confirmée le
   16 octobre.
4. ~~Choisir le tableau d'exemple montré aux anonymes.~~ Tranché le 16 octobre (~14:00) :
   aucun tableau d'exemple ni vitrine pour l'instant ; sans session, seulement la page de
   connexion (B1d).
5. ~~Valider cette note.~~ Validée le 16 octobre.
