# 14 · Tableau collaboratif : architecture validée, B0 fait

Note d'architecture (directive B d'augus, 14 octobre 2026), **validée par augus le 14 octobre
au soir** (décisions au § 14) ; **B0 exécuté le 15 octobre** (état au § 14). Elle dit ce qui
existe, ce qui manque, ce que le choix « cloud avec comptes » implique, ce que le VPS permet,
les lots, les coûts et les alternatives. Là où le texte dit « Traefik », lire « Caddy »
(décision d'augus, § 14) ; le reste tient.

## 1. Point de départ, sans détour

PlanDesign est aujourd'hui **local et mono-utilisateur** : un serveur FastAPI sur la machine
d'augus, des projets en fichiers (`data/<projet>/project.json`, `board.json`, `images/`,
`media/`, `runs/`), **aucun compte, aucune authentification d'utilisateur** (seulement un jeton
unique quand l'API est exposée, `openplan token`), aucune isolation entre utilisateurs. Blender et
Stable Diffusion tournent sur le GPU de la machine. La promesse affichée est « tout reste sur
cette machine » (sauf la case Gemini, texte seul).

La directive B suppose « authentification et isolation déjà en place » : **c'est faux**. Tout ce
qui suit commence par construire cette base.

Ce que B demande : partage d'un projet par lien avec rôles (viewer, commenter, editor, owner) ;
présence en temps réel (avatars, curseurs, sélection) ; tableau multi-utilisateur (déplacer,
redimensionner, créer, supprimer, commenter) ; conflits résolus par dernière écriture horodatée
avec annulation locale ; journal d'activité ; commentaires attachés à un objet ou à une zone ;
invitations qui expirent et se révoquent ; édition hors ligne avec état « synchronisation en
cours » ; partage dans un menu projet.

## 2. Le VPS (inventaire du 14 octobre, lecture seule)

| | |
|---|---|
| Hôte | VPS Hostinger en Europe ; `plandesign.fr` et `www` pointent vers lui (DNS en place) |
| Système | Ubuntu 24.04.4 LTS, noyau 6.8, x86_64, fuseau UTC, unattended-upgrades actif |
| Calcul | 2 vCPU (AMD EPYC 9354P), 7,8 Go de RAM, pas de swap, **pas de GPU** |
| Disque | 96 Go, 47 Go libres |
| Déjà installé (avant B0) | Docker + Compose, un proxy et des services du panneau de l'hébergeur, retirés en B0 |
| Accès | administration par clé SSH dédiée ; durcissement réalisé en B0 (§ 14) |
| Pays | Europe (Hostinger) ; le pays exact est à lire dans le panneau Hostinger (choix du centre de données) |

Conséquences : le VPS convient pour l'API, les comptes, le partage, la synchronisation et le
stockage des projets partagés. Il **ne convient pas** au calcul d'images : Stable Diffusion est
exclu (pas de GPU, 8 Go), et Blender en CPU sur 2 cœurs rendrait une vue 1280 × 960 à 64
échantillons en plusieurs minutes (contre 17 s sur le GPU d'augus). Le repli CPU n'est
raisonnable que pour des vignettes à faible échantillonnage, et seulement si augus le demande.

## 3. Le calcul : la question centrale

Remarque d'augus : « si le rendu dépend de mon PC, ce n'est pas un vrai auto-hébergement ».
Exact. Le VPS n'a pas de GPU, et ce que produit PlanDesign (plan depuis la photo, rendus
Blender, finition et inspiration par Stable Diffusion) est du calcul lourd. Trois options,
chiffrées avec les mesures du banc « CPU 2 cœurs » (§ 3.4) et le tarif public des GPU.

### 3.1 Option (1) : tout sur le VPS, en CPU

Le VPS fait tout, sur ses 2 vCPU et 8 Go sans swap. Blender passe en Cycles CPU à échantillons
réduits ; Stable Diffusion en CPU est soit désactivé, soit toléré avec un temps très long et une
RAM de pointe proche de la limite. Chemin zéro geste sur la photo 2 : voir le tableau 3.4. Coût :
0 €. Honnêteté : l'écran dit « finition IA indisponible » (ou « lente ») et « rendu CPU à
échantillons réduits ». Dépendance à aucun PC, mais un service très lent et sans IA d'images.

### 3.2 Option (2) : VPS + « postes de calcul » avec jeton — recommandée (PO)

Le VPS garde la file de tâches ; des **postes de calcul** tirent les tâches avec un jeton scopé
et renvoient les résultats : aujourd'hui le PC d'augus (RTX, Blender et SD déjà installés),
demain un GPU loué quelques heures, après-demain un petit serveur GPU. Quand **aucun poste n'est
connecté**, le VPS exécute lui-même les tâches en **repli CPU** (option 1) : plan et
aménagement tout de suite, rendu Blender à échantillons réduits, et l'écran dit « finition IA
indisponible : aucun poste de calcul connecté ». Cette option contient (1) et prépare (3) : un
GPU loué n'est qu'un poste de calcul de plus. Coût : 0 € tant que c'est le PC d'augus.

Protocole « poste de calcul » :

```
  VPS (file `jobs`)                               poste de calcul (agent `openplan agent`)
  ──────────────────                              ─────────────────────────────────────────
  POST /jobs (par un geste ou le chemin auto)
     état = queued, charge utile (projet, vue, options)
                                   ◄── GET /agent/jobs/next (jeton d'agent, long-polling 30 s)
     état = claimed, agent, échéance = +10 min  ──►  tâche {id, projet, charge utile, fichiers à tirer}
                                   ◄── GET /agent/projects/{id}/files/… (photo, project.json)
                                   ◄── POST /agent/jobs/{id}/progress {étape, restant}  (toutes les 2 s)
     diffusion WebSocket aux membres (« Rendu Vue d'ensemble… 12 s »)
                                   ◄── PUT  /agent/jobs/{id}/artifacts/<nom>  (preview.png, depth_z.npy, finished.png…)
                                   ◄── POST /agent/jobs/{id}/done {résultat}  (ou /failed {erreur})
     état = done, galerie et tableau mis à jour par le WebSocket
  Reprise sur panne : échéance dépassée sans progression → la tâche redevient queued (3 essais),
  un autre poste (ou le repli CPU du VPS) la reprend ; l'agent qui revient voit son bail expiré
  et jette son résultat partiel ; les artefacts sont envoyés en entier puis validés par une
  empreinte, jamais à moitié.
  Sécurité : jeton d'agent en empreinte, scopé (jobs et artefacts seulement), révocable ;
  connexion sortante seulement (rien d'ouvert chez augus) ; HTTPS.
```

### 3.3 Option (3) : GPU à la demande

Le VPS envoie les tâches d'images à un GPU loué à la seconde : RunPod (serverless ou pod,
RTX 4090 / A5000 : de l'ordre de 0,3 à 0,7 $ par heure), Modal (T4 / A10G : de l'ordre de 0,6 à
1,1 $ par heure, crédit gratuit mensuel de l'ordre de 30 $), Hugging Face Inference Endpoints
(T4 : de l'ordre de 0,5 $ par heure ; l'offre « serverless » gratuite ne tient pas nos modèles
avec ControlNet). Tarifs publics 2026 à vérifier le jour du choix. Chiffrage pour 50 projets
par mois : le chemin zéro geste prend 161 s de GPU sur la RTX d'augus ; sur un T4 loué, comptez
4 à 6 min, plus un démarrage à froid (poids à charger, 1 à 2 min) : environ 6 à 8 min de GPU par
projet, soit 5 à 7 heures par mois, **3 à 8 $ par mois** en facturation à la seconde (0 $ avec
le crédit Modal tant qu'il suffit). Blender en CPU reste sur le VPS ou part aussi sur le GPU
(image Docker à construire). **Les photos et rendus quittent le VPS** pour le fournisseur du
GPU : la mention de confidentialité doit le dire, et c'est une raison de garder (2) comme base.
Dans l'architecture (2), un GPU loué est simplement un poste de calcul lancé à la demande.

### 3.4 Mesures « CPU 2 cœurs » (machine d'augus, taskset 2 cœurs, 2 threads, GPU masqué)

Copie de la photo 2, magasin jetable, un seul passage ; RAM = RSS de pointe du processus (et
de Blender). Référence GPU (RTX, 14 oct.) : chemin complet 161 s, rendu 17 s par vue,
finition 9 à 12 s, inspiration 20 s.

| Étape | Temps CPU 2 cœurs | RAM de pointe | Repère GPU (RTX) |
|---|---|---|---|
| Plan depuis la photo (profondeur + ouvertures) | 48 s | 1,8 Go | 15 s |
| Aménagement (3 variantes) | 10 s | 1,8 Go | 2 s |
| Rendu Blender, 1 vue 1280 × 960, 64 échantillons | **> 600 s** (abandonné par le pont Blender, limite 600 s) | — | 17 s |
| Rendu Blender, 1 vue, 16 échantillons | 190 s | 2,1 Go | — |
| Rendu Blender, 1 vue, 4 échantillons | 87 s | 2,1 Go | — |
| Finition SD 1.5, 512 px, 30 pas (chargement compris) | 240 s | **10,2 Go** | 10 s (768 px) |
| Inspiration img2img SD 1.5, 512 px, 30 pas | 416 s | 10,2 Go | 20 s (768 px) |

Chemin zéro geste complet en CPU 2 cœurs, extrapolé : plan 48 s + aménagement 10 s + 3 vues à
16 échantillons 570 s + 3 finitions 720 s + 3 inspirations 1 250 s ≈ **43 minutes**, et SD ne
tient pas en mémoire ; sans SD : **≈ 10,5 minutes** pour 3 vignettes bruitées (contre 161 s
complets sur GPU).

Le rendu Blender coûte environ 53 s fixes (construction de la scène, pose d'exposition,
débruitage) plus environ 8,6 s par échantillon : 64 échantillons ≈ 10 min par vue, 30 min pour
les trois vues du chemin zéro geste.

Lecture :

- **Plan et aménagement** tiennent sur le VPS : environ 1 min et 1,8 Go. Le plan depuis la photo
  reste utilisable en CPU.
- **Blender en CPU** n'est acceptable qu'en vignettes : 16 échantillons donnent 3 min par vue
  (10 min pour trois vues, bruitées mais lisibles) ; 64 échantillons (la qualité actuelle) sont
  hors de portée. Un rendu CPU de deux ou trois vues bloque aussi les 2 cœurs du VPS, donc l'API,
  pendant ce temps : il faudrait limiter à une tâche à la fois et prévenir.
- **Stable Diffusion en CPU est exclu sur ce VPS** : 10,2 Go de RAM de pointe pour 8 Go sans
  swap (le processus serait tué), et 4 min par image à 512 px (contre 10 s sur GPU). Le repli CPU
  doit donc dire « finition IA et inspiration indisponibles » tant qu'aucun poste de calcul n'est
  connecté. (Une variante bf16 pourrait descendre vers 5 à 6 Go, à 8 min par image : non
  mesurée, non recommandée.)
- Conclusion : l'option (1) seule donne un produit lent et sans images IA ; l'option (2) avec le
  PC d'augus (ou un GPU loué) comme poste de calcul est la seule qui garde le chemin zéro geste
  sous 5 minutes ; le repli CPU (plan, aménagement, vignettes 16 échantillons) sert quand aucun
  poste n'est connecté.

## 4. Architecture cible : « VPS + postes de calcul » (option 2)

```
  navigateur A ─┐                                  ┌─ machine d'augus (poste de calcul)
  navigateur B ─┼─ HTTPS/WSS ─► VPS plandesign.fr ◄─┤  agent local : tire les tâches par jeton,
  navigateur C ─┘   (Traefik + Let's Encrypt)     │  exécute Blender / SD / profondeur / OWLv2,
                       API FastAPI + comptes       │  pousse rendus et images vers le projet
                       WebSocket présence + ops    └─ (jamais exposé, sortant seulement)
                       Postgres + fichiers projets
```

- **VPS** : le serveur FastAPI existant, auquel s'ajoutent une couche comptes/sessions, la
  vérification d'appartenance sur chaque route, les projets partagés, un WebSocket par projet
  (présence, opérations du tableau, commentaires, activité) et une file de tâches de calcul.
  Déploiement avec le `Dockerfile` et le `docker-compose` existants, complétés d'un service
  Postgres et de Traefik (celui du VPS peut être réutilisé, ou remplacé par le nôtre : décision
  d'augus, voir § 10). Fichiers des projets dans `/srv/plandesign/data` (47 Go libres).
- **Poste de calcul** : la machine d'augus garde Blender, SD 1.5, l'estimateur de profondeur et
  le détecteur d'ouvertures. Un **agent local** (une commande `openplan agent --server
  https://plandesign.fr --token …`) interroge la file de tâches, exécute sur place et renvoie
  les artefacts. Connexion sortante seulement : rien n'est ouvert sur la machine d'augus. Quand
  l'agent est éteint, les tâches attendent et l'écran le dit (« en attente du poste de calcul »).
- **Projets locaux** : l'application locale reste telle quelle. Un projet est local jusqu'à ce que
  son propriétaire le partage ; il est alors copié sur le VPS et devient un projet partagé.

## 5. Comptes et authentification (gratuit, sans tiers)

- **Compte** : e-mail + mot de passe, haché par Argon2id (bibliothèque `argon2-cffi`), session
  par cookie `HttpOnly; Secure; SameSite=Lax`, durée 30 jours, renouvelée à l'usage, liste des
  sessions révocable. Aucun fournisseur tiers (Google, GitHub) : pas de dépendance, rien ne part.
- **Lien magique** (connexion sans mot de passe) seulement si un SMTP existe ; sinon mot de passe.
- **Invitations** : un lien `https://plandesign.fr/join/<jeton>` ; le jeton (32 octets
  aléatoires) n'est gardé qu'en empreinte, porte le projet, le rôle, une expiration (7 jours par
  défaut) et un compteur d'usages ; révocable par le propriétaire. Envoi par e-mail si SMTP,
  sinon lien à copier (l'écran le dit).
- **Agent de calcul** : jeton dédié, scopé « calcul » (ne lit que les tâches et n'écrit que des
  artefacts), révocable, affiché une seule fois à sa création, gardé dans le `.env` de la machine
  d'augus.
- **Rôles** : owner (tout, dont partage et suppression), editor (plan et tableau), commenter
  (commentaires seulement), viewer (lecture). Le premier compte créé est administrateur du
  serveur (crée les autres comptes ou ouvre l'inscription : réglage).

## 6. Modèle de données (Postgres)

- `users` (id, email, mot de passe haché, nom, créé le) ; `sessions` (id, user, expiration,
  révoquée) ; `invitations` (id, projet, rôle, empreinte du jeton, expiration, usages, révoquée).
- `projects` (id, owner, nom, créé le, `project.json` et fichiers sur disque comme aujourd'hui) ;
  `members` (projet, user, rôle).
- `board_items` : chaque élément du tableau devient une ligne avec **`updated_at` (horodatage
  serveur) et `updated_by`** : c'est la base de la dernière écriture gagnante par objet, plus fine
  que la révision globale actuelle de `board.json`. Le `board.json` reste le format du projet
  local et de l'export ; sur le VPS il est une vue de cette table.
- `comments` (id, projet, cible = élément ou zone `{x, y, w, h}`, auteur, texte, créé le,
  résolu) ; `activity` (projet, auteur, sorte, détail, horodatage : « Alice a ajouté une
  image ») ; `jobs` (projet, sorte, charge utile, état, agent, créé/pris/fini, résultat).
- Le plan (géométrie, historique en patches) reste un document unique par niveau : voir § 7.

## 7. Protocole de synchronisation

- **Transport** : REST pour tout ce qui existe déjà ; un WebSocket par projet
  (`/ws/projects/{id}`, authentifié par la session) pour la présence (entrée, sortie, curseur,
  sélection, toutes les 100 ms au plus), les opérations du tableau, les commentaires et
  l'activité. Diffusion par le serveur à tous les membres connectés.
- **Tableau (LWW)** : une opération = `item.set` (élément entier) ou `item.delete`, avec
  l'horodatage **attribué par le serveur** à la réception (les horloges des clients ne comptent
  pas). Conflit : la dernière reçue gagne, objet par objet. Chaque client garde sa pile
  d'annulation locale (B2) ; annuler rejoue l'état précédent de l'objet comme une nouvelle
  écriture, donc visible des autres, et l'activité le note.
- **Plan** : les gestes sur le plan sont des patches ordonnés sur un seul historique ; fusionner
  deux historiques concurrents n'est pas raisonnable en V1. Règle honnête : **un seul éditeur à la
  fois sur le plan** (bail de 2 minutes renouvelé tant qu'il édite, visible des autres : « Bob
  modifie le plan ») ; les autres regardent et reçoivent chaque pas. Le tableau, lui, est
  vraiment multi-utilisateur.
- **Hors ligne** : les opérations sont mises en file localement (IndexedDB) et rejouées à la
  reconnexion dans l'ordre ; l'indicateur d'enregistrement affiche « synchronisation en cours »
  puis « synchronisé » ; une opération rejetée (objet supprimé entre-temps, plan verrouillé)
  est dite, pas perdue en silence.
- **Calcul** : un geste « Rendre » crée une tâche ; l'agent la prend (`claimed`), envoie la
  progression, pousse les fichiers ; la galerie et le tableau se mettent à jour par le WebSocket.

## 8. Sécurité

- HTTPS partout (Traefik + Let's Encrypt, HSTS) ; WebSocket en WSS seulement.
- Chaque route vérifie l'appartenance et le rôle ; les fichiers (photos, rendus, pièces jointes)
  sont servis par l'API après cette vérification, jamais par un chemin statique.
- Jetons : sessions et invitations gardés en empreinte, expirés et révocables ; jeton d'agent
  scopé ; rotation possible ; journal des connexions.
- VPS : durci en B0 (accès par clé seule, pare-feu, bannissement des abus, sauvegardes nocturnes) ;
  le détail des réglages est tenu dans le journal d'exploitation du serveur, hors dépôt.
- Aucun secret dans le dépôt, les messages ni Notion : tout dans un `.env` hors dépôt sur le VPS
  et sur la machine d'augus.

## 9. La promesse de confidentialité change

Pour un projet **partagé**, « tout reste sur cette machine » n'est plus vrai : photos, plans,
rendus et fichiers joints sont copiés sur le VPS (Hostinger, Europe, pays à confirmer) et visibles
des membres selon leur rôle. L'écran doit le dire au moment du partage (« ce projet sera copié sur
plandesign.fr et visible de ses membres »), et le pied de page doit distinguer projet local et
projet partagé. Gemini ne change pas (texte seul, case à cocher). Les projets non partagés gardent
la promesse actuelle.

## 10. Ce qu'augus doit fournir ou décider

1. Le **pays** du centre de données (panneau Hostinger) pour l'écrire dans la mention.
2. **SMTP** ou non (compte d'envoi) : sans SMTP, invitations par lien à copier et pas de lien
   magique.
3. Que faire des conteneurs déjà sur le VPS : garder Traefik/OpenClaw/Ollama (ils tiennent 80
   et 443) ou les retirer.
4. Accord pour durcir le VPS : lot B0.
5. Une adresse e-mail d'administration (certificats Let's Encrypt, premier compte).
6. La destination des sauvegardes.
7. Le repli CPU pour Blender : non recommandé ; à activer seulement sur demande.

## 11. Lots, estimation et coût

| Lot | Contenu | Estimation |
|---|---|---|
| B0 | Durcissement du VPS, déploiement vide (compose, Traefik, HTTPS), sauvegardes, page « bientôt » | 1 journée |
| B1 | Comptes, sessions, membres, rôles, invitations (expiration, révocation), menu Partager | 2 à 3 journées |
| B2 | Projets partagés : copie au partage, vérification d'appartenance sur toutes les routes, WebSocket présence (avatars, curseurs, sélection), tableau LWW par objet avec annulation locale | 3 à 4 journées |
| B3 | Agent de calcul : file de tâches, jeton scopé, Blender/SD sur la machine d'augus, progression et push des rendus | 2 à 3 journées |
| B4 | Commentaires (objet ou zone), journal d'activité, hors ligne avec file rejouée et état « synchronisation en cours », bail d'édition du plan | 2 à 3 journées |
| B5 | Mentions de confidentialité par projet, guide, mesures (temps de synchronisation, conflits) | 1 journée |

Une « journée » est une journée de travail de l'agent (sessions, validation, captures). Total :
11 à 15 journées. **Coût d'hébergement : 0 € hors VPS** (déjà payé) : Let's Encrypt, Postgres,
Traefik, Argon2 sont libres ; aucun service tiers ; un SMTP gratuit est possible mais limité.

## 12. Alternatives

- **Réseau local sans comptes** (palier intermédiaire) : le serveur d'augus accessible en LAN ou
  par Tailscale, un lien de projet avec jeton, présence et tableau LWW ; pas de comptes ni de
  VPS ; Blender/SD restent où ils sont. 2 à 3 journées, 0 €. Limite : pas d'accès hors du réseau
  sans Tailscale, un seul propriétaire.
- **VPS relais sans comptes** : le lien secret vaut accès ; simple mais tout fuit si le lien
  circule ; déconseillé au-delà d'un essai.
- **Cloud avec comptes** (choix d'augus) : cette note.

## 13. Décision attendue

Validation de cette note par augus, réponses au § 10, puis B0. Rien d'autre sur le VPS avant.

## 14. Décisions d'augus (14 octobre) et état après B0 (15 octobre)

Décisions, relayées par le PO le 14 octobre (20:45 et 22:0x) :

1. **Calcul : option 2** (VPS + postes de calcul par jeton), avec repli CPU du VPS et mention
   « finition IA indisponible » quand aucun poste n'est connecté.
2. **Pas de SMTP** : invitations par lien à copier (SMTP possible plus tard).
3. **Caddy** à la place de Traefik ; OpenClaw et Ollama retirés.
4. **Durcissement** accordé pour B0. Augus n'installe pas de clé personnelle : son accès de
   secours est la console de l'hébergeur.
5. **Sauvegardes** : les sauvegardes Hostinger, déjà actives, sont la principale ; archive
   nocturne locale de `/srv/plandesign` gardée 7 jours ; rapatriement chez augus optionnel.
6. E-mail d'administration (Let's Encrypt, premier compte) : fourni, écrit **seulement** dans le
   `.env` du VPS (droits 600), jamais dans le dépôt, la doc ni les messages.

B0 exécuté le 15 octobre (étapes 2 à 7, un script autonome par étape avec pré-vérifications,
vérifications et retour arrière, un compte rendu par étape ; snapshot Hostinger fait avant) :

Fait : pare-feu restreint au web et à SSH, bannissement automatique des abus, retrait des services
inutiles, Caddy 2 avec Let's Encrypt et HSTS pour `plandesign.fr` et `www` (page « bientôt »),
accès SSH par clé seule, archive nocturne. Chaque étape a été vérifiée de l'extérieur. Le détail
(commandes, ports, réglages, chemins) est tenu dans le journal d'exploitation du serveur, hors
dépôt public. Suite de l'exploitation : note 15 (CI/CD, déploiement, surveillance).

Suite : B1 (comptes, sessions, membres, rôles, invitations par lien) sur go d'augus.
