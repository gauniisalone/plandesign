# Poste de rendu (KAN-89)

Les rendus réalistes demandés sur app.plandesign.fr sont calculés par le poste d'augus, pour
son compte seulement. Le poste appelle le serveur, jamais l'inverse : aucun port n'est ouvert
sur le poste, il n'y a ni tunnel ni SSH. Il fait un seul appel régulier, toutes les 10 à 15 s,
qui sert aussi de battement de cœur. Il calcule un rendu à la fois, sans réseau, puis envoie
l'image.

## Une seule fois : le jeton

Dans WSL (Debian), depuis le dépôt :

```bash
uv run openplan worker token
```

- La commande crée `~/.config/plandesign/worker.env`, avec les droits 600. Ce fichier contient
  le jeton, qui n'est jamais affiché.
- Elle affiche seulement **l'empreinte** du jeton. Donnez-la à l'Infra, qui la pose dans
  `OPENPLAN_WORKER_TOKEN_SHA256` sur le serveur.
- L'Infra pose aussi votre identifiant de compte (un uuid) dans `OPENPLAN_RENDER_ACCOUNTS`.

Variables du serveur :

| Variable | Rôle | Défaut |
|---|---|---|
| `OPENPLAN_RENDER_ACCOUNTS` | Comptes autorisés (uuid, séparés par des virgules) | personne |
| `OPENPLAN_WORKER_TOKEN_SHA256` | Empreinte(s) du jeton du poste | API du poste fermée |
| `OPENPLAN_RENDER_EXPIRE_H` | Délai d'expiration d'une demande non prise (heures) | 24 |
| `OPENPLAN_RENDER_DAILY_QUOTA` | Demandes par compte sur 24 h | 20 |

Si ces variables sont absentes ou mal formées, l'application démarre quand même : le rendu
réaliste est alors désactivé.

## Lancer, mettre en pause, arrêter

```bash
uv run openplan worker run       # lance le poste ; Ctrl+C l'arrête proprement
uv run openplan worker pause     # reste en ligne, ne prend plus de rendu
uv run openplan worker resume    # reprend
uv run openplan worker stop      # s'arrête après le calcul en cours
uv run openplan worker status    # adresse, moteur, pause : jamais le jeton
```

- Si vous faites Ctrl+C pendant un calcul, le calcul s'arrête. La demande revient dans la file
  après 30 min.
- Le poste passe son tour, sans rien prendre, dans trois cas :
  - le verrou `/tmp/pd-lourd.lock` est pris ;
  - le GPU sert déjà (`nvidia-smi` au-dessus de 0 Mio), ou `nvidia-smi` ne répond pas ;
  - des tests tournent (`playwright` ou `pytest`).
- Si le serveur refuse le jeton (401 ou 403), le poste attend 1 min, puis 5 min à chaque essai
  suivant.
- Pour un 429, une panne du serveur ou du réseau, il attend 15 s, puis 30 s, puis 1 min, et
  ainsi de suite, jusqu'à 5 min au plus.
- Pour essayer l'installation sans GPU, mettez `PLANDESIGN_WORKER_ENGINE=fake` dans
  `worker.env` : le poste rend alors une image unie.

## Révoquer le jeton

1. Demandez à l'Infra de retirer l'empreinte de `OPENPLAN_WORKER_TOKEN_SHA256` et de relancer
   le conteneur. Le poste reçoit alors des 401 et attend 5 min entre deux essais.
2. Sur le poste : `uv run openplan worker stop`, puis supprimez
   `~/.config/plandesign/worker.env`.
3. Pour un nouveau jeton : `uv run openplan worker token --force`, puis donnez la nouvelle
   empreinte à l'Infra.

Pour changer de jeton sans coupure, l'Infra peut poser les deux empreintes, séparées par une
virgule, le temps de la bascule.

## Ce que le poste reçoit, et ce qu'il garde

- Le serveur ne lui envoie que du JSON validé (la demande, le projet, le bake et la caméra de
  la vue), sous plafond :

  | Fichier | Plafond |
  |---|---|
  | projet | 8 Mo |
  | bake | 8 Mo |
  | caméra | 64 Kio |

- Le poste n'exécute rien de ce qu'il reçoit : aucune commande, aucun chemin.
- Le calcul tourne dans un sous-processus sans réseau : `unshare -rn` quand le noyau le permet,
  sinon une garde de socket. Le jeton n'est pas transmis à ce sous-processus.
- L'image déposée est un PNG de 8 Mo au plus, dont le grand côté fait 2048 px au plus.
- Une demande non prise expire après 24 h (réglable). Un rendu fait est rangé avec les images
  du projet : il compte dans la place du compte et n'est jamais purgé.
- Le journal des rendus (`<data>/renders.jsonl`) ne garde que la date, le résultat, la durée,
  le moteur et la taille de l'image. Il ne contient ni compte, ni projet, ni identifiant.
