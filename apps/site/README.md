# Site public PlanDesign (plandesign.fr)

Page publique statique, construite avec Astro (sortie HTML/CSS seule, aucun script client
sans l'option de la fenêtre de connexion, voir plus bas).

```sh
cd apps/site
npm ci
npm run build   # → dist/, puis scripts/check-dist.mjs (CSP stricte, aucun hôte tiers)
```

`dist/` est servi tel quel (Caddy `file_server`, page d'erreur `404.html`).

## Sortie par défaut (production, 16 oct. 2026) : aucun lien vers l'app

Tant que les comptes ne sont pas ouverts, le site ne contient aucun lien vers
l'application ni vers le dépôt (`check-dist` échoue sinon). Les boutons « Se lancer »,
« Commencer » et « Ouvrir mon espace » sont des `<button type="button" aria-disabled="true"
data-action="login">` marqués « Ouverture prochaine » (`src/components/SoonButton.astro`).
Le formulaire de l'accueil n'est pas un `<form>` : il ne s'envoie pas.

## Fenêtre « Se connecter » (B1d-b) — derrière une option de build

Désactivée par défaut, donc en production tant qu'augus n'a pas décrété le MVP : sans
l'option, `dist/` est identique octet pour octet à la sortie d'avant (405a364), et
`check-dist` refuse tout script, toute mention de l'app et les fichiers de la fenêtre.

```sh
PUBLIC_LOGIN_WINDOW=1 npm run build                       # app : https://app.plandesign.fr
PUBLIC_LOGIN_WINDOW=1 PUBLIC_APP_ORIGIN=http://localhost:8010 npm run build   # essai local
```

Avec l'option :

- les boutons `data-action="login"` (« Se lancer », « Commencer », « Ouvrir mon espace »)
  deviennent actifs et ouvrent une fenêtre `<dialog>` en deux temps : l'adresse
  (`POST /api/auth/email/start`), puis le code à 6 chiffres reçu par e-mail
  (`POST /api/auth/email/verify`, avec les cookies) ;
- le comportement est dans `login/login.js` et le style dans `login/login.css`, copiés dans
  `dist/` par `scripts/login-assets.mjs` (fichiers externes : aucun script en ligne) ;
- code juste : la session `pd_session` est posée par l'app (même site que plandesign.fr :
  le cookie `SameSite=Lax` passe), puis le navigateur part vers l'app, sur `/nouveau` (contrat
  ci-dessous) si la description est remplie, sinon sur son accueil ;
- les limites du formulaire sont celles de l'app : description 300 caractères, surface 8 à
  400 m².

À poser par l'infra le jour de l'ouverture :

- CSP de plandesign.fr : `script-src 'self'` (déjà) et
  `connect-src 'self' https://app.plandesign.fr` ;
- sur l'app : `OPENPLAN_SITE_ORIGIN=https://plandesign.fr` (sans elle, l'API refuse toute
  origine extérieure ; avec, seulement sur la demande et la vérification du code, et sur le
  formulaire de contact) ;
- sur l'app : `OPENPLAN_CONTACT_TO=<adresse>`, destinataire du formulaire de contact
  (`/contact/`, avec l'option seulement ; par défaut `OPENPLAN_ADMIN_EMAIL`).

Le sitemap n'est pas à retoucher : avec l'option, `scripts/login-assets.mjs` ajoute
`https://plandesign.fr/contact/` à `dist/sitemap.xml` ; sans elle, `public/sitemap.xml` est
copié tel quel (sortie identique à la production).

## Contrat /nouveau v1 (site → app) — ACTIF avec la fenêtre

Après la connexion, la fenêtre envoie le visiteur, en `GET`, vers
`https://app.plandesign.fr/nouveau` avec les paramètres suivants
(encodage `application/x-www-form-urlencoded`, UTF-8 : espace = `+`) :

| Paramètre     | Valeurs                                                                 | Obligatoire |
|---------------|-------------------------------------------------------------------------|-------------|
| `v`           | `1` (version du contrat)                                                | toujours envoyé |
| `source`      | `site`                                                                  | toujours envoyé |
| `piece`       | vide, ou une valeur de `ROOM_TYPES` (`openplan/schemas/assistant.py`)    | non (vide possible) |
| `surface`     | vide, ou nombre décimal avec point, 8 à 400, au dixième près (m²)       | non (vide possible) |
| `description` | texte libre, 1 à 300 caractères                                         | oui (sinon : accueil de l'app) |

Les champs vides sont envoyés vides (`piece=&surface=`) : l'app les traite comme absents.
L'app revalide tout (pièce de la liste, surface de 8 à 400 m², description coupée à 300
caractères au dernier mot), ignore tout paramètre inconnu, crée un espace de travail au nom
automatique, préremplit ses exigences et l'ouvre compositeur ouvert ; **le plan se génère
seul** depuis ces exigences (moteur à règles, sans GPU ; retour d'augus du 19 oct. 2026). Pas
de rendus en ligne ; sur une machine avec Blender, le chemin automatique habituel s'applique.
Surface acceptée avec un point ou une virgule, arrondie au dixième.
Le site ne stocke rien et ne pose aucun cookie.

Exemple :
`/nouveau?v=1&source=site&piece=attic&surface=35.5&description=Chambre+d%27amis+sous+les+combles`

Les limites sont définies dans `src/config.ts`.

## Licences

Astro (MIT). Polices Outfit et JetBrains Mono (SIL OFL 1.1), copiées dans
`public/fonts/` avec leur licence.
