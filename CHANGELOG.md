# Journal des versions

Format : une section par version, les changements visibles par l'utilisateur d'abord, la date
de Paris. Le détail par incrément (fichiers, tests, mesures) est dans `docs/STATUS.md` ; les
décisions dans `docs/architecture/`. Le numéro de version est celui de `pyproject.toml` et de
`openplan --version`.

## 0.1.0.dev0 (en cours) — 23 → 28 septembre 2026

### 23 octobre 2026 (KAN-91, lots L1 et L1b : l'espace de travail en cartes reliées)

- Des liens relient les cartes : carte Photos → plan (ses photos le construisent), plan →
  carte Rendus (la géométrie rendue), carte Inspirations → carte Rendus (le style). PlanDesign
  les pose tout seul (une fois les photos reliées aux plans et les inspirations aux rendus,
  un bandeau le dit) ; une poignée « + » au bord d'une carte en tire un autre, les cibles
  valides restent vives, une cible refusée est expliquée. Survol : ce que la génération lira
  vraiment (« Les 2 photos de cette carte construisent Plan · RDC… »). Clic : « Couper » (ou
  Suppr), avec « Annuler » ; un lien coupé à la main n'est jamais recréé.
- Décision C1 d'augus : une génération ne lit que les membres des cartes reliées. Le plan ne se
  construit que depuis les photos de la carte Photos reliée (409 sinon) ; les rendus ne lisent
  que les images des cartes Inspirations reliées à leur carte Rendus (elles remplacent les
  images cochées « Référence », qui suit désormais l'appartenance) ; une image posée librement
  porte la pastille « hors carte : non utilisée ».
- Glisser une image dans une carte en fait un membre, la sortir la retire : la carte visée
  s'entoure, une phrase dit l'effet (« Déposer : cette photo servira au Plan · RDC »), un
  bandeau confirme avec « Annuler ». Une inspiration déposée dans la carte Photos est refusée,
  avec la raison. Une photo entrée dans la carte du plan est analysée (sauf si elle l'a déjà
  été) ; une photo sortie quitte le plan (sa caméra estimée), après le délai d'« Annuler ».
- Une carte Rendus sans plan relié ne rend pas : « Reliez un plan pour rendre », ses rendus
  restent visibles, atténués (D2) ; la demande de rendu réaliste est refusée en 409.
  Plusieurs cartes Inspirations peuvent guider un rendu, à poids égal (D1). Tous les éditeurs
  du projet modifient les liens (D3).
- « Référence » ne se coche plus : « Dans Inspirations » range l'image dans la carte. Les images
  cochées hors de toute carte (projets d'avant) entrent d'elles-mêmes dans la carte Inspirations.
- Format 0.18.0 (`board.json` : liens, paires coupées, étiquettes ignorées). Retour arrière :
  `openplan downgrade --to 0.17.0` retire ces champs (aller-retour vérifié avec la production
  a1c630f).

### 22 octobre 2026 (KAN-88 : rendu local par Z-Image-Turbo)

- En local, la finition IA des rendus passe par **Z-Image-Turbo** dès que ses quatre poids sont
  installés et qu'une carte CUDA est là (4/5 à l'essai KAN-84, contre 2/5 pour SD 1.5) ; sinon,
  ou avec `OPENPLAN_FINISH_ENGINE=sd15`, la chaîne SD 1.5 d'avant, inchangée. L'inspiration reste
  sur SD 1.5. En ligne, rien ne change.
- Chaîne : transformer GGUF Q4_K_M, ControlNet Union 2.1 complet guidé à 0,7 par les arêtes de
  la géométrie de la vue (rastérisées depuis `bake.json` et `camera.json`, sans la texture), 8 pas,
  1 024 px de grand côté ; encodeur Qwen3-4B sur le processeur, qui lit la description entière
  de KAN-86. ≈ 1 min de génération par vue sur une carte de 6 Go, plus ≈ 45 s d'encodage.
- **Garde-fou** : la génération tourne dans un processus à part, surveillé toutes les 0,5 s ;
  au-delà de 5 000 Mio sur la carte (ou sous 1 500 Mio de RAM libre, ou après 15 min), il est
  tué et la finition échoue avec un message clair, sans geler la machine. Rien ne part si
  nvidia-smi est illisible ou si la carte sert déjà.
- **Aucun réseau** pendant la génération : poids lus sur le disque seulement, connexions
  refusées dans le processus, espace de noms réseau vide (`unshare -rn`) quand c'est possible.
- `openplan models pull <id>` : révisions figées et empreintes sha256 vérifiées ; `--from
  <dossier>` reprend des poids déjà sur la machine par lien (aucune place en plus, aucun
  téléchargement), après vérification de toutes les empreintes.
- Sous des combles, avec Z-Image, les vues automatiques gagnent une **vue dans l'axe du
  faîtage** : œil à 1,60 m, en portrait, vers le pignon, du côté qui voit le plus de meubles.
- `openplan.pipeline.zimage_render.render_scene` : rendu d'une vue depuis bake + caméra
  (+ projet pour la description), point d'entrée du worker de rendu (KAN-89).
- Dépendance : `gguf` (MIT) et diffusers 0.40 au moins, dans l'extra `neural` seulement : l'image
  Docker de production ne change pas (mêmes paquets, vérifié par `uv export`).

### 22 octobre 2026 (KAN-86 : la description du rendu suit la scène)

- La finition IA et l'inspiration décrivent la scène réellement rendue (`bake.json`, à côté de
  l'image), plus seulement l'ambiance : matière dominante de chaque surface (sol, murs, pignons
  d'un comble, pentes ou plafond, charpente), meubles vus dans la vue, nommés et comptés
  (« four single beds, a low open bookcase »), habillage avec les couleurs de sa palette
  (literie, tapis de jute, suspension en rotin, lampes, plantes). Un grenier aux murs de pierre
  n'est plus décrit en « plastered walls » ; nommer les meubles évite qu'un modèle d'images en
  invente un autre (cheminée à la place de la bibliothèque basse, KAN-84b).
- Longueur selon le moteur : 60 mots pour SD 1.5 (CLIP), budget long pour Z-Image (KAN-88).
  Rendu d'avant sans `bake.json` : le prompt d'ambiance, inchangé.

### 21 octobre 2026 (KAN-72 : combles, hauteurs justes)

- La profondeur apprise penche le sol de quelques degrés (≈ 6° sur la scène de contrôle) : les
  hauteurs étaient fausses d'autant plus que le point était loin (faîtage trop haut, caméra trop
  basse). La verticale est maintenant corrigée : un pignon est vertical (sans pignon vu, les
  lignes de fuite quand elles sont sûres). Dit dans les avertissements (« verticale corrigée de
  6,6° »).
- Toit supposé symétrique quand les deux versants et les deux jambettes sont vus et que leurs
  pentes lues diffèrent de 8° au plus : pente commune (moyenne pondérée par le nombre de points de
  chaque versant), même égout des deux côtés, faîtage tiré de l'égout et de la largeur plutôt que
  de l'intersection des deux pentes (qui amplifiait leur erreur). Dit à l'écran (« toit supposé
  symétrique : pente commune 48,3° (lues 46,6° et 50,1°) »). Un toit dissymétrique garde ses
  deux pentes.
- Scène de contrôle, avec la seule hauteur de prise de vue saisie : largeur 4,79 m (vérité
  5,00), faîtage 3,74 m (3,54), égouts 1,05 m (1,00), caméra 1,45 m ; avant : 6,75 m, 5,69 m, 1,73
  et 1,46 m. Avec une mesure de référence : tout à ±10 %. Le vrai grenier ne change pas (aucun
  pignon vu).

### 21 octobre 2026 (KAN-75 : retour arrière sans perdre les données)

- `openplan downgrade --to 0.16.0 <racine des données>` redescend le format des projets vers
  celui de la version de production (MEP 5, 3da4d52), pour revenir à l'ancienne application sans
  restaurer l'archive : seules les données des champs ajoutés depuis sont retirées (ici, la
  confiance tenue avec l'échelle). Idempotent, `--dry-run`, écriture atomique projet par projet.
  Les événements et réactions d'un type nouveau restent en place : l'ancienne application les
  ignore sans erreur.
- Procédure (Infra) : arrêter l'application, `openplan downgrade --to 0.16.0 <racine>`, puis
  basculer sur l'ancienne version ; l'archive de `data/` reste le dernier recours.
- Tests : un aller-retour réel (le code de production, extrait par `git archive`, relit la
  liste, le plan, l'espace de travail, les commentaires, l'activité et la cloche des projets
  redescendus, sans 409 ni 500) ; une garde qui échoue si un champ ajouté au format n'est ni
  retiré par la descente ni toléré par l'ancien code (instantané des champs de la production).

### 21 octobre 2026 (KAN-46 : jambettes déduites du bord du sol)

- Combles : les jambettes, trop petites pour être trouvées comme plans, sont déduites du bord
  du sol vu quand le sol va jusqu'au pied d'une pente qui s'arrête nettement au-dessus de lui.
  L'égout n'est plus « au sol » et la largeur est prise entre jambettes, plus là où les pentes
  rencontreraient le sol. Sur la scène de contrôle, avec une mesure de référence : largeur
  4,86 m pour 5,00 (avant : 7,50 m), égouts 1,05 et 1,24 m pour 1,00 (avant : 0). Bord du sol
  robuste aux points aberrants. Appareil levé (sol hors champ) : inchangé, égout inconnu. Le
  faîtage et l'égout restent étirés par la profondeur apprise (KAN-72).

### 21 octobre 2026 (KAN-66, lot 1 serveur : mesure de référence sur une photo)

- Une longueur connue sur la photo (deux points, « une porte de 2,04 m ») recale le plan
  estimé depuis cette photo, hauteurs comprises : calcul géométrique sur la profondeur gardée
  à l'estimation, sans nouveau passage du modèle ; annulable, retouches gardées ; les murs
  restent estimés. Une mesure par photo : une nouvelle remplace l'ancienne ; la retirer rend
  l'échelle de la hauteur de prise de vue. Refus motivés : points hors de la photo ou sur deux
  surfaces, longueur hors de 5 cm à 30 m, échelle changée de plus de ×3.
- Chaque plan estimé depuis une photo dit sur quoi repose son échelle (`Level.scale_basis`) :
  « Échelle calée sur votre mesure de 2,04 m (porte) », « Échelle : hauteur de prise de vue
  saisie 1,45 m » ou « … supposée 1,50 m (par défaut) ». La confiance de l'estimation (KAN-31)
  y est tenue à jour par le serveur : une mesure la fait passer à « à vérifier » (« échelle
  calée sur votre mesure »), la retirer rend le niveau d'avant.
- Sur la scène de contrôle des combles, une référence exacte divise l'erreur de largeur au
  moins par deux (+108 % → +50 %) et ramène le faîtage dans ±20 % (+61 % → +16 %). L'écart de
  largeur restant vient surtout de la largeur prise au sol (jambettes non extraites, KAN-27) :
  la vraie valeur à cet endroit est 6,97 m, par rapport à laquelle l'écart est de +5 à +8 %.
  Le faîtage (+13/16 %) reste à traiter (KAN-46).
- Profondeur gardée par photo dans `images/` (réduite, ~0,6 Mo), comprise dans l'archive du
  projet et supprimée avec la photo ; une photo d'avant se réestime sur un geste
  (`POST …/levels/{id}/images/{id}/reestimate`, tâche de fond). Routes : `PUT
  /projects/{id}/images/{id}/reference`, `DELETE …/reference/{segment}` (éditeur ; comme les
  autres écritures du plan, refusées en 409 quand une autre personne tient ce plan). Schéma
  0.16.0 (`ImageRef.reference_segments`, `Level.scale_basis`), migration sans perte.

### 20 octobre 2026 (KAN-32 : l'Assistant modifie les portes et les fenêtres)

- « déplace la fenêtre de la chambre de 50 cm vers la gauche », « supprime la porte du
  placard », « élargis la fenêtre du salon à 1,20 m » : l'Assistant déplace une porte ou une
  fenêtre le long de son mur, la supprime ou change sa largeur ou sa hauteur. Gauche et
  droite se lisent depuis la pièce, face au mur. Même geste avec ou sans Gemini.
- Deux ouvertures possibles : il demande laquelle (« la fenêtre nord ou la fenêtre ouest ? »),
  sans rien faire au hasard. Hors du mur, sur un angle, sur une autre ouverture, au ras du
  plafond ou verrouillée : refus, avec la raison.
- Chaque geste s'annule et paraît au journal d'activité. Une demande explicite supprime aussi
  une ouverture que vous avez posée (« Annuler pour la remettre »).
- Avec les comptes : le lecteur ne peut pas modifier le plan par l'Assistant, et l'Assistant
  respecte la main sur le plan (une seule personne le modifie à la fois).

### 20 octobre 2026 (KAN-64 : plus rien ne se chevauche dans l'espace de travail)

- Le panneau de droite de l'espace de travail, comme chez Figma et Miro : en haut l'Assistant
  (vos exigences, Générer), dessous les Commentaires (ouverts, résolus, les fils,
  « Masquer les épingles »), en bas l'Activité, repliée. La séparation entre l'Assistant et
  les Commentaires se règle ; chaque zone se replie. Ouvert d'office : l'arrivée par le site
  trouve l'Assistant ouvert et prérempli, comme avant.
- Une sélection : ses actions prennent la place de l'Assistant, « ← Assistant » y revient.
- La barre « Commentaires (n) » du bas disparaît : la bulle de la barre du haut ouvre les
  Commentaires et compte les fils ouverts. En bas, il ne reste que le zoom.
- Panneau fermé, l'espace de travail prend toute la largeur ; il se rouvre d'un clic. Le
  cadrage de départ tient compte du panneau ouvert.
- La case Gemini reste visible, sur une ligne : « Mieux comprendre mon texte avec Gemini
  (texte seul) ».

### 20 octobre 2026 (KAN-43 : une vue par personne)

En local comme avec les comptes, à l'identique.

- Chacun retrouve SA vue de l'espace de travail (position et zoom) en rouvrant un projet, sur
  tous ses appareils ; lecteur et commentateur compris. Vos déplacements et vos zooms ne
  changent plus la vue des autres, et ne leur font plus rien relire.
- Sans vue enregistrée : le cadrage de départ (tout ce qui est posé). Le propriétaire reprend
  l'ancienne vue commune du projet, une fois.
- La vue n'est ni au journal, ni dans la cloche, ni dans l'archive ; un membre retiré perd la
  sienne. Suivre quelqu'un (B2b) ne change pas votre vue enregistrée.

### 20 octobre 2026 (KAN-58 : le journal d'activité du projet)

En local comme avec les comptes, à l'identique.

- Un panneau « Activité » dans le projet (barre du haut) : qui a fait quoi, sur quel objet,
  quand ; tout le journal, vos gestes compris, et aussi ce que la cloche ne notifie pas (un fil
  résolu ou rouvert). 50 lignes puis « Voir plus » ; tout membre le lit, lecteur compris.
- Le détail des gestes sur l'espace de travail : posé, modifié, retiré, avec le nom de
  l'élément quand il n'y en a qu'un (« Gilles a supprimé « Photo 2 » ») ; sur le plan, le
  libellé du geste (« Ajouter un mur », « Annulé : … »). Zoomer ou déplacer la vue n'est pas
  une modification (ni au journal, ni dans la cloche).
- Filtres par personne et par sorte (commentaires, espace de travail, plan, membres, rendus) ;
  un clic sur une ligne mène au bon endroit, comme la cloche.

### 20 octobre 2026 (KAN-52 : la cloche des notifications)

En local comme avec les comptes, à l'identique.

- Une cloche en haut à droite (accueil et atelier) : le nombre de non-lus (« 9+ » au-delà de
  9), rien quand tout est lu. Le panneau a deux onglets, « Non lues » et « Toutes », 50 lignes
  puis « Voir plus » ; chaque ligne dit qui, quoi, quel objet, quel projet et quand (« Fanny a
  commenté « Photo 2 » · Grenier · il y a 5 min »), un point signale les non-lus.
- Un clic sur une ligne la marque comme lue, elle seule, et ouvre le projet au bon endroit
  (l'épingle et son fil, l'élément, le plan, les rendus) ; « Tout marquer comme lu » remet le
  compteur à 0. L'état « lu » est le même sur tous vos appareils.
- Ce qui est notifié : un commentaire ou une réponse dans un fil où vous avez écrit (ou sur
  votre projet), un membre qui rejoint le projet, un rendu prêt (le vôtre aussi), une
  modification de l'espace de travail ou du plan par un autre membre, regroupée par auteur
  sur 10 min (« 12 changements »). Jamais vos propres gestes, rien d'avant votre arrivée dans
  le projet, seulement les projets dont vous êtes membre.
- En direct avec les comptes (un canal par compte) ; en local, relu toutes les 15 s.
- Un seul journal par projet (`events.jsonl`, partagé avec KAN-13), gardé 90 jours.

### 20 octobre 2026 (KAN-51 : commentaires de l'espace de travail)

En local comme avec les comptes, à l'identique.

- Outil « Commentaire » (touche c) : un clic pose une épingle n'importe où dans l'espace de
  travail, ou sur un élément (carte, photo, rendu, cadre du plan) ; elle le suit quand il bouge
  et reste, « détachée », s'il est supprimé. Les autres membres la voient en moins de 2 s.
- Un fil de réponses signées (nom, couleur, heure) ; « Résoudre » masque l'épingle, le fil
  reste dans « Résolus » et peut être rouvert ; panneau « Commentaires » ; les épingles se
  masquent d'un clic. Texte affiché comme du texte, 2 000 caractères au plus.
- Droits gardés par le serveur : lire, lecteur ; commenter, commentateur et plus ; seul
  l'auteur modifie ou supprime son message ; le propriétaire supprime tout fil (l'auteur, le
  sien tant qu'il n'a pas de réponse). Le rôle « commentateur » devient réel : il commente,
  sans modifier l'espace de travail ni le plan. Un ancien membre est signé « Ancien membre ».
- Stockage par projet (`comments.json`, emporté par l'archive du projet) ; chaque fil,
  réponse, résolution ou réouverture laisse une ligne dans `events.jsonl` (qui, quoi, où,
  quand ; jamais le texte), pour la future cloche (KAN-52).

### 19 octobre 2026 (KAN-12 : place disque par compte)

Avec les comptes seulement ; le mode local n'a pas de limite.

- Chaque compte a une place disque : la taille des projets dont il est le premier propriétaire
  (photos, fichiers joints, plans, rendus, tableau), mesurée par le serveur.
  `OPENPLAN_QUOTA_MB` (500 par défaut ; 0 = sans limite), documentée dans `.env.example`.
- Un envoi (photo, fichier joint) qui ferait dépasser la place est refusé avant toute
  écriture (413) : « Votre espace est plein (480 Mo sur 500 Mo) : supprimez des fichiers ou
  des projets. » Un calcul qui écrit des fichiers (rendus, détections, chemin automatique) est
  refusé avant son lancement s'il reste moins de 20 Mo (réserve d'un jeu de rendus) : il ne
  dépasse jamais la limite en cours de route. Les rendus comptent dans la place.
- Un membre invité consomme la place du propriétaire du projet ; le refus le dit, sans
  révéler la place de ce dernier.
- Réglages du compte : « Votre espace : 120 Mo sur 500 Mo », avec une barre. Sur ses propres
  projets, l'atelier prévient avant d'envoyer un fichier trop gros (rien ne part).
- Supprimer un fichier, une photo ou un projet libère la place tout de suite.
- `make check` et la CI lancent aussi les tests unitaires du front (`npm run test:unit`,
  `node --test`, KAN-47).

### 19 octobre 2026 (B2d : bail d'édition du plan, curseurs en mètres, KAN-11)

Avec les comptes seulement ; le mode local ne change pas.

- Une seule personne modifie un plan à la fois. Le bail se prend tout seul au premier geste et
  se prolonge à chaque geste : seule, on ne voit aucune différence.
- Tenu par un autre membre : bandeau « Fanny modifie ce plan », outils d'édition grisés (voir et
  se déplacer restent possibles), et toute écriture refusée par le serveur (409, même texte).
  « Demander la main » prévient la seule personne qui tient le plan (« Gilles demande la main
  · Rendre la main ») ; personne n'arrache la main, pas même le propriétaire.
- Le bail est rendu en quittant l'onglet Plan, quand la personne n'a plus aucune connexion, ou
  après 2 minutes sans geste ; « Prendre la main » n'apparaît qu'après l'expiration.
- Les écritures du système (chemin automatique, calculs lancés) ne sont jamais bloquées.
- Dans l'onglet Plan, les curseurs des autres apparaissent en mètres sur le même plan (10 par
  seconde au plus), nommés et à leur couleur.
- Bail en mémoire, dans le processus : perdu au redémarrage (le plan est alors libre).

### 19 octobre 2026 (B2c : espace de travail en dernière écriture par élément, KAN-10)

- Chaque geste n'enregistre que les éléments qu'il touche (`PATCH /api/projects/{id}/board/
  items` : éléments posés ou modifiés, retirés, vue) ; le serveur les horodate et note leur
  auteur (`updated_at`, `updated_by` par élément). Deux personnes qui déplacent deux cartes
  différentes ne se gênent plus ; sur la même carte, la dernière écriture gagne. Plus de refus
  « enregistré ailleurs, rechargé ».
- Les écritures pas encore confirmées restent par-dessus chaque relecture (geste d'un autre
  membre, projet changé) ; un geste terminé après une attente (déplacement, import de
  fichiers) part du tableau d'à présent, pas de celui du début. Une photo déposée n'a qu'une
  carte : celle que la synchronisation pose en même temps pour la même image (jamais vue de
  l'atelier) est retirée par l'atelier ; une copie « Dupliquer » n'est jamais touchée, et le
  serveur ne dédoublonne rien de lui-même.
- Annuler / rétablir sont à chacun : ils réécrivent l'état d'avant ou d'après de mes gestes,
  comme de nouvelles écritures visibles des autres ; un élément supprimé entre-temps par
  quelqu'un d'autre n'est pas recréé, et un message court le dit.
- La vue (déplacement, zoom) reste une par projet, en dernière écriture (une vue par personne :
  ticket à part).
- Compatibilité : le `PUT /api/projects/{id}/board` du tableau entier (avec son 409) ne sert
  plus qu'aux onglets ouverts sur une version d'avant B2c ; le front ne l'utilise plus. Il
  est gardé jusqu'au déploiement de B2d, et au moins jusqu'au 2 novembre 2026, puis retiré.

### 19 octobre 2026 (B2b : curseurs, sélections, « Suivre »)

Avec les comptes, dans l'espace de travail ; éphémère, rien n'est écrit ni stocké.

- Curseurs des autres membres, nommés et à leur couleur, en coordonnées de l'espace de travail
  (10 envois par seconde au plus ; disparus quand la personne quitte le canevas). Les curseurs
  en mètres dans l'onglet Plan iront avec le bail du plan (B2d).
- Sélections des autres : contour à leur couleur, avec leur nom ; un nouvel arrivant reçoit les
  sélections en cours.
- « Suivre » : un clic sur un avatar, et ma vue suit la sienne (centre et zoom, 4 fois par
  seconde au plus, envoyés seulement quand quelqu'un suit, et seulement à ceux qui suivent).
  Bandeau « Vous suivez … · Arrêter » ; tout geste de ma part, ou son départ, arrête de suivre.
- WebSocket : le serveur valide et relaie (jamais vers ses propres onglets), ignore un message
  invalide ; débit et taille toujours limités.
- Reconnexion : après deux échecs d'affilée sans réponse du serveur (un refus de poignée de
  main derrière l'intergiciel n'est qu'un échec), l'application lit /api/auth/me : en 401,
  retour à la connexion ; sinon, l'attente croissante continue.

### 19 octobre 2026 (bêta sur invitation, annoncée)

Un visiteur non invité ne croit plus le site cassé, sans qu'on lui dise si son adresse a un
compte. Les mentions n'apparaissent qu'en inscription sur invitation (`OPENPLAN_SIGNUP=invite`,
le défaut) ; ouverte, elles disparaissent sans changer de code.

- `/api/health` donne `signup` (« invite » ou « open »), avec les comptes seulement, et se lit
  depuis l'origine du site (CORS sans cookies).
- Page de connexion de l'app et fenêtre du site (option de build) : « PlanDesign est en bêta,
  sur invitation. Déjà inscrit ou invité ? Connectez-vous. » au-dessus du champ ; après
  l'envoi, « Si cette adresse a un compte ou une invitation, un code vient de partir. ».
  Arrivée par un lien d'invitation : la ligne d'invitation remplace la ligne « bêta ».
- Accueil du site, option de build seulement : « Bêta sur invitation » à côté de « Connexion
  par e-mail, sans mot de passe ». Le formulaire reste.
- Sitemap : `/contact/` ajouté avec l'option seulement (`scripts/login-assets.mjs`) ; sortie par
  défaut inchangée.

### 19 octobre 2026 (retours d'augus en production)

- **Surface (m²) des Questions guidées** : on peut de nouveau taper un nombre. Le champ était
  contrôlé par la valeur numérique bornée (8 à 400 m²) : chaque frappe sous 8 (« 3 » en route
  vers « 35 ») était effacée, et la virgule disparaissait. Le texte tapé est maintenant gardé
  (« 3 », « 35 », « 35,5 », « 35.5 ») ; la validation se fait à la sortie du champ, avec un
  message clair hors bornes (« Entre 8 et 400 m². »). Même règle pour « Chambres » (0 à 6),
  qui était ramené en silence à 6.
- **« + Nouveau plan »** quitte la barre du haut : il est dans l'espace de travail, sous la
  dernière rangée Plan + Rendus (même petite fenêtre), et reste absent pour un lecteur.
- **Arrivée depuis le site (/nouveau)** : le plan se génère seul depuis les exigences
  préremplies (« Générer le plan », moteur à règles, sans GPU), sans aucun geste ; pas de
  rendus en ligne, chemin automatique habituel sur une machine avec Blender. Si rien n'est
  lisible, le compositeur reste ouvert avec un message court (« Plan impossible sans
  surface : indiquez-la dans « Questions guidées ». »). Le préremplissage lui-même marchait
  (vérifié : redirection de production et paramètres gardés après la connexion) ; la surface
  du lien accepte aussi la virgule et plus d'une décimale (arrondie au dixième).
- e2e : `e2e/composer-numbers.spec.ts` (frappe touche par touche), `plans.spec.ts` (bouton
  dans l'espace de travail, sous les rangées), `e2e-online` (arrivée déconnectée par
  /nouveau, connexion, plan généré, aucun calcul de rendu ; description illisible),
  `e2e-accounts` (/nouveau : plan généré).
- Intégration (avec B2b) : la surface affichée est arrondie au dixième à la sortie du champ,
  comme la valeur enregistrée (« 35,55 » devient « 35,6 ») ; le plan de l'arrivée par /nouveau
  attend aussi que le plan ouvert soit chargé. `make e2e` lance aussi `e2e:site` (fenêtre
  « Se connecter » du site construit avec l'option, KAN-36).

### 19 octobre 2026 (site, page relue par augus)

Avec l'option de build seulement (la sortie par défaut reste celle de la production) :

- Plus aucune promesse de rendus ni de plan depuis une photo : intro de « Comment ça marche »,
  étape 2, cartes « Plan tracé ou généré » et « Un espace de travail », appel final, « Vos
  données » ; « Rendus réalistes : bientôt » au plus une fois par section.
- Six cartes de fonctionnalités, dont « Partage et collaboration » (lien d'invitation, rôles,
  présence en temps réel).
- Illustrations redessinées : étape 1, une note de texte et un plan DXF ; étape 3, la pièce en
  3D isométrique. L'encart « Visuel à fournir » est retiré.
- Téléphone : l'en-tête ne déborde plus (« Ouvrir mon espace » masqué sous 520 px, comme en
  production) ; la fenêtre de connexion dit seulement « Sans mot de passe. » en tête.

Application :

- Démarrage : pendant la lecture de /api/health, l'application affiche son squelette (barre du
  haut et fond), jamais une page blanche ; au-delà de 10 s sans réponse, « Connexion au
  serveur… ».
- e2e : après chaque chargement, les tests attendent l'application affichée (repère
  `data-app-ready`) et non son squelette ; un e2e couvre le démarrage lent.

### 19 octobre 2026 (démarrage plus rapide)

- L'application ne lit plus `/api/health` qu'une fois avant de s'afficher : les mentions
  (hébergement) et ce que le serveur sait calculer partagent la même réponse ; seules les
  relectures périodiques des capacités refont une requête.

### 19 octobre 2026 (MVP en ligne sans GPU)

Le MVP ouvre sans rendus en ligne (décision d'augus) : ce qui marche marche, ce qui manque le
dit en une ligne, et aucun bouton ne finit en erreur de calcul.

- **L'application décide sur ce que le serveur sait calculer** (Blender, modèles d'images,
  estimation de profondeur, lus dans `/api/health` et relus toutes les minutes) : la machine
  d'augus ne change pas, et un poste de calcul qui se connecterait demain rendrait les
  fonctions disponibles sans recharger la page.
- **Sans rendus** : le cadre Rendus dit « Rendus réalistes : bientôt en ligne » ; le chemin
  automatique ne s'enchaîne plus après un plan, un import ou une photo ; « Générer maintenant »
  devient « Générer le plan » tant qu'il n'y a pas de mur, puis disparaît ; la bascule
  « générer au dépôt d'une photo », « Re-rendre » et la vignette 3D d'une variante sont masqués.
- **En ligne sans estimation de profondeur** : une photo déposée est rangée dans Photos, le plan
  ne se construit pas depuis elle, et le cadre dit « Plan depuis photo : bientôt en ligne ».
- **Sans modèle d'images** : image d'inspiration, rendu contextualisé, finition IA et Version
  catalogue sont masqués.
- **Connexion** : après l'envoi du code, « Pas reçu ? Regardez dans vos courriers indésirables. »
  Correction : Brevo transforme le texte en HTML et peut y ajouter un pixel d'ouverture, malgré
  `contactPixelTrackingConsent: false` ; la page Confidentialité « comptes » le dit.
- « Partager » prend le style des autres boutons de la barre.
- Image Docker : l'extra « comptes » est installé ; l'image démarre sans base (mode local ou
  jeton) et avec (`OPENPLAN_DATABASE_URL`), vérifié par un build local. Elle passe de 2,72 à
  1,12 Go : plus de cache de téléchargement d'uv dans l'image, et seuls les dossiers écrits à
  l'exécution (données, poids, matières) appartiennent à l'utilisateur, au lieu d'un
  `chown -R /app` qui recopiait tout dans une couche de plus (le code reste en lecture seule).
- Site, avec l'option de build seulement : les textes ne promettent plus ni rendus réalistes
  ni plan depuis une photo (« Quelques mots suffisent pour commencer », « Voyez-la en 3D »,
  « Rendus réalistes : bientôt ») ; sans l'option, la sortie reste celle de la production.
- Tests : nouveaux e2e « en ligne sans GPU » (sans Blender ni poids) : aucune réponse 409 ou
  5xx, jamais de chemin automatique ni de plan depuis photo lancés (`make e2e` les enchaîne).

### 19 octobre 2026 (B2a, présence en temps réel)

- **Qui est là** (comptes) : dans un projet partagé, les avatars des autres membres présents
  s'affichent dans la barre du haut (initiale et couleur, nom au survol). Plusieurs onglets
  d'une même personne comptent pour une présence. Le nom montré est celui du compte, sinon le
  début de l'adresse avant le @ : jamais l'adresse complète.
- **« Votre nom »** : à la première connexion, une ligne facultative pour le nom que verront les
  autres membres.
- **Ce que les autres changent se voit** : quand un membre modifie le projet ou l'espace de
  travail, les autres onglets ouverts le relisent tout seuls (l'onglet qui a fait le geste
  n'est pas dérangé).
- **Coupure et retour** : si la connexion tombe (redémarrage du serveur, déploiement, réseau),
  un bandeau « Reconnexion… » paraît ; l'application se reconnecte toute seule avec un délai
  croissant et aléatoire, puis relit tout (présence, projet, espace de travail).
- Sécurité : un WebSocket par projet (`/ws/projects/{id}`), qui ne sert qu'à recevoir ; il
  exige la session, l'origine de l'application et l'appartenance au projet ; les écritures
  restent en REST. Un membre retiré du projet (ou un projet supprimé) est déconnecté tout de
  suite et n'entend plus rien ; une session révoquée l'est à la revérification suivante (toutes
  les 30 s). La politique de contenu nomme l'origine `wss://` de l'application quand les
  comptes sont actifs ou l'API exposée (anciens Safari) ; le mode local ne change pas.

### 19 octobre 2026 (stabilité)

- **Le plan ne saute plus pendant qu'on trace des murs** : si la zone du plan changeait de
  taille au milieu d'un tracé (barre d'adresse d'un téléphone, bandeau qui paraît, panneau),
  la vue se recadrait sous le doigt ; le geste qui referme la pièce manquait alors le premier
  sommet et en posait un cinquième. Pendant un tracé, la vue reste en place ; « Recadrer »
  recadre toujours.
- **Un geste n'est plus effacé à l'écran par un résultat arrivé en retard** : quand une
  tâche (construction du plan, chemin automatique) rendait son résultat pendant un geste,
  l'écran pouvait afficher le projet d'avant le geste (le geste restait enregistré, mais ne
  se voyait plus). L'écran ne revient plus jamais à un projet plus ancien que celui affiché.
  Pour que cette règle ne masque jamais un état récent, chaque enregistrement d'un projet
  avance désormais son `updated_at` (strictement croissant, à la microseconde), même un
  réglage ou un résultat de tâche.
- Tests : le journal d'accès du serveur des e2e est gardé, pour avoir une preuve quand un
  test tombe.

### 18 octobre 2026 (contact, et lien rouvert)

- **Un lien d'invitation rouvert par la personne qui l'a déjà accepté ouvre le projet**
  (si elle en est toujours membre), au lieu du message « plus valable ». Pour tout autre, et
  pour un membre retiré depuis, le message unique reste. C'est le serveur qui décide, même
  après un rechargement.
- **Formulaire de contact** sur le site, page `/contact/` et lien « Contact » dans le pied de
  page : adresse e-mail, message (2000 caractères au plus), case « J'accepte que mon adresse
  serve à me répondre ». Le message part par e-mail vers `OPENPLAN_CONTACT_TO` (par défaut
  l'adresse administrateur), en texte seul, sujet « PlanDesign · contact », « Répondre »
  écrivant directement au visiteur ; rien n'est enregistré en base. Anti-spam : champ piège
  caché et 3 envois par IP et par heure, avec toujours la même réponse. L'API
  (`POST /api/contact`) n'accepte que l'origine du site, sans cookies.
- Comme la fenêtre de connexion, le formulaire n'existe qu'avec l'option de build
  `PUBLIC_LOGIN_WINDOW=1` ; avec elle, les mentions légales et la confidentialité renvoient à
  `/contact/`. Sans elle, pas de page `/contact/` : la sortie du site reste identique à la
  production.

### 18 octobre 2026 (B1e, partage)

- **Inviter quelqu'un à un projet** (comptes) : menu « Partager », pour le propriétaire seul.
  Un lien d'invitation par personne, avec un rôle (lecteur, commentateur ou éditeur) et une
  durée (1, 7 ou 30 jours) ; il n'est montré qu'une fois, avec « Copier », et c'est le
  propriétaire qui le transmet (aucun e-mail d'invitation). Le secret est dans l'ancre du lien
  (`#/invitation/…`) : il n'est jamais envoyé au serveur dans une adresse, et la base n'en garde
  que l'empreinte. Un lien sert une fois et se révoque tant qu'il n'a pas servi. Le menu liste
  aussi les membres : changer un rôle, retirer quelqu'un (jamais le dernier propriétaire).
- **L'invité sans compte** voit la page de connexion avec une ligne « Vous avez été invité à
  un projet », sans rien du projet ; le lien lui ouvre l'inscription même « sur invitation ».
  Après le code, il devient membre avec le rôle du lien et le projet s'ouvre. Lien expiré,
  révoqué ou déjà servi : un seul message.
- **Galerie selon le rôle** : « Renommer » à partir d'éditeur, « Supprimer » pour le
  propriétaire, et le rôle affiché sur les projets partagés.
- **Moins de texte** : la mention de connexion tient en une ligne, « Un code vous est envoyé
  par e-mail. Vos données restent en France. », avec un lien « Confidentialité » vers le site ;
  connecté, le pied de l'app dit seulement « Vos données restent en France · Confidentialité ».
  Sur le site, la page Confidentialité « comptes » (code, Brevo, Hostinger, durées) n'est
  produite qu'avec l'option de build ; la sortie par défaut reste celle de la production.
- **`openplan accounts import --owner <email> <dossier>`** : liste les projets de la machine,
  demande confirmation (ou `--yes`, `--only`), copie la scène, les photos, les vignettes et les
  rendus (pas les scènes Blender ni les cartes de profondeur), et donne le projet au
  propriétaire, dont le compte doit exister. Migration `b1e_0003` (table des invitations).

### 18 octobre 2026 (B1d-b, site public)

- **Fenêtre « Se connecter » sur plandesign.fr, derrière une option de build**
  (`PUBLIC_LOGIN_WINDOW=1`, désactivée par défaut) : sans elle, la sortie du site est
  identique octet pour octet à celle en production (405a364), boutons « Ouverture prochaine »
  compris, et `check-dist` refuse tout script, toute mention de l'app et les fichiers de la
  fenêtre. Avec elle : « Se lancer », « Commencer » et « Ouvrir mon espace » ouvrent une
  fenêtre en deux temps (adresse, puis code reçu par e-mail) qui appelle l'API de l'app
  (script externe `login.js`, aucun script en ligne) ; code juste, on part vers l'app, sur
  `/nouveau` si la description est remplie (compositeur prérempli), sinon sur son accueil.
- **Formulaire du site aligné sur l'app** (avec l'option) : description 300 caractères,
  surface 8 à 400 m². Contrat « /nouveau v1 » réactivé dans `apps/site/README.md`.
- Pour l'ouverture (infra) : `connect-src 'self' https://app.plandesign.fr` dans la CSP du
  site, `OPENPLAN_SITE_ORIGIN=https://plandesign.fr` sur l'app.

### 18 octobre 2026 (B1d-a, note 17 § 5 et § 11)

- **Avec les comptes, l'application commence par la connexion** : sans session, la page
  « Se connecter à PlanDesign » et rien d'autre (adresse, puis code à 6 chiffres reçu par
  e-mail), avec la mention de la note 17 § 11 (code e-mail, Brevo, Hostinger en France). Au
  chargement, une seule réponse 401 au plus. Une session perdue en cours de route ramène à
  cette page ; après le code, on revient là où l'on était. Le mode local ne change pas.
- **Arrivée depuis le site (contrat « /nouveau v1 »)** : `app.plandesign.fr/nouveau?v=1&…`
  crée un espace de travail au nom automatique, avec les exigences préremplies (description,
  type de pièce, surface), et l'ouvre **compositeur ouvert et prérempli**. **Rien ne se génère
  tout seul** : il faut un geste, « Générer maintenant » ou le dépôt d'une photo. Les
  paramètres sont revalidés (pièce de la liste, surface de 8 à 400 m², description coupée à
  300 caractères), un paramètre inconnu est ignoré.
- **Rôles à l'écran** : `GET /api/projects/{id}/role`. Le lecteur (et le commentateur, jusqu'à
  B2) ne voit ni outils, ni compositeur, ni « Générer », ni « Nouveau plan », ni assistant ;
  le serveur refuse de toute façon ce que le rôle ne permet pas. La galerie « Vos espaces de
  travail » ne montre que les projets du membre.
- **Réglages** : l'adresse connectée, « Me déconnecter » et « Me déconnecter partout »
  (accueil et paramètres de l'espace de travail). Le bilan de l'installation est réservé aux
  administrateurs.
- **Préparation de la fenêtre du site (B1d-b)** : `OPENPLAN_SITE_ORIGIN` (vide par défaut :
  aucune origine extérieure acceptée, nulle part). Renseignée, cette seule origine peut
  demander et vérifier un code et lire `/api/auth/me`, avec CORS à cookies ; tout le reste lui
  est refusé. `/api/health` dit seulement si les comptes sont actifs.
- **Tests** : nouveaux e2e avec les comptes (`make e2e` les enchaîne). Le mailer `fake` a un
  point de test qui donne le dernier code ; il n'existe qu'avec ce mailer, et l'API refuse de
  démarrer en mailer `fake` hors de la boucle locale.
- **Correctif « Générer maintenant »** : taper son texte puis cliquer tout de suite
  « Générer maintenant » pouvait répondre « Rien à lire pour générer le plan » (le texte
  part au projet quand le champ est quitté, donc par ce même clic, et la génération lisait
  la copie d'avant). Le compositeur passe désormais ses exigences du moment.
- **Backlog** :
  - B1d-b : aligner les limites du formulaire du site sur l'app (description 300 caractères,
    surface 8 à 400 m²).
  - B1e : sur les cartes de la galerie, masquer « Renommer » et « Supprimer » selon le rôle
    (le résumé des projets portera le rôle).
  - B2 : bloquer à l'écran, pour un lecteur, le dépôt de photos et le déplacement des cartes
    sur l'espace de travail (le serveur répond déjà 403).

### 18 octobre 2026 (B1b2, avenant à la note 17)

- **Connexion par code à 6 chiffres**, avec les comptes seulement : l'e-mail ne porte plus de
  lien, seulement un code, « Ce code expire dans 15 minutes. » et « merci de ne pas
  répondre ». La fenêtre « Se connecter » (servie par l'API en attendant le front, B1d,
  `/api/auth/login`, sans script) demande l'adresse, puis le code. Code à usage unique, gardé
  en empreinte salée, comparé en temps constant ; 5 essais faux au plus, puis il est révoqué.
  Code faux, expiré ou déjà servi : un seul message, « ce code n'est pas bon, ou il a
  expiré ». Limites (3 codes par adresse par 15 min, 10 demandes par IP par heure) et réponse
  neutre inchangées. Pour le front : `POST /api/auth/email/verify` (`email`, `code`).
  `/api/auth/email/callback` disparaît. Migration `b1b2_0002` (`openplan accounts migrate`).
- **Envoi par Brevo** (`OPENPLAN_MAILER=smtp_api` + `OPENPLAN_BREVO_API_KEY`, jamais dans le
  dépôt) : API transactionnelle HTTPS, texte seul (pas de HTML, donc ni pixel ni lien
  réécrit), `contactPixelTrackingConsent: false`, aucun en-tête `X-Mailin-*`, expéditeur
  `connexion@plandesign.fr`. Sans clé, l'API refuse de démarrer en `smtp_api` ; `console`
  reste le défaut.

### 18 octobre 2026 (branche comptes, note 17)

- **Base des comptes et couche de stockage (B1a)** : nouvel extra `comptes` (SQLAlchemy 2,
  psycopg 3, Alembic). Les comptes ne s'activent que si `OPENPLAN_DATABASE_URL` est défini :
  sans elle, rien ne change (aucune base, aucun compte, SQLAlchemy jamais importé, projets au
  même endroit). Avec elle : tables `users`, `login_tokens`, `sessions`, `projects`, `members`
  (une migration Alembic, `b1a_0001`), projets rangés sous `<données>/projects/<id>/`, et
  l'API refuse de démarrer sur une base non migrée. `openplan accounts migrate` applique les
  migrations, `openplan accounts status` dit où en est la base (jamais son adresse). Une seule
  couche `Storage` pour les fichiers (disque aujourd'hui, stockage objet possible plus tard) :
  clés relatives sûres, écriture atomique, rien hors du dossier des données, lien symbolique
  compris. Tests sur SQLite et sur un Postgres 16 jetable.
- **Connexion par lien envoyé par e-mail (B1b)**, avec les comptes seulement :
  `POST /api/auth/email/start` envoie un lien de 32 octets aléatoires, valable 15 minutes et
  une seule fois, dont la base ne garde que l'empreinte ; la réponse est la même que l'adresse
  ait un compte ou non, et l'envoi part après la réponse. Ouvrir le lien affiche « Se connecter
  à PlanDesign » avec un bouton : c'est le bouton qui consomme le jeton, pas l'ouverture (les
  aperçus de messagerie ne connectent personne). Lien expiré, déjà servi ou inscription fermée :
  page « Connexion impossible » avec la raison et « Recevoir un nouveau lien ». Inscription
  `OPENPLAN_SIGNUP` sur invitation (défaut) ou ouverte ; `OPENPLAN_ADMIN_EMAIL` devient
  administrateur à sa première connexion, et seule elle. Session : cookie `pd_session`
  HttpOnly, Secure, SameSite=Lax, 30 jours prolongés à l'usage (au plus une fois par heure),
  empreinte en base, révocable (`/api/auth/logout`, « me déconnecter partout »
  `/api/auth/logout-all`, révocation par un administrateur). Toute modification exige
  l'origine de `OPENPLAN_PUBLIC_URL` (en-tête Origin, ou Referer), sinon 403 ; la politique de
  référent devient `same-origin` avec les comptes (avec `no-referrer`, le navigateur enverrait
  `Origin: null`). Limites : 3 liens par adresse par 15 min, 10 demandes par IP par heure (au
  delà, même réponse, rien ne part), 10 tentatives par minute et par IP sur `/api/auth/*`
  (429). Envoi derrière une interface `Mailer` : « console » (le lien va dans le journal du
  serveur), « fake » (boîte en mémoire des tests) et un squelette Brevo par API HTTPS qui
  n'envoie rien (service d'envoi pas encore choisi). Le journal des accès note `user:<id>`,
  jamais l'adresse. Avec les comptes, le jeton Bearer et le cookie de lecture ne servent plus.
- **Droits par projet sur toutes les routes (B1c)**, avec les comptes seulement : chaque route
  `/api` passe par une garde unique qui la cherche dans une table explicite ; une route de
  projet déclare son rôle minimal (`viewer` < `commenter` < `editor` < `owner` : lecture,
  modification du plan, de l'espace de travail et rendus, suppression et confidentialité), les
  autres sont publiques (santé, connexion), réservées aux personnes connectées ou aux
  administrateurs (réglages du serveur, bilan de l'installation). Une route absente des tables
  est refusée. Un non-membre reçoit la même 404 qu'un projet inexistant ; un rôle insuffisant,
  403. La liste des projets ne montre que ceux dont on est membre, la création fait de vous le
  propriétaire, la suppression retire aussi les droits ; changer l'accord pour un modèle
  distant demande le propriétaire. Les photos, rendus, fichiers joints et artefacts passent
  tous par la couche `Storage` (`served_file`), jamais par un chemin statique. Deux dépendances
  pour les routes à venir : `current_user` et `project_member(rôle)`. Un test parcourt toutes
  les routes (routeurs inclus) et échoue s'il en trouve une sans garde, une absente des tables
  ou une entrée de table sans route ; un autre essaie chaque rôle sur un échantillon de routes.
  Sans comptes, la garde ne refuse rien : le mode local ne change pas.

### 4 octobre 2026

- **Charpente apparente (C1)** : sous chaque pente, chevrons (8 × 16 cm, entraxe 60 cm),
  pannes et faîtière en bois massif vieilli (matière CC0 Poly Haven « rough_wood », installée
  avec les autres), dans la vue 3D, le GLB et le rendu ; elle contourne les velux ; la fiche
  de la pente la retire ou règle l'entraxe (45, 60, 80 cm). Aucune règle d'aménagement.
- **Velux vus sur la photo (C2)** : à l'import d'une photo de combles, les fenêtres de toit
  sont détectées (OWLv2 local, « roof window / skylight » en concurrence avec « window » et
  « door », score ≥ 0,30, posées seulement si le pourtour de la boîte est une pente) et posées
  sur leur versant comme velux `estimated`, corrigeables, sans aucun geste. Taille ajustée si
  elle est crédible, sinon 78 × 98 cm. Essai : 2 sur 2 sur la photo de référence et sur notre
  rendu de contrôle, aucune fausse alarme sur les photos d'augus (qui n'ont pas de velux).
- **Focale plus sûre sous les pentes** : une focale déduite des lignes au-delà de 1,6 fois le
  grand côté de l'image (champ de 20°) est ignorée au profit de l'EXIF ou du défaut ; sur des
  photos sans EXIF, les combles sortaient aplatis (31 m de long au lieu de 11).
- **Décor enrichi selon l'ambiance (C3)** : la fiche d'une pièce choisit son ambiance
  (naturel, chalet, scandinave, industriel, contemporain). Les rendus réalistes s'habillent en
  conséquence : coussins et plaid tissé sur les lits, suspension sous le faîtage (rotin, métal
  noir, blanc ou laiton), une ou deux grandes plantes dans les coins libres où la pente laisse
  la hauteur, livres sur les étagères ouvertes, rideaux légers de part et d'autre des fenêtres,
  tirages abstraits encadrés aux couleurs de l'ambiance, tapis à larges rayures contrastées
  (qui survivent à la finition IA), feuillage vert naturel. Habillage de rendu seulement, hors
  des règles ; nouveaux modèles du kit CC0 de Kenney (grande plante, coussin, livres).
- **Cage d'escalier (S1)** : la bibliothèque a une section « Circulation » avec quatre
  escaliers paramétriques (droit, quart tournant, demi-tournant, hélicoïdal), posés d'un clic
  le long du mur proche. L'escalier perce la trémie dans le plancher du niveau où il arrive,
  calcule ses marches par la règle de Blondel depuis la hauteur d'étage (15 marches de 18 cm
  et 27 cm de giron pour 2,70 m) et pose un garde-corps de 1 m sur les côtés ouverts (bois, ou
  métal noir en ambiance industrielle ou contemporaine). Règles : aucun meuble sur la trémie,
  palier d'arrivée dégagé, échappée d'au moins 1,90 m signalée sous une pente ; le générateur
  de dispositions évite la trémie. Symbole normalisé (marches, ligne de foulée fléchée, ligne
  de coupe, garde-corps, « 15 × 18,0 / 27 cm ») dans le plan, le SVG, le PDF et le DXF (calque
  ESCALIERS) ; marches, cage et garde-corps en 3D, GLB et Blender. L'assistant comprend
  « ajoute un escalier droit contre le mur de gauche » ou « mets une trémie avec garde-corps
  au centre ». Format de scène 0.3.0 : l'ancien escalier réservé est migré sans perte.
- **Correctif : mise à jour de l'application installée** : après une mise à jour du serveur,
  l'onglet ouvert gardait l'ancienne version (la section « Circulation » restait invisible).
  Désormais un seul rechargement suffit ; si une nouvelle version arrive pendant que vous
  travaillez, un bandeau « Nouvelle version disponible » propose de recharger (rien n'est
  perdu, chaque modification est déjà enregistrée).

### 12 octobre 2026

- **OpenPlan devient PlanDesign** (décision d'augus) : nouveau nom affiché partout (accueil, titre de l'onglet, application installée, bilan d'installation, exports PDF et SVG, guides). La commande `openplan` reste et gagne l'alias `plandesign` ; le paquet, les variables `OPENPLAN_*` et le dossier des données ne changent pas : rien à refaire pour les projets existants.
- **3D d'un projet vide** : ouvrir la 3D d'un niveau encore vide renvoyait une erreur serveur (scène vide refusée par la bibliothèque 3D) ; c'est maintenant une scène vide.
- **Rendu contextualisé (A1)** : depuis le tableau, une photo ou le rendu d'une vue 3D sert de source (le plan 2D n'est pas un guide d'image : s'il est choisi, une vue 3D rendue est proposée) ; l'intention (style, ambiance, matériaux, contraintes) et le budget (contrainte de matières seulement) donnent un prompt enrichi par le plan : pièce et usage, dimensions avec « ≈ » quand elles sont estimées, portes, fenêtres et radiateurs avec leur côté, lumière naturelle, ce qu'il faut garder. Le prompt est lu par les phrases simples (Gemini seulement si la case est cochée, texte seul), montré avec ce que le plan a apporté, et modifiable avant toute génération. Trois variantes à la même graine, conservatrice (img2img 0,45, contraintes 1,2), créative (0,65 / 1,0) et radicale (0,85 / 0,8), guidées par la profondeur et les arêtes de la source avec la chaîne d'inspiration existante (SD 1.5 local) ; tâche de fond avec une étape par variante. Chaque image porte « proportions estimées » et ses métadonnées (prompt final, source spatiale, horodatage, paramètres) ; les cartes entrent dans un cadre « Moodboard » du tableau, avec le prompt dépliable. Une génération ne modifie jamais le plan ni la 3D. Le tableau envoie aussi un geste en attente quand la page se cache ou se ferme (plus de geste perdu au rechargement). IP-Adapter (références de style) et « à conserver / commenter » suivent (A2, A3).
- **Références d'allure pour le rendu contextualisé (A2)** : sur le tableau, une photo, un rendu, une variante ou une image jointe se coche **Référence** (barre de sélection, badge sur la carte) ; son allure (matières, ambiance) guide alors les variantes, jamais sa géométrie, par l'IP-Adapter SD 1.5 (`h94/IP-Adapter`, Apache-2.0, adaptateur 45 Mo + encodeur d'images 2,53 Go, à télécharger une fois avec `openplan models pull ip_adapter_sd15@1.0`, licence et empreinte affichées, poids hors dépôt). L'adaptateur n'est chargé qu'à une génération qui porte des références et déchargé sinon ; quatre références au plus, poids réglable (0,6 par défaut) ; sans les poids, une génération avec référence est refusée avec la commande à lancer, rien n'est simulé. Les cartes des variantes nomment leurs références. Dans le volet : plus de « Photo · Photo » pour une photo sans nom ; ce que le lecteur a compris s'affiche en mots simples et en français (« chambre · chaleureux · 1 lit double · chêne clair ») ; une traduction française du prompt construit s'affiche en lecture seule au-dessus du prompt anglais, qui reste modifiable.
- **Galerie de tableaux à la place de l'accueil (T5)** : la page d'arrivée est « Vos tableaux » : une carte par projet avec sa vignette (son rendu retenu ou le plus récent, réduit en WebP 480 px et mis en cache ; sinon son plan ; sinon sa première photo ; sinon sa note Exigences), son nom (renommer d'un double-clic ou du crayon), son état en mots (« Aucune photo · pas encore de plan » pour un projet neuf) et sa corbeille. La première carte, « + Nouveau projet », ouvre directement le tableau vide. L'ancien accueil à prompt (texte libre, questions guidées, envoi) est supprimé : tout ce qu'il faisait se fait sur le tableau (note Exigences, cadres Photos et Inspirations, « Ajouter une source » pour un plan DXF ou PDF, « Générer le plan », déclencheur au dépôt d'une photo). Réglages de la galerie : thème, case Gemini (texte seul), état de l'installation ; la mention de confidentialité suit la case. Un projet vide s'ouvre sur son tableau. Limite : l'image d'inspiration de secours quand le plan échoue (accueil A2) n'est plus proposée ; le projet, la photo et la consigne restent, avec un message clair. Limite acceptée par le PO : elle reviendra avec le guide (T6) ou sur le tableau, dans le cadre Inspirations.
- **Suivi des erreurs du serveur en ligne (D4, GlitchTip)** : nouvel extra `monitoring` (sentry-sdk), inclus dans `app`. Il ne s'active qu'avec `OPENPLAN_SENTRY_DSN` ; sans elle, rien n'est importé et rien ne part, donc l'application installée sur une machine n'envoie jamais rien. Quand il est actif, seul part ce qui aide à corriger un plantage : type d'exception, pile d'appels sans variables locales, message filtré et tronqué à 200 caractères, méthode et route de la requête, User-Agent. Un filtre unique retire le corps, les cookies, les autres en-têtes, les paramètres, l'utilisateur, les contextes libres, les journaux et les messages des fils d'Ariane, les chemins personnels et ceux des projets, les noms d'images, les jetons, les identifiants et le texte cité (consignes, noms de projet). `openplan doctor` affiche « Suivi des erreurs : actif, vers <hôte> » ou « inactif ». `openplan sentry-test` envoie une erreur volontaire pour vérifier la réception après un déploiement. Release `openplan@<version>+<SHA>` (OPENPLAN_GIT_SHA), environnement OPENPLAN_ENV, « production » par défaut.
- **Mentions de confidentialité justes, en local comme en ligne (T6)** : la mention de la galerie, de la bande des photos et de « Ajouter une source » dit où sont vraiment les photos et les plans : « restent sur cette machine » en local, « gardés sur le serveur PlanDesign » en ligne (lu dans `/api/health`, qui dit aussi si des rapports d'erreur filtrés partent). La case Gemini ajoute « votre texte part chez Google (Gemini), jamais les photos ». Tant que l'hébergement n'est pas connu, la mention n'affirme rien. Les panneaux d'inspiration, de rendu contextualisé et le lien à copier ne disent plus « sur cette machine » ; plus aucun texte ne parle d'un « onglet Plan ». Guide : parcours zéro geste depuis le tableau, image d'inspiration de secours, nouvelle section « Ce qui part, et où ». Note 17 : pas de vitrine ni de tableau d'exemple pour les anonymes (décision d'augus du 16 octobre), seulement la page de connexion. Mesure à froid sur le tableau (serveur neuf, GPU, 16 octobre) : photo 2, plan en 38 s et trois vues rendues en 130 s sans aucun geste après le dépôt (bascule activée), 124 s avec un geste (« Générer maintenant ») ; vraie photo du grenier, plan en 35 s et deux vues en 71 s (0 geste) ou 66 s (1 geste) : sur ce petit grenier, le chemin automatique ne pose que la vue d'ensemble et le détail du lit. Aucune requête n'est sortie de la machine.
- **Toujours trois vues distinctes** : le chemin automatique pose au moins trois vues qui diffèrent deux à deux d'au moins 35° d'orientation ou de 1 m de position (critère du PO). Une vue trop proche d'une autre est retirée ; quand la vue de zone ou le détail manque (petit volume, une seule zone, rien à cadrer), une vue de secours prend sa place : un **contrechamp** depuis le fond de la pièce, sinon une vue **vers la lumière** (fenêtre ou velux), sinon un **autre angle**, à hauteur d'œil, à 35° au moins de chaque autre vue, jamais face à un mur proche ni avec un meuble coupé au premier plan. Une vue que vous n'avez pas voulue (intention de la pièce) n'est pas remplacée. Un projet qui avait déjà ses trois vues ne change pas. Limites connues (PO, 16 octobre), pour un prochain lot de cadrage : sur la vraie photo du grenier, la vue d'ensemble et le contrechamp montrent surtout la charpente (environ deux tiers de l'image) et presque pas le sol, alors qu'un rendu soigné met l'horizon vers le tiers supérieur avec le sol visible ; et le « lit encastré » se lit comme un lit posé contre le mur de pierre, pas comme un lit encastré.
- **Intégration des comptes (B1a à B1c) dans main** : la branche des comptes rejoint la file d'attente, les plans multiples et le cadrage. Correctifs de l'intégration : la liste des projets ne donne plus le chemin du dossier sur le serveur quand l'API est exposée ou que les comptes sont actifs ; l'état et l'annulation d'une tâche (`GET` et `DELETE /projects/{id}/jobs/{job_id}`) ne répondent que pour une tâche de ce projet (sinon 404, rien n'en est dit) ; l'annulation d'un calcul en attente entre dans la table des droits (membre éditeur). L'extra « comptes » (SQLAlchemy, Alembic, psycopg) est désormais installé durablement : une seule liste d'extras sert `make setup` et chaque commande du Makefile (`make start`, `make check`…), qui l'ajoute sans rien retirer ; les tests des comptes échouent s'il manque, au lieu d'être ignorés (seuls les tests Postgres restent conditionnés à OPENPLAN_TEST_DATABASE_URL). Le mode local n'importe toujours ni SQLAlchemy ni Alembic.
- **Correctif du contrechamp sous les combles (18 octobre)** : sur le vrai grenier, la caméra du contrechamp tombait dans la charpente apparente (chevrons et pannes pendent de 16 à 38 cm sous la pente), que la mesure du cadrage ignorait : un grand plan de bois flou bouchait l'image. La mesure compte maintenant l'habillage réel que le rendu bâtit (chevrons, pannes, faîtière, et le décor debout : plantes, lampes, suspension, rideaux, livres) et garde, pour chaque rayon, la distance de la première surface touchée. Une place de caméra est écartée si plus de 10 % de l'image est occupée par une surface à moins de 1,20 m, quelle qu'elle soit (pente, mur, panne, meuble, décor) ; si toutes l'étaient, la moins bouchée resterait. Une vue large baisse aussi l'objectif tant que le sol fait moins de 15 % ; le détail du lit encastré passe à 24 mm quand aucune place libre ne cadre tout à 28 mm, et, trop proche d'une autre vue, il cherche une autre place au lieu d'être remplacé. Sur le vrai grenier : vue d'ensemble et détail inchangés, contrechamp déplacé, premier plan dégagé.
- **Cadrage sous les combles mesuré sans rendu** (18 octobre) : une mesure géométrique (`openplan.geom.framing`, sans GPU ni Blender) lance un rayon par case d'une grille posée sur l'image et donne la part de toit (pentes et plafond), de sol, de murs et de meubles, et la hauteur de l'horizon. Sous une pente, les vues larges (vue d'ensemble, contrechamp, vers la lumière, autre angle) se posent à 1,30 m au lieu de 1,50 m, là où la caméra tient sous la pente avec 25 cm de marge, objectif baissé pour mettre l'horizon à 36 % du haut de l'image (vers le tiers supérieur), puis encore baissé jusqu'à 28 % si le toit dépasse 40 % de l'image ; parmi les places les mieux notées, celle qui laisse le moins de toit au-delà de 40 % l'emporte. La vue d'architecte qui tient lieu de vue d'ensemble ne se pose plus au-dessus d'un meuble (le lit encastré contre le pignon) : elle avance dans l'axe, depuis le bout le plus proche d'une place libre. Le détail du lit encastré cadre le lit entier et le rampant au-dessus de l'oreiller (toit entre 12 et 40 % de l'image), de trois quarts. Une pièce seule n'a plus de vue de zone autour d'un lit encastré que le détail cadre déjà. Sans pente, rien ne change. Mesure sur le vrai grenier (sans rendu) : vue d'ensemble toit 62 % → 35 %, sol 3 % → 29 %, horizon 58 % → 36 % ; contrechamp toit 57 % → 36 %, sol 2 % → 28 %. Les rendus de vérification restent à faire sur un créneau GPU.
- **Compositeur flottant et nouvelle disposition du tableau (T7b, maquette d'augus)** : la note Exigences quitte le tableau. Vos exigences se donnent dans un **compositeur** qui flotte en bas : onglets **Prompt libre** et **Questions guidées** (mêmes champs qu'avant), case Gemini (texte seul), ligne « compris », case « Générer automatiquement au dépôt d'une photo » et **Générer maintenant**, qui étaient dans la barre du bas. Le × le referme : plus rien de vos exigences sur le tableau, la pastille **Générer** le rouvre ; il est ouvert d'office tant qu'aucun rendu n'existe. Le contenu reste dans le projet, lu par le chemin automatique comme avant. Le tableau se range comme la maquette : Photos de la pièce puis Inspirations à gauche, le plan au centre, les Rendus à droite ; sur un tableau existant, la note s'en va et la colonne de gauche remonte à sa place. Le cadrage de départ laisse la place du compositeur.
- **« Espace de travail » au lieu de « tableau »** (consigne d'augus) : dans tout le texte visible de l'application (galerie « Vos espaces de travail », en-têtes, messages, libellés accessibles), dans les messages de l'API et dans le guide. Le code, les routes et les fichiers ne changent pas. Un test échoue désormais si « tableau » réapparaît dans le texte visible du front, les messages de l'API ou le guide ; deux exceptions, où le mot désigne une peinture (objets de décor lus dans une consigne, raison de rejet d'une ouverture). Au passage : « tableau côte à côte » de l'aménagement devient « comparaison côte à côte », et le « tableau d'erreurs » de `openplan evaluate` « erreurs mesurées, pièce par pièce ».
- **La carte graphique se libère toute seule** : les modèles locaux (Stable Diffusion et ses ControlNet, l'IP-Adapter, la profondeur, le détecteur d'ouvertures) sont relâchés après 10 minutes sans calcul et le cache CUDA est vidé ; le calcul suivant les recharge sans rien demander (la profondeur en une demi-seconde). Mesuré sur la machine : 4 928 Mo chargés, 124 Mo après libération, contexte CUDA compris. Le délai se règle par `OPENPLAN_GPU_IDLE_S` (0 : jamais) ; jamais pendant un calcul.
- **Import dans la barre de gauche et barre de la sélection en bas (T7c, maquette d'augus)** : dans l'espace de travail, « Ajouter une source » quitte la barre du haut ; le bouton **Importer** de la colonne d'outils propose « Photo ou plan de la pièce » (photos, DXF, PDF, SVG ou image : le plan se construit) et « Fichiers dans l'espace de travail » (images d'ambiance, vidéos, documents). Dans le plan et les résultats, le bouton du haut reste. Les actions de la sélection (modifier le plan, rendu contextualisé, référence, dupliquer, verrouiller, ordonner, supprimer) ne s'affichent qu'avec une sélection, dans une barre en bas au centre, au-dessus du compositeur : elle ne couvre plus ce qu'on a sélectionné.
- **Rendus dans l'espace de travail, panneau fermé, cadres sobres (T7d1, retours d'augus)** : un projet qui a des rendus s'ouvre sur l'espace de travail, cadré sur le cadre **Rendus** ; le chemin automatique y arrive aussi. L'en-tête du cadre Rendus porte « Re-rendre n vues périmées » (absent jusqu'ici de l'espace de travail) et **Réglages et galerie**, qui ouvre le volet **Rendus** du panneau : « Vos rendus » et le respect de la demande, vues automatiques, points de vue, qualité et éclairage, « Rendre toutes les vues », galerie. La vue 3D cachée des vignettes n'est montée que volet ouvert. Le **panneau de droite** est fermé au départ et ne s'ouvre jamais tout seul (icône du haut ou action explicite) ; le **nom et la hauteur du niveau** passent dans la barre du bas du plan. Les **cadres vides** disent une ligne courte (« Déposez vos photos », « Déposez vos images d'ambiance », « Les rendus apparaîtront ici », le plan vide n'a que « Tracer moi-même » et « Générer le plan »), l'explication passe en infobulle ; leurs textes se règlent sur la largeur du cadre et rien ne déborde plus, même navigateur zoomé à 150 % (test ajouté). L'écran de résultats a ensuite laissé place à la visionneuse (T7d2). Au passage : la bande des rendus se pose au-dessus de la barre du bas du plan, à droite ; nouvelle route `GET /api/jobs/running` (la tâche lourde en cours : sa sorte et depuis quand) ; les e2e qui lancent des calculs attendent que le serveur ait fini avant le test suivant, pour qu'un échec ne fasse plus refuser le lancement d'à côté.
- **Visionneuse plein écran à la place de l'écran de résultats (T7d2, plan validé)** : les rendus s'ouvrent en grand, un par un, par-dessus l'espace de travail ou le plan : **Voir en grand** dans l'en-tête du cadre Rendus ou dans la barre de la sélection, un double-clic sur un rendu, une vignette de la bande sous le plan. Chaque vue garde tout ce que portait sa carte (périmée, favori, A / B / C, version catalogue, « Affiner cette vue », désignation d'un meuble au clic, téléchargement) ; ← → ou les flèches passent d'une vue à l'autre, Échap ferme (d'abord la carte de désignation si elle est ouverte) ; l'en-tête refait les vues périmées. L'image occupe la largeur : la place des commentaires de la vue (B2, travail à plusieurs) est prévue dans le code mais rien n'en est visible avant B2, et le lien « agrandir » de l'image n'y figure pas (redondant en plein écran). Affiner, déplacer, demander à l'assistant ferment la visionneuse ; retirer ou remplacer un meuble se voit sur place. Les boutons **Ouvrir l'éditeur →** et **← Résultats** disparaissent ; l'espace affiché s'appelle « Espace de travail ». Mesure : ouvrir la visionneuse est compté à part (`viewer_opens`), ce n'est pas un geste ; `results_editor_trips` reste, toujours à 0 pour les projets récents. Au passage : le double-clic sur un élément déplaçable de l'espace de travail (un rendu) n'arrivait jamais, la capture du pointeur l'avalait ; il est reconnu à l'appui.
- **Plusieurs plans par espace de travail (T7e, schéma 0.15.0)** : **+ Nouveau plan**, en haut de l'espace de travail, ouvre une petite fenêtre à deux champs : un nom libre et une sorte au choix, **Variante de ce plan** (une copie du plan ouvert, autre version du même lieu), **Nouvel étage** (un plan vide posé au-dessus du plus haut étage) ou **Plan vide** (une variante vide du plan ouvert, à tracer). Le plan créé s'ouvre ; la barre du haut dit « Plan · nom » quand le projet en a plusieurs. Sur l'espace de travail, chaque plan a sa rangée : son cadre « Plan · nom » et, à sa droite, son cadre « Rendus · nom » ; Photos et Inspirations restent communes. « Modifier le plan » ouvre le plan de ce cadre, qui devient le plan ouvert : c'est lui que visent l'édition, les vues et le chemin automatique ; les boutons d'un cadre Rendus (re-rendre ses vues périmées, Voir en grand, Réglages et galerie) agissent sur son plan. Créer un plan est un seul pas d'historique : Ctrl+Z le retire, avec sa rangée. Une variante ne mélange rien : ses vues et ses rendus sont à elle (identifiants neufs), retoucher une variante ne périme pas les rendus de l'original ni l'inverse, et la finition IA d'un rendu suit l'ambiance de son plan. Schéma : nouveau champ `Level.variant_of` (l'étage dont un plan est la variante ; migration sans perte, tout niveau d'avant est un étage) ; chaque rendu note désormais son plan (`level_id`, retrouvé par la caméra pour les rendus d'avant) ; nouvelle route `POST /projects/{id}/levels`. Un projet à un seul plan ne change pas (même cadre « Rendus », même parcours). Limite connue : la 3D et les exports montrent un plan à la fois, comme avant ; l'empilement des étages en 3D reste à faire.
- **Accent à comparer et en-têtes des cadres sur une ligne (T7a)** : Réglages (l'engrenage de l'accueil, ou Paramètres avancés dans l'atelier) propose, sous le thème, l'**Accent** : **Indigo** (par défaut, inchangé tant qu'augus n'a pas tranché), **Violet** (celui de la maquette du 16 octobre, #A100FF) ou **Provence** (terre cuite). Un clic suffit pour comparer, en clair comme en sombre ; le choix est retenu par ce navigateur. Les boutons colorés gardent un texte blanc lisible (au moins 5:1 en clair ; en sombre, le même compromis que l'indigo). L'en-tête d'un cadre tient sur une ligne : un nom long (« Rendus · Variante bureau ») se termine par des points de suspension, en entier dans l'infobulle, et « Réglages et galerie » devient une icône (son nom dans l'infobulle et pour les lecteurs d'écran).
- **Importer droit dans les cadres Photos et Inspirations (T7a, demande d'augus)** : un cadre vide a un bouton **Importer** sous sa ligne ; un cadre rempli, un petit **+** dans son en-tête (« Ajouter des photos », « Ajouter des images d'ambiance »). Le sélecteur ne propose que ce que le cadre attend : photos et plans (images, PDF, DXF, SVG) pour Photos, images pour Inspirations. L'effet est celui d'un dépôt dans ce cadre, sans geste de plus (la mesure le confirme) : une photo entre dans le projet et la bascule « générer au dépôt d'une photo » s'applique ; une image d'ambiance arrive cochée Référence ; un plan mis dans Photos (bouton ou dépôt) se lit comme un plan, comme « Importer › Photo ou plan de la pièce ». L'import de la barre de gauche reste. Au passage : ce qu'on dépose dans un cadre se pose sous son en-tête (il couvrait le titre depuis T7b).
- **File d'attente des calculs lourds** : une photo ou un plan déposé pendant un calcul (chemin automatique, rendu, finition, inspiration, d'un projet ou d'un autre) n'est plus refusé avec « une tâche lourde à la fois » : le calcul attend son tour et démarre seul dès que celui en cours se termine, dans l'ordre d'arrivée, puis la suite s'enchaîne comme d'habitude (plan, chemin automatique, rendus) : la mise en attente n'est pas un geste. Le cadre Rendus du plan ouvert dit « En attente : démarre dès que le calcul en cours se termine », avec **Annuler** ; le cadre du plan dit « En attente ». Déposer à nouveau sur le même plan pendant l'attente n'empile pas un second chemin automatique (ni une seconde construction du plan, ni un second « Rendre toutes les vues ») : la nouvelle demande remplace celle qui attend, à sa place, car elle porte l'intention la plus récente (dernière photo, dernière consigne) ; un calcul déjà lancé, lui, n'est jamais remplacé. API : état `queued` avec `queue_position` (1 = le prochain), `cancelled`, `elapsed_s` à 0 pendant l'attente ; `DELETE /projects/{id}/jobs/{job_id}` annule une tâche en attente (409 si elle a démarré) ; `GET /api/jobs/running` ajoute `queued` (le nombre de vos tâches en attente) quand il y en a. Le refus 429 ne reste qu'au-delà de 8 tâches en attente par utilisateur. **Limite connue : les tâches en attente sont perdues au redémarrage du serveur** (le registre est en mémoire, comme avant) ; les rendus déjà faits restent. Préparé pour les postes de calcul (lot B3) : chaque tâche porte son projet, son utilisateur, sa sorte et sa clé de doublon, et les états sont ceux de la future table `jobs`. Une tâche restée en attente relit le projet quand elle démarre (rendu d'une vue, « Rendre toutes les vues », « Re-rendre », finition, version catalogue, image d'inspiration, rendu contextualisé, vignette de variante) : ce qui a changé pendant l'attente compte, pas l'état lu au clic.
- **API exposée : plus de chemin du serveur dans les erreurs** (contrôle de sécurité D2) : quand un calcul n'est pas possible sur le serveur (poids absents, pas de GPU), l'API exposée répond « Calcul indisponible sur ce serveur : nécessite un poste de calcul GPU. » au lieu d'un message citant ses dossiers ; les autres messages d'erreur (409, erreurs des tâches de fond, avis de l'analyse d'une photo) gardent leur sens, chemins masqués. Le message complet reste dans le journal du serveur ; sur la boucle locale, rien ne change (la commande à lancer reste dite).
- **Déclencheur sur le tableau (T4)** : tant qu'aucun rendu n'existe, une barre en bas du tableau porte la bascule « Générer automatiquement au dépôt d'une photo » (activée par défaut, réglage du projet, aussi dans Paramètres avancés) et le bouton « Générer maintenant ». Bascule active : une photo déposée sur le tableau d'un projet sans plan construit le plan puis enchaîne le chemin automatique, sans autre geste. Coupée : la photo est gardée, et « Générer maintenant » lance le plan (ou, s'il existe, le chemin automatique). Mesure : chaque « Générer » cliqué compte pour un geste (`generate_clicks`) ; le chemin reste à 0 geste avec la bascule, 1 sans. Le cadre « Rendus » est désormais là dès le départ, à droite du plan. La poignée de redimensionnement d'un élément garde au moins 14 px à l'écran en vue d'ensemble (elle devenait introuvable). Format de scène 0.14.0.
- **Cadre Plan vide : tracer ou générer (T3)** : tant que le plan n'a pas de mur, son cadre sur le tableau propose « ✎ Tracer moi-même » (entre dans le plan avec ses outils) et « Générer le plan » : d'après la première photo de la pièce (plan estimé), sinon d'après la note Exigences (logement décrit → plan proposé, surface seule → plan de départ), puis le chemin automatique s'il est actif ; la progression (construction du plan, puis chemin automatique) s'affiche dans le cadre. Sans photo ni exigences, l'écran dit quoi faire. Retouches : le badge « Référence » repasse en haut à gauche (il recouvrait le nom de l'image) ; la barre d'actions de la sélection ne recouvre plus le titre d'un cadre.
- **Cadres « Photos de la pièce » et « Inspirations » (T2)** : les deux cadres sont là dès l'ouverture d'un tableau, avec leur consigne tant qu'ils sont vides. Une image déposée dans **Inspirations** est jointe au projet, montrée sur le tableau et cochée **Référence** (son allure guidera les rendus contextualisés) ; une photo déposée dans **Photos de la pièce** (ou ailleurs) entre dans le projet et fait le plan. Un fichier déposé dans un cadre y prend sa place (le cadre grandit d'une rangée s'il est plein). Les images jointes par l'API se montrent désormais sur le tableau au lieu d'une carte « fichier ». Un cadre fixe retiré à la main ne revient pas.
- **Note « Exigences » sur le tableau (T1, chantier « arrivée sur le tableau », principe d'augus : un projet = un tableau)** : chaque tableau porte une note Exigences, une seule, qui reprend les questions guidées de l'accueil (espace, surface, chambres, style, format, lumière, ambiance, meubles voulus, à éviter), le texte libre, la case « Mieux comprendre mon texte avec Gemini » (texte seul) et la ligne « compris : … » (même lecteur). Son contenu vit dans le projet (réponses et consigne gardées, format de scène 0.13.0) : le chemin automatique les lit au prochain plan et les efface une fois appliquées ; « Nouvelle variante » envoie le texte à l'assistant comme avant. L'ancienne note de la demande devient cette note, à sa place. Note d'architecture 15 (maquette validée, correspondance avec l'accueil, lots T1–T6).
- **API exposée : l'atelier entier marche derrière le jeton (cookie de lecture)** : hors boucle locale, les images, téléchargements, exports et le GLB du viewer recevaient 401 (le navigateur ne met pas d'en-tête sur un `<img>` ou un lien). `POST /api/session` pose désormais un cookie de lecture HttpOnly, Secure, SameSite=Lax, limité à `/api`, 12 h, dont la valeur est un HMAC du jeton (jamais le jeton) ; il n'est accepté que pour GET/HEAD, jamais sur les routes GET qui écrivent, et toute modification exige toujours le Bearer. Le front le pose à l'arrivée et à l'invite du jeton, le renouvelle toutes les 6 h ; `DELETE /api/session` l'efface. Repli : photos et rendus du tableau passent par fetch + blob avec l'en-tête quand un jeton est connu. Boucle locale inchangée. Note d'architecture 16 (modèle de menace).
- **Rendu contextualisé : 640 px (A4b)** : à 768 px avec les références (IP-Adapter), la mémoire de la carte débordait et une variante prenait sept minutes au lieu de vingt secondes ; les variantes sont produites à 640 px de grand côté.
- **plandesign.fr : serveur préparé (B0, 15 octobre)** : note 14 validée par augus (option « VPS + postes de calcul », pas de SMTP, Caddy, durcissement) ; VPS durci (pare-feu 22/80/443, fail2ban, SSH par clé seule), Caddy avec certificat Let's Encrypt et page « bientôt », sauvegardes nocturnes. Aucun compte ni API en ligne pour l'instant : les projets restent sur la machine ; rien ne change dans l'application.
- **Rendu contextualisé depuis une photo : l'aménagement plutôt que le chantier (A4)** : sur une photo de pièce vide, les variantes reproduisaient la photo presque à l'identique (objets, câbles, gravats) et la référence n'apportait qu'une teinte. Désormais, depuis une photo, la source proposée par défaut est la **vue 3D rendue de l'aménagement** (recommandée ; la photo telle quelle reste une option « restyler la pièce ») ; sur la photo brute, la génération est guidée par la **profondeur seule** (plus d'arêtes), avec des forces plus hautes et des contraintes plus basses pour la créative (0,75 · 0,6) et la radicale (0,92 · 0,4), pour que les meubles de l'intention apparaissent ; la radicale écoute davantage les références (+0,2, soit 0,8 par défaut). Le prompt est compté en **jetons** (le modèle d'images coupe à 77 sans prévenir ; la limite précédente était en mots et la fin du prompt, dont le texte de l'utilisateur, sautait) : le texte de l'utilisateur vient juste après la pièce et le style, la géométrie ensuite, et le volet affiche « environ N jetons sur 77 » avec un avertissement si une modification déborde. Les cartes disent ce qui a tenu la géométrie (« profondeur » ou « profondeur + arêtes »).
- **Trier les variantes du moodboard (A3)** : sur chaque carte, **À conserver** (étoile et contour, variante retenue), **Commenter** (remarque attachée à la carte, enregistrée avec le tableau, 2 000 caractères au plus) et **Dupliquer**. Rien d'autre ne change : ni le plan, ni la 3D, ni l'image.
- **Vidéos, PDF et liens sur le tableau (C1)** : sans aucune requête vers l'extérieur. Une vidéo mp4 ou webm (reconnue à ses premiers octets, 200 Mo au plus) se lit sur le tableau ; un PDF montre la vignette de sa première page, rendue sur la machine (pdfplumber, déjà présent, qui s'appuie sur pypdfium2, BSD-3 / Apache-2.0), et s'ouvre dans un nouvel onglet ; un PDF illisible reste une carte fichier. Outil **Lien** (L) ou adresse collée : une carte avec le domaine et le titre saisi, http(s) seulement (refusé sinon, par l'écran et par le serveur), ouverte au clic avec `noopener` ; la page n'est jamais récupérée. Un fichier joint par l'API arrive de lui-même dans un cadre « Fichiers ». La vignette du plan en thème sombre est désormais l'export SVG aux couleurs sombres de l'éditeur (`exports/svg?theme=dark`), plus un filtre d'inversion.
- **Indicateur d'enregistrement et mesures du parcours (B4)** : la barre du haut dit l'état réel de l'enregistrement de tout l'atelier, gestes sur le plan comme sur le tableau : « Enregistrement… » pendant l'envoi, « Enregistré » à la réponse, « Échec de l'enregistrement » (avec « réessayer » pour le tableau) si le serveur ne répond pas ; un geste refusé par les règles du plan n'est pas un échec d'enregistrement. Le cadrage automatique du tableau à l'ouverture n'est plus enregistré : l'indicateur reste sur « Enregistré » sans geste. Le texte des notes garde au moins 12 px à l'écran à tout zoom. Mesures du parcours (`/metrics`, journal local) : en plus du total des passages, les allers-retours résultats ↔ éditeur, les entrées dans le plan et les changements de vue du plan sont comptés à part.
- **Plus d'onglets : résultats, éditeur, plan (B3)** : l'atelier a trois espaces et un seul bouton pour passer de l'un à l'autre : « Ouvrir l'éditeur → » sur l'écran de résultats, « ← Résultats » sur le tableau (l'éditeur), « ← Tableau » dans le plan. Le plan s'ouvre depuis son cadre du tableau (double-clic ou « Modifier le plan ») : la vue zoome en continu dans le cadre, puis le plan s'édite en grand avec ses outils ; la 3D est dans sa barre des vues (Plan, Plan + 3D, 3D) ; « ← Tableau » rend le tableau à la même position et au même zoom. Sans rendu, un projet s'ouvre sur l'éditeur (sur le plan s'il n'a encore aucun mur, pour le tracer). Retouches du tableau : la vignette du plan suit le thème sombre ; la barre d'actions se pose au-dessus de la sélection (en dessous si elle sortirait de l'écran) ; titres et boutons des cadres gardent une taille lisible à tout zoom.
- **Le tableau à l'écran (B2)** : nouvel espace « Tableau » dans l'atelier (provisoirement à côté de Rendus, Plan et 3D, qui partiront avec B3). Photos, note de la demande, rendus, plan et fichiers joints y sont posés ; on les déplace, les redimensionne, les sélectionne à plusieurs (Maj + clic ou rectangle), les range dans des cadres nommés, les duplique, les verrouille, les met devant ou derrière, les supprime (sauf le plan). Notes (N), cadres (F), main (H) ; molette pour défiler, Ctrl + molette pour zoomer, Ctrl+0 pour tout voir ; import par glisser-déposer, collage ou bouton (une photo entre dans le projet, un autre fichier est joint). Double-clic : modifier le plan, retrouver un rendu en grand. « Nouvelle variante » de la note de la demande passe par l'assistant. Annuler et rétablir (Ctrl+Z, Ctrl+Maj+Z) agissent sur l'espace actif : sur le tableau, les gestes du tableau seulement, l'historique du plan n'est pas touché. L'indicateur dit l'état réel de l'enregistrement ; un enregistrement fait ailleurs entre-temps (autre onglet) recharge le tableau et le dit. Guide : « Le tableau du projet ».
- **Tableau du projet, côté serveur (B1)** : le projet a désormais un tableau (photos, note de la demande, rendus, images d'inspiration sans plan, plan, cadres, fichiers joints), enregistré dans `board.json` à côté de `project.json` : une sauvegarde de la disposition ne peut pas écraser un geste sur le plan, et redimensionner le cadre du plan ne change jamais ses cotes. La première ouverture pose tout de lui-même ; une photo ou un rendu nouveau entre ensuite dans son cadre, un rendu refait prend la place de l'ancien, ce que l'on a retiré ne revient pas. Chaque sauvegarde porte une révision : un envoi parti d'une révision périmée (deux onglets) est refusé au lieu d'écraser l'autre. Fichiers joints (50 Mo au plus) gardés dans `media/` ; seules les images courantes sont montrées, tout autre fichier (SVG compris) part en pièce jointe, jamais interprété. Nouvel export « Archive (.zip) » : sauvegarde complète (plan et historique, tableau, photos, fichiers joints, images des rendus). L'écran du tableau arrive avec B2.
- **Statut du plan (A3)** : un plan reconstruit d'après des photos porte « Estimé — à vérifier » tant qu'aucune cote n'a été saisie ni recalibrée, un plan proposé d'après un texte « Proposé — à ajuster » ; un plan relevé (DXF, PDF, SVG) n'en porte pas. Le statut s'affiche dans le plan, avec ce que veut dire « ≈ » (cote estimée ; sans « ≈ » : saisie ou mesurée) et « Recalibrer », et en tête des rendus avec « Modifier le plan ».
- **Plan et rendus à états indépendants (A2)** : un plan qui échoue depuis l'accueil ne fait plus rien perdre. Le projet et sa photo restent (la consigne est gardée), une image d'inspiration locale guidée par la photo sert de rendu de secours, marquée « inspiration, sans plan » (agrandir, télécharger), et « Réessayer le plan » relit la même photo dans le même projet au lieu de le supprimer pour en recréer un. Des rendus qui échouent après un plan réussi n'effacent plus le plan : le projet s'ouvre avec le plan et dit l'échec (« relancez Rendre toutes les vues »). Un plan non dessiné depuis un texte garde le projet et la consigne. Les remarques de l'accueil (fichier ignoré, échec) s'affichent à l'ouverture de l'atelier au lieu d'être perdues.
- **Mentions de confidentialité exactes (A1)** : le pied de l'accueil ne dit plus « tout reste sur cette machine » quand la case Gemini est cochée (« photos et plans restent sur cette machine ; votre texte part chez Google (Gemini) »). Dans l'atelier, « Ajouter une source » et le « + » de la bande de photos disent que les photos peuvent partir chez le modèle distant quand le projet l'a autorisé dans Paramètres avancés › IA sans « texte seul » ; sinon, rien ne change.
- **Coin repas et coin détente composés** : la table et ses chaises forment maintenant un groupe (table au mur avec les chaises en face et en bout ; à partir de quatre chaises, table écartée du mur avec une chaise côté mur), comme le lit et ses chevets ou le canapé et sa table basse. Un groupe qui ne tient pas dans sa bande de la pièce (porte-fenêtre, lit voisin) essaie toute la pièce plutôt que d'être défait : dans le studio, la table basse n'est plus à l'autre bout de la pièce et les quatre chaises ne sont plus alignées devant le canapé. Une chaise qui ne trouve pas sa place autour de la table est omise (le respect de la demande le dit) plutôt que posée seule.
- **« ajoute une plante »** : un objet de décor sans meuble au catalogue (plante, fleurs, cadre, tableau, coussins, plaid, rideaux, livres, bougies, vase…) monte d'un cran la densité du décor de la pièce (sobre → normal → riche) : « ✓ décor plus fourni : plantes et objets ajoutés (au prochain rendu) » ; au plus fourni, l'assistant le dit au lieu de « pas compris ». Le tapis et la lampe restent des meubles posés sur le plan. Le champ de l'assistant passe sur deux lignes, sur toute la largeur du volet (Entrée envoie, Maj+Entrée va à la ligne).
- **Une seule boîte pour écrire (P5c)** : le volet Aménager perd son champ « Votre souhait » (une phrase s'écrit à l'assistant, qui montre les dispositions dans le volet) et garde le brief par zones et la composition à la main (catégories du catalogue). La consigne gardée de l'accueil devient un tour en attente dans l'assistant, qui s'ouvre de lui-même (« Appliquer à la pièce », « oublier ») ; la consigne de l'accueil appliquée par le chemin automatique est le premier tour du fil. Une proposition que l'assistant ne comprend pas est dite (✗ avec la raison), jamais passée sous silence. « propose… avec un bureau » ne recopie plus le bureau en envie d'ambiance. L'ancienne route `/assistant` est retirée (tout passe par `/assistant/say`). Champs de saisie libre dans l'atelier : 4 → 1 (plus la consigne experte de l'image d'inspiration, dans Paramètres avancés).
- **Assistant unifié dans les trois onglets (P5b)** : un bouton **Assistant** dans la barre du haut (et la touche « / ») l'ouvre dans Rendus, Plan et 3D. Une seule boîte pour l'ambiance et les gestes : chaque tour montre ses parties (✓ fait, ✗ refusé avec la raison, ? question), qui a lu la phrase (phrases simples ou Gemini) et « Annuler » pour tout le tour. **À propos de** suit la pièce ou le meuble choisi ; la carte d'un meuble désigné dans un rendu gagne **Demander…**. La fiche Ambiance de la pièce se déplie sous la conversation. Le fil est relu du journal local (20 derniers tours) ; journal coupé, une ligne dit qu'il ne vit que le temps de la page. La fiche d'une pièce n'a plus de champ « Consigne » : elle rappelle la dernière consigne et ouvre l'assistant sur cette pièce. Dans l'assistant, un meuble nommé sans verbe (« sans tapis », « deux lits ») reste une envie de l'ambiance ; seul « ajoute… », « mets… », « il me faut… » pose un meuble.
- **Assistant unifié, côté serveur (P5a)** : `POST /projects/{id}/levels/{lid}/assistant/say` lit une seule phrase avec les deux lecteurs : l'ambiance (« plus chaleureux », « lumière du soir ») va à la pièce en contexte, les gestes (« ajoute un bureau », « enlève le tapis ») au plan. Chaque partie dit fait, refusé ou question avec sa raison ; un échec partiel garde ce qui a réussi ; le tour donne le nombre de pas à défaire pour l'annuler en entier. Les phrases simples lisent un geste par proposition (« lumière du soir, et enlève l'armoire »). Le modèle distant ne lit la phrase que si la case Gemini de l'accueil est cochée. `GET /projects/{id}/assistant/thread` relit les 20 derniers tours du journal local (vide si le journal est coupé). L'atelier s'en sert au lot suivant.
- **Libellés du plan** : en thème clair, les meubles ronds (lampadaire, table ronde) n'apparaissent plus remplis de noir et les noms des points de vue reprennent leur bleu (deux couleurs du thème s'étaient perdues avec l'arrivée du thème sombre) ; le nom d'un meuble plus large que lui passe sous son symbole ; le nom d'un point de vue évite meubles, escaliers, noms de pièce et cotes.
- **Mesure de la désignation (P4c)** : chaque clic sur un rendu est noté au journal local (ce qui a été trouvé : un élément et sa sorte, du décor sans porteur, ou rien ; jamais la position du clic). `openplan journal metrics` et `/metrics` donnent le nombre de clics, leur issue et le taux de résolution (cible : au moins 80 % de clics qui désignent un élément).
- **Désigner un meuble dans le rendu (P4b)** : dans l'onglet Rendus, un clic sur une image entoure ce qui s'y voit (contour jaune tiré de la carte des éléments) et ouvre une petite carte près du clic : pour un meuble, **Déplacer** (le plan, ce meuble choisi), **Remplacer** (les modèles de la même famille ; même place, dos contre le mur, un seul pas annulable), **Affiner** (la 3D depuis la caméra du rendu, ce meuble choisi) et **Retirer** (annulable ; la vue devient périmée) ; pour un mur, le sol ou le toit, **Ambiance et matières** ; un décor posé par l'ambiance (plante, tapis…) est dit comme tel. Échap ou un clic à côté ferme la carte ; « agrandir » ouvre l'image.
- **Carte des éléments des rendus (P4a)** : chaque rendu d'une vue garde, à côté de l'image, une carte qui dit quel élément du projet se voit à chaque pixel (une passe Blender d'environ 1 s, quelques dizaines de Ko). Le linge, la lampe de chevet et les livres y désignent le meuble qu'ils habillent ; plantes, cadres, tapis, rideaux et suspensions restent du décor. Un rendu plus ancien la reçoit à la demande depuis sa scène Blender. Route `GET /projects/{id}/renders/{run}/pick?x=&y=` (coordonnées relatives, valables pour l'image Blender, sa finition et l'image d'inspiration).
- **« Affiner cette vue » (P3)** : sous chaque image de l'onglet Rendus, un bouton ouvre l'onglet 3D depuis la caméra de ce rendu, la pièce regardée choisie et sa fiche ouverte (ambiance, consigne, zones) ; après la retouche, la bande propose de re-rendre la vue.
- **Bande de rendus et « Re-rendre » (P2)** : en bas, dans les trois onglets, le dernier rendu de chaque vue en vignette ; un point signale une vue périmée et **Re-rendre les n vues** (aussi dans la ligne d'en-tête de Rendus) les refait en un clic sans changer d'onglet : chaque vue comme son dernier rendu (qualité, finition, image d'inspiration), la lumière des vues automatiques suivant l'ambiance du moment. Une vignette ouvre Rendus sur sa vue. Une vue n'est périmée que si la géométrie, les meubles, les matières ou l'ambiance ont changé ; déplacer un point de vue ne périme que le sien, et les réglages du projet aucun. Dans Rendus, les touches 1 à 4 ouvrent l'onglet 3D au point de vue demandé.
- **Onglets Rendus · Plan · 3D (P1)** : la barre « 1 Importer · 2 Éditer · 3 Rendre » devient trois onglets d'un même projet, sans numéros. Un projet qui a des rendus s'ouvre sur **Rendus** : le dernier rendu de chaque vue en grand au centre, les points de vue, le rendu et la galerie à droite ; sinon sur **Plan**. **3D** montre la maquette seule ; l'onglet Plan garde « Plan » et « Plan + 3D ». « Importer » n'est plus une étape : l'accueil, et le bouton **Ajouter une source** (photos, plan DXF/PDF/SVG, avec la correction des calques et de l'échelle papier). Une vue dont le plan a changé depuis son rendu est marquée **périmée**.
- **Mesure du parcours (P0)** : l'atelier note dans le journal local du projet les changements d'étape ou de vue, les clics « Affiner » et les rendus demandés (un mot d'un vocabulaire fermé, jamais envoyé ; le réglage « Journal local » les coupe aussi). `openplan journal metrics <projet|--all>` donne par projet le temps jusqu'au premier rendu, les gestes avant et après, ces compteurs et les vues dont le rendu est périmé.

### 10 octobre 2026

- **Densité et composition des combles (chantier H, lot H5)** :
  - Les combles demandent une rangée de lits encastrés de 90 (un pour 3 m de jambette, 2 à 4), toujours tête sous le rampant, avec 40 cm de passage entre deux lits ; autant qu'il en tient, et le lit classique seulement si aucun ne tient.
  - Couette plus gonflée, avec un retour arrondi au bord et des plis plus marqués à la tête.
  - Tapis de jute sur le côté du lit quand le pied n'a pas de place.
  - Vues : la vue d'ensemble évite les meubles coupés par les bords gauche et droit du cadre ; les vues de zone et de détail évitent un meuble étranger au premier plan.
- **Heure dorée (chantier H, lot H4)** :
  - Nouvel éclairage « heure dorée », distinct du soir qui ne change pas : soleil bas et chaud (16°, 3 000 K) aux ombres adoucies, ciel réchauffé, appoint intérieur chaud.
  - L'ambiance « heure dorée » (fiche, consigne, questions guidées) le choisit enfin, au lieu du soir ; il est aussi proposé dans « Éclairage » du panneau Rendre.
  - L'exposition automatique reste maîtresse : l'image se réchauffe sans s'assombrir.
- **Textiles (chantier H, lot H3)** :
  - Le plaid tissé n'est plus une plaque : posé de biais au pied du lit, il retombe souplement sur les côtés, avec des plis légers et des franges.
  - Le lit encastré reçoit une couette de lin souple qui déborde du matelas.
  - Un tapis au pied de chaque lit quand la place est libre : jute tressée pour les ambiances naturel, chalet et scandinave, laine unie pour les autres.
- **Velux à ébrasement (chantier H, lot H2)** :
  - Chaque velux a son caisson sous la toiture, comme dans les combles de référence : linteau horizontal en haut, appui vertical en bas, joues perpendiculaires à la pente, sur la profondeur des chevrons. Il est en planches pour les ambiances naturel et chalet, en enduit clair sinon.
  - Le soleil se tourne vers la pente qui porte le plus de velux, sans s'écarter de plus de 60° de l'heure choisie (sa hauteur et sa couleur ne changent pas), pour qu'une flaque de jour tombe dans la pièce.
- **Retouches H1b** :
  - Vue de détail du lit encastré de trois quarts, depuis le côté, à 2,5 m et 1,20 m de haut (28 mm) : on lit le coffre, ses tiroirs et la pente au-dessus de la tête.
  - Vue d'ensemble : plus de meuble coupé au premier plan (on avance d'un pas plutôt que de garder un fauteuil devant l'objectif), et le lit encastré cherché dans le cadre.
  - Vignette de bibliothèque du lit encastré, rendue depuis sa propre géométrie (blender/scripts/built_in_bed_preview.py).
- **Accueil, un seul point d'import (US1)** :
  - Un bouton « Importer un plan ou des photos » dans la carte de l'accueil.
  - Le glisser-déposer marche sur toute la carte, avec surbrillance.
  - Les formats sont dans une infobulle et une ligne d'aide.
  - « Commencer par un plan vide » devient un lien qui crée un projet au nom automatique.
- **Galerie des rendus (US2)** :
  - Tous les rendus du projet, rangés par vue : ensemble, zones, détails, à la main, photos,
    variantes.
  - Favoris par image, avec un filtre ; renommer ; supprimer avec « Annuler » (corbeille
    douce).
  - Télécharger une image, ou les favoris en zip.
  - Comparer deux images, côte à côte ou au curseur avant/après.
  - Galerie agrandie ; les favoris passent en premier sur « Vos rendus ».
- **Lit encastré sous pente (chantier H, lot H1, note 13)** :
  - Nouveau meuble paramétrique « Lit encastré sous pente » (carte « Couchage ») : coffre en bois à tiroirs sur les deux longs côtés, matelas à 0,55 m, planche de tête ; couchage de 90, 140 ou 160 cm au choix dans la fiche. Géométrie calculée (aucun modèle tiers) ; symbole 2D dans le plan, le SVG et le PDF.
  - Règle : 0,90 m libres au-dessus du matelas, à l'oreiller. Une pose trop près de la jambette est refusée, et une remarque le signale si le plan change.
  - Le générateur l'utilise dans les combles, tête sous le rampant, pied vers le faîtage ; s'il ne tient pas, le lit classique et ses chevets reviennent.
  - L'assistant comprend « ajoute un lit encastré sous la pente ». Vue de détail « lit encastré » et linge (oreillers, plaid) posés sur son matelas.

### 9 octobre 2026

- **Retouches L3b** :
  - « Respect de votre demande » n'est jamais vide : un plan généré affiche le type
    (« ✓ Type : studio ») et la surface (« ✓ Surface : 30 m² — plan 31,7 m² ») ; sans
    demande précise, « Aucune demande particulière : ambiance déduite ».
  - Studio : le coin nuit est au fond, à l'opposé de l'entrée, avec 80 cm de passage
    jusqu'au coin détente ; si le lit ne tient pas ainsi, il est posé sans cet écart.

### 8 octobre 2026

- **Plan généré depuis un programme (L3)** : sans fichier, un logement décrit (« un
  appartement de 60 m², deux chambres, une cuisine ouverte et un coin bureau », ou les
  questions guidées) donne un plan à plusieurs pièces.
  - Le programme (type, surface, chambres, cuisine ouverte ou fermée, coin ou pièce bureau,
    salles de bain, WC séparés, cellier, dressing) est lu par des règles fixes, hors ligne.
    Il est lu par Gemini si la case de l'accueil est cochée ; les réponses guidées priment,
    et ce qui est supposé est dit.
  - Découpage récursif :
    - le séjour d'un côté ; de l'autre, un dégagement de 1 m qui dessert chaque pièce,
      toutes en façade ;
    - cloisons de 7 cm, portes de 83 cm (73 cm pour les pièces d'eau), une fenêtre par
      pièce ;
    - surfaces nettes tenues au pour cent près du studio au T4 ;
    - un seul pas d'historique, attribué au pipeline.
  - Un WC ou un cellier trop larges gardent leur surface ; le fond devient un placard, avec
    une cloison et une porte pliante sur la pièce voisine.
  - Bandeau « Plan généré depuis votre programme » : ce n'est pas un relevé. Il donne aussi
    ce qui a été supposé et, au-delà de 90 m², « plan simplifié, une seule circulation ».
  - Une pièce seule avec sa surface garde le plan de départ simplifié (L1b).
- **Chemin automatique sur plusieurs pièces** :
  - L'étape Ambiance déduit le séjour (ou applique la consigne) ; les autres pièces
    habitables prennent le même style.
  - La consigne ne retype pas le séjour : « cuisine ouverte » n'en fait pas une cuisine.
  - Chaque pièce est meublée par son brief ; le séjour reçoit les zones cuisine et repas
    (nouvelles), le studio un coin nuit.
  - Le coin bureau va au séjour s'il a la place, sinon à la chambre principale.
  - Pièce serrée (moins de 10 m² par zone) : pas de meubles « en plus ».
  - Trois vues :
    - l'ensemble du séjour ;
    - la chambre principale ;
    - le détail de la zone la plus demandée (bureau, cuisine, lit).
    Une vue au plus par pièce au-delà du séjour.
  - Nom automatique : « Appartement T3 », « Studio », « Maison ».
  - Schéma 0.12.0 (zones `cuisine`, `repas`).
- **Respect de la demande** :
  - De vraies chambres : « 2 chambres, un lit dans chacune ».
  - Les coins demandés : « Zone : coin bureau — dans chambre 1 ».
  - Les accords sont écrits en toutes lettres (« 1 lit posé sur 2 », « 2 posés malgré
    tout »), jamais « (s) ».

### 7 octobre 2026

- **Questions guidées (L2)** :
  - L'accueil a deux onglets : « Texte libre » et « Questions guidées ». Les questions sont :
    quel espace, surface, chambres, style, puis vos envies et contraintes en texte libre.
  - « Plus d'options » (replié) : format des images, lumière, ambiance (deux au plus),
    meubles voulus et à éviter.
  - Les réponses sont des choix de l'utilisateur (`user_defined`) et priment sur le texte.
    La ligne « Compris » les reprend.
  - Une surface donnée sans fichier pose le plan de départ.
  - Avec une photo d'une seule pièce, un écart de plus de 15 % recale le plan (facteur
    √(S donnée / S estimée), borné entre 0,6 et 1,6) en un pas attribué au pipeline,
    annulable d'un clic.
  - Avant L3 et ses pièces multiples, N chambres donnent une zone couchage et un lit par
    chambre dans la même pièce.
  - **Respect de votre demande**, à l'arrivée sur « Vos rendus » : chaque demande est
    reprise (style, ambiance, surface, chambres, meubles voulus et à éviter, format,
    lumière) avec ✓ si elle est tenue, sinon la raison : « cheminée : pas au catalogue »,
    « 1 lit posé sur 2 : place insuffisante », « 2 coins nuit dans une seule pièce ».
- **Case « Mieux comprendre mon texte avec Gemini »** (décision d'augus, 17:46) :
  - Elle se trouve sous la zone de texte et n'apparaît que si une clé est configurée.
  - Elle est décochée par défaut ; le choix est retenu pour la machine.
  - Cochée, la ligne « Compris » est faite par Gemini, une pause après la frappe et au plus
    un appel toutes les 4,5 s. Elle dit qui a lu le texte. Si Gemini échoue (quota,
    réseau), les phrases simples prennent le relais et la ligne le dit.
  - Les projets créés depuis l'accueil reçoivent l'accord, texte seul : la passerelle
    refuse toute requête avec image (`privacy.remote_text_only`, schéma 0.11.0).
  - Le journal réseau ne contient jamais le texte.
  - Une infobulle prévient qu'en palier gratuit, Google peut utiliser le texte.

- **Accueil sans fichier (L1b)** :
  - Un texte seul s'envoie. S'il donne une surface (« un appartement de 60 m² »), une pièce
    rectangulaire 4:3 de cette surface est posée, murs `generated`, marquée « plan de départ
    simplifié » (bandeau dans l'atelier), puis le chemin automatique continue comme avec une
    photo.
  - Sans surface, le projet s'ouvre dans Éditer avec la consigne gardée : un bandeau la
    rappelle ; dès qu'une pièce est fermée, « Appliquer à la pièce » l'applique, « oublier »
    l'efface ; le chemin automatique la reprend aussi. Schéma 0.10.0 (`pending_instruction`).
  - La ligne « Compris » parle français, dans l'ordre du texte : « grenier · chaleureux ·
    lin · 2 lits simples ».
  - Les anciens projets au nom de photo prennent leur nom automatique dès l'affichage de la
    liste (« Projet · 25 août »), sans passer par l'historique ; un nom donné à la main
    n'est jamais touché.

### 6 octobre 2026

- **Accueil façon assistant (L1a)** :
  - À gauche, « Vos projets » : petite vignette, nom, renommer (double-clic ou crayon),
    supprimer ; « + Nouveau projet » ; Réglages (thème, état de l'installation).
  - Au centre, « Que voulez-vous concevoir ou planifier ? » : on écrit ce que l'on veut,
    on joint une photo ou un plan (plusieurs photos acceptées), puis on envoie. La ligne
    « Compris : … » dit avant l'envoi ce que la consigne donnera (lu sur cette machine).
  - Après l'envoi, le formulaire reste visible, grisé, et un fil de progression suit le
    chemin (« Création de votre plan… », « Aménagement… », « Rendu des vues (2/3)… », temps
    restant), puis l'atelier s'ouvre sur « Vos rendus ». En cas d'échec, un message clair
    et le formulaire de nouveau éditable.
  - La consigne devient l'ambiance de la pièce (champs choisis par vous) à la première
    étape du chemin automatique.
  - Les projets se nomment seuls d'après la pièce et la date (« Grenier · 6 oct. ») ; un
    nom donné à la main ne change jamais ; un ancien nom de photo (« WhatsApp Image… »)
    est remplacé à l'ouverture. Schéma 0.9.0 (`name_auto`), lu sans perte depuis 0.8.0.
  - « Commencer par un plan vide » reste sous la zone de dépôt.
- **Version catalogue et bandeau lisible (V1.7b)** :
  - Le bandeau « Inspiration · cotes non garanties » inscrit sur l'image fait désormais au
    moins 1/25 de sa hauteur, sur fond sombre semi-opaque.
  - À l'arrivée, un bouton « Version catalogue » par vue refait l'image librement (allure la
    plus « catalogue ») ; elle est marquée « Inspiration · matières libres », s'ajoute aux
    autres images (bascule A / B / Catalogue) et n'est jamais lancée par le chemin
    automatique.
- **Deux images par vue : la référence et l'inspiration (V1, sortie B)** : le chemin
  automatique rend désormais, pour chaque vue, l'image de référence A (rendu Blender et sa
  finition légère, force fixée à 0,35, cotes tenues) et une image d'inspiration B, refaite par
  le modèle d'images local à partir du rendu (img2img à 0,8, guidé par la profondeur et les
  arêtes à 1,2) : allure de photo d'intérieur, matières et meubles tenus.
  - B est marquée « Inspiration · cotes non garanties » sur l'image elle-même et dans ses
    métadonnées (`generated`) ; l'image Blender et la finition restent à côté.
  - À l'arrivée, B est montrée d'abord, avec une bascule A / B par vue ; la galerie montre
    les trois images.
  - Paramètres avancés › Réglages › « Image d'inspiration par vue » la coupe.
  - Schéma 0.8.0 (`render.auto_inspiration`), lu sans perte depuis 0.7.0.
- **Journal local des demandes (V2)** : chaque consigne, chaque phrase à l'assistant et chaque
  chemin automatique est noté avec sa traduction, ainsi que ce que vous en faites ensuite
  (variante gardée, ambiance corrigée, meuble déplacé ou retiré, Annuler), chaque geste
  rattaché à la demande d'origine.
  - Dans le dossier du projet (`journal/design.jsonl`), jamais envoyé ; aucune photo, aucune
    image, aucune coordonnée, aucun nom de fichier (une ligne qui en contiendrait n'est pas
    écrite).
  - Actif par défaut ; Paramètres avancés › Réglages › « Journal local » le coupe en un clic
    et « Vider le journal » l'efface.
  - Export : `openplan journal export <projet|--all> > demandes.jsonl`.
  - Schéma 0.7.0 (`privacy.design_journal`), lu sans perte depuis 0.6.0.
- **Temps restant juste** : le bandeau du chemin automatique annonce un temps restant tiré des
  durées réellement mesurées sur cette machine (moyenne des cinq dernières par sorte d'étape :
  rendu, finition, aménagement), recalculé en direct ; il n'affiche jamais « 0 s » tant
  qu'une étape tourne (« encore quelques secondes » en fin d'étape, plus si elle déborde).
- **Chemin « zéro geste » (V1, point 6)** : déposez une photo ou un plan, et sans autre
  geste le plan est construit, puis l'ambiance est déduite (naturel), la pièce aménagée par
  zones, les trois vues posées, rendues et finies (finition IA).
  - Pendant le calcul, une progression par étape avec le temps restant ; l'éditeur reste
    utilisable, et vos gestes faits entre-temps sont gardés.
  - À l'arrivée, l'écran Rendre montre vos rendus en premier, avec « Affiner le plan »,
    « Changer l'ambiance », « Modifier les vues », et ce qui a été fait, étape par étape.
  - Tout s'annule (Ctrl+Z) ; rien de ce que vous avez choisi n'est réécrit : une ambiance
    choisie est gardée, des meubles déjà posés ne sont pas remplacés, une vue retouchée reste.
  - La finition enchaînée garde toujours l'image Blender à côté ; elle se coupe dans
    Paramètres avancés › Réglages (« Finition IA enchaînée »), comme le chemin entier. Sans
    le modèle d'images installé, elle est sautée et c'est dit.
  - Schéma 0.6.0 (réglages `render`), lu sans perte depuis 0.5.0.

### 5 octobre 2026

- **Consigne libre → ambiance (V1, point 5)** : dans la fiche « Ambiance » d'une pièce, une
  zone « Consigne » (« grenier chaleureux, bois et lin, deux lits »).
  - Traduite par le modèle configuré (texte seul ; accord du projet pour un modèle distant),
    sinon par des phrases simples hors ligne : style, humeur, couleurs, matières par surface
    (sol, murs, pignons, pentes, charpente), densité du décor, lumière, type et usage de la
    pièce, meubles voulus ou à éviter, zones, format 4:5.
  - Rien n'est deviné : ce qui n'est pas au catalogue (« cheminée ») est dit ; une ou deux
    couleurs sont complétées par la palette du style, et c'est dit ; ce que le modèle propose
    d'invalide est écarté et dit.
  - Appliquée en un geste annulable, champs « choisi » ; la consigne reste dans la fiche.
  - Quinze consignes de référence testées.
  - La finition IA suit l'ambiance par défaut : son prompt vient du style, de l'humeur, des
    matières, des couleurs, de la lumière et de la densité du décor (« selon l'ambiance »).
- **Vues automatiques, retouches (V1, point 4 b)** :
  - Le détail cadre toute la tête du lit et le chevet, en 4:3 comme en 4:5 : la caméra recule
    jusqu'à ce qu'ils tiennent dans le cadre.
  - Chaque meuble posé par le brief garde sa zone (couchage, lecture…) ; la vue de zone la lit.
  - Portes : les vantaux suivent le style (chêne clair, noyer, bois peint blanc, chêne foncé) ;
    un seuil ferme le pied des portes et des passages (un liseré de ciel bleu s'y voyait) ; les
    vantaux doubles et pliants se recouvrent au lieu de laisser un jour.
- **Trois vues automatiques (V1, point 4)** : dans Rendre, « Poser les 3 vues ».
  - Vue d'ensemble depuis l'arrivée dans la pièce (haut de l'escalier, sinon la porte), à
    hauteur d'œil, grand angle, tournée vers la plus grande profondeur visible ; un mur
    proche ou un lampadaire devant l'objectif sont évités. Sans escalier ni porte : dans
    l'axe de la pièce.
  - Vue de zone : la zone meublée principale (détente, lecture, bureau, sinon couchage), à
    3 m environ, à 1,20 m de haut, 26 mm, de trois quarts.
  - Détail : lit et chevet (sinon fauteuil et lampadaire, bureau et chaise…), à 2 m, 35 mm,
    du côté du chevet.
  - Chaque vue est vérifiée : dans la pièce, sous la pente avec de la marge, hors des meubles
    et de la trémie, sans mur dans le cadre. Une vue impossible est dite, avec la raison.
  - Format 4:3 paysage ou 4:5 portrait, gardé dans l'ambiance de la pièce.
  - Reposer les vues remplace les vues automatiques d'avant, sauf celles déplacées ou visées
    à la main. Annulable d'un geste.
  - « Rendre toutes les vues » : les vues automatiques puis vos autres points de vue, l'un
    après l'autre en une tâche ; la galerie se remplit au fil des rendus.
  - Correctifs : la caméra des vignettes de variantes reste sous la pente quand l'égout
    descend au sol ; le garde-corps suit le style (bois peint blanc en scandinave, métal noir
    en industriel) ; les planches des pentes suivent la pente, comme un lambris, au lieu du
    zigzag.
- **Ambiance → matières (V1, point 3)** :
  - Le style de la pièce choisit la matière de chaque surface : sol, murs, pignons, pentes,
    charpente (brute, blanchie ou sombre).
  - Il choisit aussi le bois des meubles (chêne clair, noyer, bois peint blanc, chêne foncé)
    et la teinte du tissu et du métal. Le tissu prend la première couleur de la palette si
    elle est dite.
  - Ordre de priorité : la matière choisie à la main sur un élément, puis celle voulue par
    l'ambiance pour la surface, puis le style, puis celle posée par une étape automatique,
    puis le défaut. Une pièce sans ambiance garde ses matières d'avant.
  - Quatre matières Poly Haven CC0 de plus, à installer avec `openplan materials pull` :
    chaux (painted_plaster_wall), béton de sol (concrete_floor_worn_001), chêne foncé
    (dark_wooden_planks), bois peint blanc (distressed_painted_planks désaturé et éclairci).
  - Aménagement : les velux comptent comme lumière pour la lecture et le bureau. Un tapis
    trop grand se réduit dans la plage du catalogue avant d'être omis.
- **Zones composées (V1, point 2 b)** : une zone est une composition, pas une liste.
  - Gabarits cotés, posés d'un bloc le long d'un mur ou d'une cloison de la baie :
    - couchage : lit tête au mur, deux chevets accolés, tapis au pied ;
    - détente : canapé, table basse à 0,45 m sur le tapis, fauteuil en angle tourné vers la
      table, lampadaire derrière le bras ;
    - lecture : fauteuil, lampadaire, petite table ;
    - bureau : bureau, chaise devant, bibliothèque à côté.
  - Un meuble d'accompagnement essaie l'autre côté du groupe s'il ne tient pas. Un tapis sans
    place dans sa composition est omis plutôt que posé seul.
  - Un seul meuble principal de chaque sorte par zone ; l'appoint se fait en rangements.
  - Les meubles qui vont ensemble (table basse et canapé, chevet et lit, chaise et bureau) ne
    déclenchent plus de remarque de dégagement.
  - La densité visée se compte sur la surface à plus de 1,40 m, jusqu'à 35 meubles.
- **Brief par zones (V1, point 2)** :
  - « Brief par zones » dans Aménager, sans modèle : la pièce se meuble par zones (couchage,
    lecture, bureau, rangement, détente), chacune avec son kit de meubles du catalogue.
  - Les zones viennent de l'ambiance, sinon du type de pièce et de sa surface (une par 18 m²,
    quatre au plus). La densité visée est de 0,35 meuble/m² (5 à 25 meubles).
  - Les zones se répartissent par baies le long du faîtage, sinon du grand axe : chaque meuble
    cherche d'abord sa place dans sa baie, ailleurs seulement si rien n'y tient.
  - Sous pente, lits, chevets, commodes et rangements bas vont dans la bande basse. Contre
    un mur d'égout sans jambette, ils reculent jusqu'à tenir sous la pente.
  - Sur les combles d'augus (95 m², ses murs intérieurs et son escalier) : 25 meubles placés
    au lieu de 7, variante valide, en 20 s.
- **Intention de design de la pièce (V1, point 1)**, schéma 0.5.0 :
  - Chaque pièce porte une intention qui dit ce qu'elle doit devenir, jamais où poser les
    choses : type et usage, style, humeur (deux au plus), palette nommée, matières par
    surface, densité du décor, lumière (moment et chaleur), meubles voulus et à éviter, zones,
    vues automatiques et leur format.
  - L'ambiance d'un projet 0.4.0 devient son style, marqué « choisi », sans perte.
  - Fiche « Ambiance » dans la fiche de la pièce : une phrase de résumé, puis chaque champ
    marqué « choisi » ou « déduit ».
  - Ce que l'utilisateur choisit est prioritaire : une étape automatique ne réécrit jamais un
    champ choisi (règle tenue par l'opération `set_intent`).
  - Un passage sans menuiserie reçoit un couloir neutre de l'autre côté, pour qu'un rendu n'y
    voie plus le ciel.
- **Tracé cranté (E1)** : en traçant un mur, une cote (Recalibrer) ou en déplaçant un sommet,
  le point s'accroche comme dans SketchUp : extrémités et milieux des murs, angle par crans
  (0, 45, 90° et pas de 15°) depuis le point de départ, parallèles et perpendiculaires aux
  murs existants, pied de perpendiculaire, prolongements, alignements sur un sommet. Lignes
  guides en pointillé et étiquette près du curseur (« Extrémité », « Milieu »,
  « Perpendiculaire », « 90° »…). Maj : tracé libre (avant, Maj forçait l'orthogonal).
- **Redimensionner sur le plan (E2)** : le meuble sélectionné (tapis compris) a huit poignées
  (côtés et coins) ; les tirer change largeur et profondeur, bornées aux plages du catalogue,
  avec la cote affichée et les règles vérifiées en direct (le meuble rougit, rien n'est
  enregistré si une règle dure est touchée) ; Alt redimensionne depuis le centre. L'escalier
  a deux poignées pour sa largeur (0,70 à 1,20 m), une ouverture deux poignées à ses bouts
  pour sa largeur le long du mur. Chaque redimensionnement s'annule.
- **Coupe et transparence dans l'aperçu 3D (E3, partie vue)** : dans la barre de l'aperçu,
  « Coupe » horizontale ou verticale (axe X ou Y), déplacée au curseur, avec un repère
  translucide et les faces coupées en rouge ; « Maison de poupée » masque toit, charpente,
  plafonds et les murs tournés vers vous, recalculés quand on tourne autour ; « Rayons X »
  rend tout semi-transparent. Ces réglages sont mémorisés par projet.
- **Coupe en rendu et en PDF (E3, fin)** : la coupe active de l'aperçu s'applique aussi au
  rendu Blender (même plan, faces coupées bouchées ; en coupe verticale, la moitié où se
  trouve le point de vue est retirée), et la galerie note « rendu en coupe ». En coupe
  verticale, « PDF de la coupe » exporte la coupe cotée à l'échelle (structure coupée en
  gris, meubles au trait, hauteur au faîtage ou au plafond, largeurs hors tout et
  intérieure, cartouche) : `GET /projects/{id}/exports/section-pdf?level_id&axis&value`.
- **Escalier déplacé à la souris, portes vers la pièce (E5c)** :
  - Escalier : on le glisse sur le plan. Son flanc s'accroche à un mur parallèle à sa montée
    (à moins de 35 cm) ; ailleurs, il se cale sur la grille ; Alt le laisse libre. Les règles
    de trémie sont vérifiées pendant le geste. Au lâcher, un refus est expliqué (« Non
    déplacé : … »). Le déplacement s'annule.
  - Porte posée (outil ou bibliothèque) : elle s'ouvre vers la pièce, quel que soit le sens de
    tracé du mur. Dans la fiche et les messages, « intérieur » veut dire « vers la pièce »,
    d'après le côté du mur où se trouve le sol de la pièce.
- **Menuiseries en 3D (E5b)** : chaque ouverture reçoit chambranle, vantaux, vitrages et rail
  (prismes `joinery`). C'est la même géométrie dans l'aperçu 3D du navigateur et dans le
  rendu Blender, qui n'a plus son habillage à part :
  - double porte en deux vantaux ;
  - pliante en quatre panneaux ;
  - porte-fenêtre et baie coulissante en vantaux vitrés, ceux de la baie décalés dans
    l'épaisseur ;
  - coulissante en applique : vantail contre la face du mur et rail au-dessus de la baie et
    du tronçon où il s'efface.
  Le chambranle reste dans l'épaisseur du mur.
- **Bibliothèque d'ouvertures (E5a)**, schéma 0.4.0 (un projet 0.3.0 se lit tel quel) :
  - Nouvelles sortes : double porte (vantaux égaux ou 1/3–2/3), porte coulissante en applique,
    porte à galandage, porte pliante, porte-fenêtre (un ou deux vantaux), baie vitrée
    coulissante.
  - Dans le volet Meuble, une section « Portes et fenêtres » présente des cartes à vignettes
    dessinées. Un clic sur un mur pose l'ouverture à ses cotes types (0,83 / 1,40 / 2,40 m…),
    et le fantôme suit le mur. Les ouvertures se redimensionnent (E2) et se retournent (E4).
  - Les symboles en plan sont normalisés : arc par vantail battant, rail et flèche pour une
    coulissante, poche en tireté pour un galandage, zigzag pour une pliante, vitrage en bleu.
    Ils sont identiques dans le plan, en SVG, en PDF et en DXF.
  - Règles :
    - débattement de chaque vantail ;
    - bande que parcourt le vantail d'une coulissante en applique, qui doit rester libre
      (`sliding_zone_blocked`, dure) ;
    - place pour le vantail ouvert d'une coulissante ou d'un galandage (`slide_no_room`).
  - L'assistant comprend « ajoute une double porte sur le mur du fond », « mets une porte
    coulissante », « une baie vitrée », « une porte-fenêtre double »… Il ouvre la porte vers
    la pièce.
- **Perspective de coupe (E3b)** : en coupe verticale, « Vue de coupe » (écran Rendre) ajoute
  un point de vue perpendiculaire au plan, face à la tranche, reculé du côté retiré juste
  assez pour cadrer toute la coupe, à mi-hauteur. Le rendu en coupe verticale a un fond gris
  clair uni à la place du ciel (la lumière du ciel ne change pas). Les faces coupées sont en
  poché gris très foncé, au rendu comme dans l'aperçu 3D (au lieu du rouge). Route
  `GET /projects/{id}/levels/{lid}/cameras/section?axis&value`.
- **Tourner sur le plan (E4)** : le meuble ou le tapis sélectionné porte un rond de rotation
  au bout d'une tige, au-delà de sa face avant ; en le glissant, l'angle s'affiche en direct,
  avec des crans de 15° où 90° est prioritaire (Maj : libre). L'escalier sélectionné a le
  même rond (crans de 90°, Alt : 15°, Maj : libre) : son dessin et son sens de montée tournent
  en direct. La trémie est vérifiée pendant le geste : dans la pièce, hors des murs (deux
  nouvelles règles dures, `stairs_outside_room` et `stairs_on_wall`), sans meuble dedans,
  arrivée dégagée, échappée sous pente. Au lâcher, un refus est expliqué. La porte
  sélectionnée a un rond dans son débattement : un clic change ses gonds puis son sens, G les
  gonds, O le sens. Le débattement se redessine et un meuble dans le nouveau débattement
  refuse le retournement (en rouge). Élargir un escalier par sa poignée (E2) déplace la rive
  tirée, l'autre reste en place, et une trémie qui entrerait dans un mur est refusée avec
  la raison. Tout s'annule. Route
  `POST /projects/{id}/levels/{lid}/elements/{eid}/check`.

### 3 octobre 2026

- **Finition IA (D5)** : sous chaque rendu de la galerie, « Finition IA » (jamais
  automatique) retouche l'image par un modèle local, à force bornée (légère 0,35 par défaut,
  0,55 au plus) et dans un style au choix, guidé par la profondeur et les arêtes du rendu
  pour ne rien déplacer ; l'image Blender reste à côté, l'image finie est marquée « générée ».
  ControlNet « arêtes » (OpenRAIL-M) : `openplan models pull controlnet_canny_sd15@1.1
  --accept-license`, facultatif.
- Retouches du rendu : plus de filet de ciel entre pente et plancher quand l'égout est bas
  (chaque pente se prolonge sous l'égout et une semelle ferme le dessous du plancher) ;
  tapis en jute plus sombre, à trame croisée visible.
- **Décor et meubles habillés (D4)** : deux tapis au catalogue, traversables (on pose les
  meubles dessus, aucune règle de chevauchement) ; au rendu, les meubles Kenney prennent de
  vraies matières (bois, linge de lit en lin, jute des tapis, métal, abat-jour lumineux) et
  la pièce est habillée : lampe allumée sur chaque chevet, petite plante sur les commodes,
  cadre au mur au-dessus d'un lit ou d'un canapé adossé (si la pente le permet).
- **Correctif combles (longueur)** : quand le pignon du fond n'est pas reconnu comme un plan
  net (petit, encombré, à contre-jour), sa distance est prise aux points les plus lointains
  dans l'axe du faîtage au lieu de supposer 5 m ; sur les photos du grenier d'augus, 8 m au
  lieu de 5, la caméra au bout du grenier et non plus au milieu.
- **Lumière du jour et vue d'architecte (D3)** : dans un rendu, quand la pièce a des
  fenêtres ou des velux, le jour prime : les lampes d'appoint ne font plus qu'un faible
  remplissage et l'exposition compense une pièce peu vitrée ; « auto » devient le réglage
  par défaut (lumière du jour si la pièce a des ouvertures). Rendre propose une « Vue
  d'architecte » : frontale, au centre de la pièce, dans l'axe de sa longueur, grand angle.
- Retouches : la matière du **sol** se choisit dans la fiche de la pièce ; l'enduit (murets
  d'égout compris) est rendu clair par défaut ; la carte Recalibrer montre la valeur actuelle
  seule dans ses champs (plus de texte tronqué).
- **Matières de rendu CC0 (D2)** : `openplan materials pull --all` installe à la demande, hors
  du dépôt, sept matières photo (enduit, parquet clair, parquet ancien, lambris de chêne,
  pierre, moellons, béton) et un ciel HDRI de Poly Haven (CC0, empreintes vérifiées,
  ≈ 14 Mo) ; le rendu Blender s'en sert, et garde sinon ses matières dessinées ; la matière
  se choisit dans la fiche d'un mur ou d'une pente ; `doctor` dit ce qui est installé.
- **Velux (D1)** : une pente reçoit des fenêtres de toit (« Ajouter un velux » dans sa
  fiche, 78 × 98 cm par défaut, position et cotes réglables) ; la toiture est percée, avec
  cadre et vitrage, et la lumière du ciel entre dans la vue 3D et le rendu Blender ; le plan,
  le SVG et le PDF montrent leur emprise. Format de scène 0.2.0 (les projets 0.1.0 s'ouvrent
  tels quels).
- **Recalibrer hors du plan** : la carte se range dans le panneau de droite (ou, panneau
  replié, en bas à droite) : la pièce reste entièrement visible et cliquable pour les deux
  clics.
- **Recalibrer avec les dimensions de la pièce (R3)** : longueur, largeur et hauteur sous
  plafond, chacune facultative ; une seule valeur met tout le plan à l'échelle comme avant ;
  longueur et largeur ensemble redonnent les proportions de la pièce (murs, ouvertures,
  meubles, vues et fond suivent ses deux axes), la hauteur règle alors plafond, égouts et
  faîtage ; l'effet est annoncé avant d'appliquer (« 9,65 × 5,00 m → 8,00 × 4,20 m »),
  le geste s'annule.
- **Niveau et type de pièce (R2)** : des combles reconnus sur la photo nomment le niveau
  « Grenier » (sorte « combles / grenier ») au lieu de « Rez-de-chaussée » ; la fiche du
  niveau montre son nom, sa sorte (rez-de-chaussée, étage, combles / grenier, sous-sol) et
  le type de chaque pièce (chambre, séjour, cuisine, salle de bain, bureau, combles, autre),
  modifiables d'un clic ; l'assistant et le générateur s'en servent.
- **Correctif (R1)** : une photo déposée sur l'accueil construit le plan tout de suite ;
  jusqu'ici elle était seulement importée, et le plan n'arrivait qu'avec une seconde photo.
- **Assistant et générateur sur toute la bibliothèque (F5)** : l'assistant comprend évier,
  cuisinière, frigo, WC, douche, baignoire, lavabo, lave-linge, table basse… et « dans le
  coin » ; l'usage de la pièce écarte, en le disant, les meubles qui n'y vont pas (pas de WC
  dans une chambre, pas de lit dans une cuisine ; le lave-linge reste possible en cuisine) ;
  une demande de dispositions garde tous les meubles nommés.
- **Symboles 2D des meubles (F4)** : dans le plan, l'export SVG et le PDF, chaque meuble a
  son contour et le symbole de sa famille (oreillers du lit, dossier du canapé, feux de la
  cuisinière, cuvette du WC, croix de la douche, tambour du lave-linge…), à sa taille et dans
  son orientation ; table ronde et lampadaire ont un contour rond. Le SVG et le PDF, qui
  n'affichaient pas les meubles, les montrent désormais (pointillés si estimés ou générés).
- **Bibliothèque de meubles (F3, panneau)** : le bouton Meuble ouvre des cartes illustrées
  (vignette, nom, pièce, cotes), filtrées par pièce (Séjour, Chambre, Cuisine, Salle de bain,
  Bureau) et par recherche ; on choisit une carte puis on clique sur le plan : le meuble suit
  le curseur, s'accroche au mur proche (Alt : libre), rougit et dit la règle touchée avant
  d'être posé ; Échap annule. Remplace la liste déroulante et « Ajouter au plan ».
- **Meubles en vrai volume (F2)** : la vue 3D, le GLB et le rendu Blender montrent le
  modèle low-poly de chaque meuble (couleurs de ses matières), à la taille et à l'orientation
  de sa boîte ; les règles restent calculées sur la boîte ; sans modèle, la boîte revient.
- **Bibliothèque de mobilier (F1, catalogue)** : 34 meubles au lieu de 11 (séjour, chambre,
  bureau, cuisine, salle de bain : canapés, fauteuil, table basse, meuble TV, lits superposés,
  meubles de cuisine, évier, cuisinière, réfrigérateurs, WC, douche, baignoire, lavabo,
  lave-linge…), chacun avec sa pièce, son modèle 3D low-poly et sa vignette (Kenney, Furniture
  Kit, CC0 vérifiée) ; les onze meubles d'origine gardent leur identifiant.

### 2 octobre 2026

- **Assistant avec un modèle distant (Gemini), consenti par projet** : le panneau dit quel
  modèle répond et où ; tant que l'envoi n'est pas autorisé pour le projet, rien ne part et
  les phrases simples font le travail ; « Voir ce qui partirait » montre le texte mot pour mot
  avant tout envoi ; l'accord est demandé dans le panneau ; chaque envoi est au journal réseau.
  Le modèle local reste le défaut.

### 1er octobre 2026

- Aménager : chaque variante commence par un verdict lisible (« Très bien », « Correct »,
  « À revoir »), le score chiffré passe dans le détail ; deux meubles identiques se nomment
  par leur place (« Table de chevet (à gauche) / (à droite) »).
- Finitions : champs d'une fiche alignés, bouton « Ajouter une photo » au lieu du champ de
  fichier du navigateur, accueil (séparateurs réguliers, formats PDF et SVG cités, cartes
  centrées, « supprimer » en bas de chaque carte), nouveau point de vue placé dans un coin et
  tourné vers la pièce, curseur « interdit » sur un bouton inactif.
- Lisibilité : dates en français fixe (« 1er oct. 2026 à 10:56 »), une seule phrase par
  variante d'aménagement avec « Appliquer » en bouton principal et le détail replié, zones
  cliquables d'au moins 26 px (liens, listes, cases), textes d'au moins 12 px.
- **Assistant sur le plan lui-même** : « la pièce fait 5 m sur 3,5 », « le mur du fond fait
  4,5 m », « hauteur sous plafond 2,60 » (longueurs des murs, jamais une coordonnée),
  « ajoute une porte sur le mur de gauche », « mets une fenêtre de 1,20 m au fond » (posée dans
  la place libre du mur), « appelle cette pièce Chambre d'amis » ; le mur se désigne par sa
  position (face à la photo, sinon vers le nord), sa taille ou ce qu'il porte, sinon question.
  Mémoire courte (« déplace-le » = dernier meuble cité), modèle qui a compris affiché en
  petit, bandeau « Plan estimé » réservé à Éditer.

### 30 septembre 2026

- Mise en page selon la largeur : palette en haut à gauche sous la pastille du projet
  (maquette), étapes centrées, bande des photos toujours en bas à gauche, barre du bas allégée
  sur les écrans étroits, zoom et Recadrer masqués en 3D plein écran, panneau en tiroir
  (fermé au départ) sous 900 px, bandeaux au-dessus des barres, galerie qui remonte après un
  rendu ; le panneau replié garde l'état de ses fiches.
- Plan : les étiquettes du plan ne se recouvrent plus (nom et surface de la pièce, noms des
  meubles et des variantes, cotes) : elles s'écartent d'un cran quand la place manque.
- Correctif : deux gestes très rapprochés (deux clics de mur) pouvaient s'écraser l'un l'autre
  côté serveur ; chaque projet a désormais son verrou d'écriture.
- **Assistant : le modèle local comprend** les phrases libres (texte + description du plan,
  jamais la photo) et répond aux questions (« quelle surface ? ») ; sans modèle, indisponible ou
  sans consentement, les phrases simples prennent le relais ; la commande d'installation d'Ollama
  est dite une fois. Essai réel qwen2.5vl:3b : 9 phrases sur 10, piège tenu, ambiguïté → question,
  aucune coordonnée ni dimension inventée, 2 à 3 s par phrase.
- **Assistant dans l'atelier** (bouton de la palette) : on écrit « ajoute un bureau près de la
  fenêtre », « déplace le lit loin de la porte », « enlève l'armoire », « propose-moi trois
  aménagements » ; sans modèle configuré, ces phrases simples sont comprises (exemples
  cliquables sous le champ) ; chaque message dit ce qui a été fait, refusé (avec la voie
  manuelle) ou demandé, surligne les meubles touchés et s'annule d'un clic ; les dispositions
  s'ouvrent dans Aménager.
- **Assistant qui agit sur le plan (serveur)** : intentions typées (ajouter, déplacer, retirer
  un meuble, proposer des dispositions, question, réponse) comprises par le modèle mais
  **placées par le moteur** (jamais une coordonnée du modèle) ; chaque geste est un commit
  d'historique attribué à « assistant », annulable ; cible ambiguë → question ; meuble posé
  par l'utilisateur jamais retiré par l'assistant (`POST …/assistant/act`).
- **« Recalibrer », seul outil de correction d'échelle** : deux clics sur une longueur
  connue du plan, ou la hauteur sous plafond, ou la longueur réelle d'un mur depuis sa
  fiche ; murs, fond de plan, meubles et vues suivent ensemble, le facteur est annoncé
  (« Plan mis à l'échelle × 1,200 »), le geste s'annule ; l'outil Cote du fond est remplacé.
- **Paramètres avancés allégés** : Réglages (unités, nord), Assistant IA, État de
  l'installation, Diagnostic ; le reste (photos, calibration détaillée, Blender, légende)
  dans « Outils experts (rarement utiles) », replié. Les unités quittent la fiche Niveau.
- Documentation : guide « Premier essai » réécrit pour le parcours automatique (quatre étapes :
  déposer, recalibrer, vérifier, exporter), note de parcours et protocole d'évaluation (durées
  mesurées) complétés ; le front ne garde plus les anciennes actions d'analyse.
- **Fiche d'une pente de toit** : hauteur à l'égout, inclinaison et hauteur au faîtage se
  corrigent en sélectionnant la pente dans le plan.
- **Importer sans rien décider** : on dépose une photo, l'image d'un plan ou un plan DXF /
  PDF / SVG, et le plan est construit tout seul (progression lisible avec la durée annoncée,
  la page reste utilisable). À l'arrivée dans Éditer : bandeau « Plan estimé » avec
  « Recalibrer » et, si l'image a été mal comprise, « ce n'est pas une photo / un plan »
  qui la relit autrement en un clic. Plus aucun bouton d'analyse (« Proposer un plan »,
  « Profondeur », « Combles », « Portes et fenêtres », « Vectoriser »…) ni question sur la
  hauteur de l'appareil (supposée 1,50 m). Un plan vectoriel donne ses murs tout de suite ;
  calques et échelle papier se corrigent après coup dans « Corriger la lecture du plan ».
  Le détail de ce qui a été fait est dans Paramètres avancés › Diagnostic (replié).

### 29 septembre 2026

- **Plan construit tout seul à l'import (côté serveur)** : une photo de pièce ou une image
  de plan déposée donne un plan sans question ni choix de méthode
  (`POST …/images/{id}/autoplan`, tâche de fond à étapes lisibles). Photo : lignes,
  distances, pentes de toit, portes et fenêtres, meilleure méthode gardée et l'autre en
  secours, appareil supposé à 1,50 m. Image de plan : redressement, échelle devinée (cotes
  lues si Tesseract est installé, sinon épaisseur des murs, porte standard, taille
  plausible), murs estimés. Ce qui manque est dit une fois avec la commande.

### 28 septembre 2026

- **Installation sans cloner** : paquet `openplan-studio` avec le front dedans
  (`uv tool install "openplan-studio[app]"`, puis `openplan doctor` et `openplan serve --web`) ;
  guide de dix minutes `docs/guide/installer.md` ; workflows CI et release écrits (publication
  PyPI dès que le dépôt est en ligne).
- **`openplan doctor`** : bilan de l'installation avec la commande pour chaque manque, aussi
  sur l'accueil (« État de l'installation ») et par `GET /api/doctor`.
- **Sécurité de l'API exposée** : jetons nommés (`openplan token <nom>`, acteur `user:<nom>`
  dans l'historique), journal des accès sans jeton ni contenu, une tâche lourde à la fois par
  utilisateur (429), en-têtes de sécurité et CSP stricte, refus de démarrer exposé sans jeton,
  `/api/health` enrichi.
- **Téléphone** : application installable (PWA, service worker limité au front), « Prendre une
  photo » sur l'écran Importer (focale EXIF conservée, le reste retiré), éditeur au doigt
  (pointer events, pincer pour zoomer, barres adaptées sous 600 px), guide `docs/guide/mobile.md`.
- **Aménagement** : avis sur une variante (règles calculées puis modèle, chaque remarque cite le
  meuble et la règle, action en un clic vers une variante révisée à relire), glisser-déposer des
  meubles dans le plan avec accrochage aux murs et règles en direct, comparaison côte à côte de
  deux variantes, vignette Blender par variante sans l'appliquer. Fin de la feuille de route 0.3.
- **Import et rendu** : plan PDF vectoriel, SVG et image de plan vectorisée (U1b) ; éclairage
  « heure du jour » avec le nord du plan (R1b).
- **Correctif** : superpositions des barres flottantes et « trait violet » (boussole d'un serveur
  resté sur l'ancien code) ; disposition vérifiée en 1366 × 768.
- Note de cadrage 0.4 (hébergement gratuit et sécurisé, mobile, consolidation) et ses décisions.

### 27 septembre 2026

- Générateur de dispositions (3.4) : brief → variantes valides, distinctes, scorées ; « Appliquer
  cette variante » annulable.
- Refonte du front sur la maquette d'augus (accueil avec dépôt de photo, canevas plein écran,
  palette et barres flottantes, volet 3D), puis parcours **Importer · Éditer · Rendre** (E1, U1,
  R1) : inspecteur contextuel, fiche Aménager en trois pas, import de plan DXF, écran Rendre
  (points de vue, rendu Blender en tâche de fond, galerie).

### 23 → 25 septembre 2026

- Tranche verticale 0.1 : photo → proposition de pièce → éditeur 2D → calibration → 3D →
  exports SVG, PDF, DXF, GLB, JSON ; API locale sur 127.0.0.1 ; CLI.
- Assistant IA opt-in par API compatible OpenAI (Ollama local ou Gemini avec consentement par
  projet), rôles Perception, Intention, Critique ; journal réseau sans contenu.
- Profondeur métrique locale (Depth Anything V2), combles depuis la profondeur, plan depuis la
  profondeur, portes et fenêtres (OWLv2), image d'inspiration locale (SD 1.5 + ControlNet,
  licence à accepter), rendu Blender avec matières, multi-photos, index des projets, conteneur
  Docker, catalogue de meubles CC0 et règles d'aménagement.
