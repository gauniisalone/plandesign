# Site public PlanDesign (plandesign.fr)

Page publique statique, construite avec Astro (sortie HTML/CSS seule, aucun script client).

```sh
cd apps/site
npm ci
npm run build   # → dist/, puis scripts/check-dist.mjs (CSP stricte, aucun hôte tiers)
```

`dist/` est servi tel quel (Caddy `file_server`, page d'erreur `404.html`).

## Contrat /nouveau (site → app)

Le formulaire de l'accueil envoie le visiteur, en `GET`, vers
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
