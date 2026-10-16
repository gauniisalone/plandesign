# Site public PlanDesign (plandesign.fr)

Page publique statique, construite avec Astro (sortie HTML/CSS seule, aucun script client).

```sh
cd apps/site
npm ci
npm run build   # → dist/, puis scripts/check-dist.mjs (CSP stricte, aucun hôte tiers)
```

`dist/` est servi tel quel (Caddy `file_server`, page d'erreur `404.html`).

## État actuel (16 oct. 2026) : aucun lien vers l'app

Tant que les comptes ne sont pas ouverts, le site ne contient aucun lien vers
l'application ni vers le dépôt (`check-dist` échoue sinon). Les boutons « Se lancer »,
« Commencer » et « Ouvrir mon espace » sont des `<button type="button" aria-disabled="true"
data-action="login">` marqués « Ouverture prochaine » (`src/components/SoonButton.astro`).
Le formulaire de l'accueil n'est pas un `<form>` : il ne s'envoie pas.

## Fenêtre de connexion (à venir, non codée)

Les boutons `data-action="login"` ouvriront une fenêtre « Se connecter » sur le site :
saisie de l'e-mail, puis envoi du lien magique par l'API de l'app (note 17, lot B1b).
Cela demandera :

- un script sur le site (fichier externe, pas de script en ligne) ;
- `connect-src 'self' https://app.plandesign.fr` dans la CSP de plandesign.fr ;
- CORS côté app : origine `https://plandesign.fr` autorisée sur la route d'envoi du lien,
  sans cookie tiers ; réponse neutre (même message que l'adresse existe ou non).

## Contrat /nouveau (site → app) — SUSPENDU

Ce contrat n'est plus utilisé depuis le 16 oct. 2026 (formulaire débranché). Il est
gardé comme référence pour le jour où le formulaire préremplira le premier espace de
travail.

Le formulaire de l'accueil envoyait le visiteur, en `GET`, vers
`https://app.plandesign.fr/nouveau` avec les paramètres suivants
(encodage `application/x-www-form-urlencoded`, UTF-8 : espace = `+`,
retours à la ligne = `%0D%0A`) :

| Paramètre     | Valeurs                                                                 | Obligatoire |
|---------------|-------------------------------------------------------------------------|-------------|
| `v`           | `1` (version du contrat)                                                | toujours envoyé |
| `source`      | `site`                                                                  | toujours envoyé |
| `piece`       | vide, ou une valeur de `ROOM_TYPES` (`openplan/schemas/assistant.py`)    | non (vide possible) |
| `surface`     | vide, ou nombre décimal avec point, 1 à 1000, au dixième près (m²)           | non (vide possible) |
| `description` | texte libre, 1 à 1000 caractères                                        | oui (côté navigateur) |

Les champs vides sont envoyés vides (`piece=&surface=`) : l'app doit les traiter
comme absents. Les limites ne sont garanties que par le navigateur : l'app doit
revalider (type, bornes, longueur, valeur de `piece` dans la liste) et ignorer
tout paramètre inconnu. Le site ne stocke rien et ne pose aucun cookie.

Exemple :
`/nouveau?v=1&source=site&piece=attic&surface=35.5&description=Chambre+d%27amis+sous+les+combles`

Les limites sont définies dans `src/config.ts`.

## Licences

Astro (MIT). Polices Outfit et JetBrains Mono (SIL OFL 1.1), copiées dans
`public/fonts/` avec leur licence.
