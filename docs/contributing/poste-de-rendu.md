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
  - le verrou du GPU `/tmp/pd-gpu.lock` est pris (`pd-lock gpu`, KAN-152) ;
  - le GPU sert déjà (`nvidia-smi` au-dessus de 0 Mio), ou `nvidia-smi` ne répond pas ;
  - des tests tournent : un verrou de `pd-lock` est pris (`/tmp/pd-e2e.lock`,
    `/tmp/pd-pytest-1.lock`, `/tmp/pd-pytest-2.lock`), ou un vrai lanceur de tests tourne
    (`pytest`, `python -m pytest`, `playwright test`). Un `grep pytest` ne compte pas.
- Le poste écrit aussi son journal dans `~/.local/state/plandesign/poste.log` (un fichier par
  jour, 14 jours gardés), avec chaque tour passé. Rien du projet n'y entre.
- KAN-162 : à chaque prise, le serveur dit combien de rendus attendent en file (en-tête
  `X-Render-Pending`, un entier seul). Quand le poste passe son tour, le journal le dit avec la
  ressource de `pd-lock` en cause et une commande courte (sans chemin ni argument long) :
  `En attente : 2 rendu(s) en file (verrou tenu par pytest-1 depuis 14:32, pytest test_x.py).`
  Avec un serveur d'avant (pas d'en-tête), les phrases restent celles d'avant. Le poste écrit
  aussi un état pour les agents : voir plus bas.
- Quand le garde-fou de Z-Image arrête un calcul (ou ne le lance pas), le poste envoie la cause
  exacte avec la valeur relevée et le seuil, sans aucun texte : `vram_depassee` (Mio, seuil
  5 000), `nvidia_smi_illisible` (lectures ratées d'affilée, seuil 3),
  `nvidia_smi_illisible_depart`, `carte_occupee` (Mio, seuil 500), `ram_basse` (Mio libres,
  minimum 1 500), `duree_max` (secondes, plafond 900). La phrase montrée à la personne est
  écrite par le serveur. Vers un serveur d'avant, le poste envoie `vram` comme avant.
- Un échec dû au poste (garde-fou, moteur absent, délai dépassé, connexion perdue) ne compte
  pas dans le quota du jour de la personne, ni une demande expirée (personne ne l'a prise à
  temps : poste éteint ou occupé).
- Si le serveur refuse le jeton (401 ou 403), le poste attend 1 min, puis 5 min à chaque essai
  suivant.
- Pour un 429, une panne du serveur ou du réseau, il attend 15 s, puis 30 s, puis 1 min, et
  ainsi de suite, jusqu'à 5 min au plus.
- Pour essayer l'installation sans GPU, mettez `PLANDESIGN_WORKER_ENGINE=fake` dans
  `worker.env` : le poste rend alors une image unie.

## État du poste pour les agents (KAN-162)

Le poste écrit son état dans `/tmp/pd-poste.etat`, pour que les agents de la machine lui
cèdent la place à temps (la garde `pd-poste-libre` de l'Infra, appelée par `pd-lock`, le lit).

```json
{"en_file": 2, "rendu_en_cours": false, "horodatage": "2026-11-07T13:05:12+00:00", "version": 1}
```

| Clé | Valeur |
|---|---|
| `en_file` | Demandes en attente que le poste pourrait prendre (mêmes filtres que la prise), lues à la dernière prise ; `null` si le serveur ne le dit pas (serveur d'avant KAN-162, serveur injoignable, poste arrêté) |
| `rendu_en_cours` | `true` pendant un calcul et son envoi, sinon `false` ; jamais d'identifiant de demande |
| `horodatage` | Heure de l'écriture, ISO 8601 en UTC |
| `version` | Version de ce format : `1` |

- Le fichier est réécrit à **chaque tour** (rendu pris, tour passé, pause, serveur en erreur),
  et au moins toutes les 60 s pendant les longues attentes et pendant un calcul. Plus vieux
  que 300 s, il est **périmé** : le poste est sans doute arrêté.
- L'écriture est **atomique** : un fichier temporaire dans le même dossier (`/tmp`), droits
  644, puis `os.replace`. Un lecteur ne voit jamais un fichier à moitié écrit.
- Il ne contient que ces quatre clés : ni compte, ni projet, ni identifiant.
- À l'arrêt, le poste écrit `en_file: null` et `rendu_en_cours: false`.

**Règle des agents** : un agent **cède** (ne lance pas de travail lourd : tests, bancs, GPU)
dès que `en_file > 0` ou que `rendu_en_cours` est vrai ; il **reprend** quand les deux sont nuls
(`en_file` à 0 ou `null`, `rendu_en_cours` faux), ou quand l'état est périmé.

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
  le moteur et la taille de l'image, et pour un arrêt du garde-fou la cause, la valeur relevée
  et le seuil. Il ne contient ni compte, ni projet, ni identifiant.
