# Contribuer

## Règles

- Le plus petit incrément fonctionnel, testé, mergeable.
- Pas de code fictif présenté comme terminé ; un `TODO` est signalé dans la PR.
- Toute dimension produite par le code porte une `source` et une `confidence`.
- Aucun modèle ni asset dont la licence est inconnue ou non commerciale n'entre par défaut.
- Aucun appel réseau implicite. Un téléchargement de poids est une commande explicite.
- `make check` passe avant toute PR.

## Frontières d'architecture

Les couches sont imposées par import-linter (`pyproject.toml`) :

```
cli  >  api  >  pipeline  >  adapters  >  validation  >  scene  >  geom  >  schemas
```

`schemas` et `geom` n'importent ni torch, ni cv2, ni fastapi, ni bpy, ni trimesh.
Les imports lourds dans `adapters` se font à l'intérieur des fonctions.

## Commandes

Voir `make help` et [docs/contributing/setup.md](docs/contributing/setup.md).

## Commits

Convention : `type(scope): sujet` avec `type` parmi feat, fix, docs, test, refactor, chore.
