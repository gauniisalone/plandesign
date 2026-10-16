# 12 — Cadrage : accueil façon assistant (L1, L2) et plan depuis un programme (L3)

Demande d'augus du 6 octobre 2026 (relayée par le PO à 21 h) : une page d'accueil qui
ressemble à un assistant. On y écrit ce qu'on veut, ou on répond à quelques questions, on
dépose éventuellement une photo ou un plan, puis on arrive sur « Vos rendus ». Cette note
précède tout code. Elle s'accompagne d'une maquette statique (captures en 1280 et 1920, en
clair et en sombre, dans les états saisie, envoi et échec) et d'un tableau de correspondance
avec l'écran Importer actuel.

## 1. Ce qui ne change pas

- Le chemin automatique (V1.6/V1.7) reste le moteur : plan, ambiance, aménagement, trois vues,
  rendus, finition, inspiration. L'accueil n'en est qu'une nouvelle porte d'entrée.
- Tout reste local. Le texte n'est envoyé à un modèle distant que si le projet y a consenti
  (comme pour l'assistant B5). Par défaut, ce sont les phrases simples de V1.5, sur cette
  machine.
- L'écran Importer du projet (étape 1) reste en place pour ce qui ne tient pas sur l'accueil :
  correction des calques et de l'échelle papier d'un DXF/PDF, liste des images,
  « Prendre une photo » sur téléphone.

## 2. L1 — Page d'accueil

### Mise en page (maquette)

- **Colonne gauche (264 px, repliable)**
  - logo OpenPlan et bouton de repli ;
  - « + Nouveau projet » : vide le formulaire central ;
  - « Vos projets » : nom tronqué avec infobulle (nom complet et date de modification) ;
    corbeille au survol, avec la même confirmation qu'aujourd'hui ;
  - pied : « OpenPlan Studio · tout reste sur cette machine » et Réglages (thème, état de
    l'installation).
- **Centre**
  - logo, titre « Que voulez-vous concevoir ou planifier ? » ;
  - onglets « Texte libre » / « Questions guidées » ;
  - une carte de saisie : « Importer un plan » (trombone), pastilles des fichiers joints
    (vignette, nom, ×) et bouton d'envoi violet ;
  - dessous, la zone de dépôt « Déposer un plan ou une photo ».
- **Formats acceptés** : PNG, JPG, WEBP, SVG, comme dans la demande. J'ajoute DXF et PDF
  vectoriel, déjà pris en charge, pour ne rien perdre.
- **Ligne « Compris : … »** sous le texte : ce que la traduction locale a compris, mis à jour
  en direct (phrases simples, sans réseau). On voit avant l'envoi ce qui partira dans
  l'intention. C'est un ajout de ma part, à valider.

### Comportement de l'envoi

| Cas | Ce qui se passe |
| --- | --- |
| Fichier(s) + texte ou réponses | Le projet est créé, nommé d'après le texte (« Grenier chaleureux ») ou, sans texte, d'après le fichier. Le plan est construit depuis le premier fichier ; les fichiers suivants sont ajoutés comme photos du projet (multi-photos 2.5). Le chemin automatique démarre avec la consigne (voir plus bas). |
| Fichier seul | Comme aujourd'hui depuis l'accueil : plan puis chemin, ambiance déduite (`inferred`). |
| Texte ou réponses sans fichier, avant L3 | On ne bloque pas. Si une surface est connue (réponse guidée ou « 60 m² » dans le texte), une pièce rectangulaire de cette surface (4:3, hauteur 2,50 m) sert de plan de départ, marquée « plan de départ simplifié », puis le chemin démarre. Sans surface, le projet s'ouvre dans Éditer avec la consigne gardée : elle s'applique dès qu'une pièce existe. |
| Texte ou réponses sans fichier, après L3 | Plan généré depuis le programme (§ 4), puis le même chemin. |
| Ni texte ni fichier | Bouton d'envoi inactif. « + Nouveau projet » suivi de l'envoi d'un texte vide n'existe pas. Le projet vide pour tracer à la main reste possible : lien discret « Commencer par un plan vide » sous la zone de dépôt (l'actuel « Créer »). |

### Après l'envoi : le fil de progression (ajout d'augus)

- Le formulaire reste affiché mais grisé (champs désactivés) et le bouton d'envoi devient un
  indicateur qui tourne.
- Sous la carte, une ligne discrète suit le chemin :
  « Création de votre plan… » (tâche de construction du plan), puis « Ambiance… »,
  « Aménagement… », « Vues… », « Rendu des vues (2/3)… », « Finition… », « Inspiration… ».
  Elle affiche aussi le temps restant (`remaining_s` du bandeau actuel) et une barre fine.
- À la fin, on bascule sur le projet, à l'étape Rendre, sur « Vos rendus » (écran d'arrivée
  V1.6).
- **Échec de la construction du plan** : message clair (« Le plan n'a pas pu être construit
  depuis cette photo »). La photo et le texte sont gardés, et le formulaire redevient
  éditable avec trois issues : réessayer, ajouter une photo, ou continuer sans plan (Éditer).
- **Échec d'une étape suivante** : comme aujourd'hui, on arrive sur « Vos rendus », qui dit
  quelle étape a échoué.

Point technique : aujourd'hui, l'enchaînement « plan construit → chemin » vit dans
`useProject` (l'atelier). L'accueil doit le suivre sans ouvrir l'atelier. Je propose
d'extraire l'orchestration dans un petit module partagé (`state/pipeline.ts`) que
l'accueil et l'atelier utilisent tous deux. Aucun changement d'API pour cette partie.

### Où va la consigne (changement d'API, « relance nécessaire »)

Aujourd'hui, la première étape du chemin pose une ambiance déduite seulement si la pièce n'en
a pas, et la consigne V1.5 s'applique à une pièce qui existe déjà. Avec l'accueil, la pièce
n'existe pas encore au moment où l'on écrit. Je propose :

- `POST …/autopilot` accepte en option `instruction` (texte, 500 caractères au plus) et
  `answers` (réponses guidées, § 3) ;
- l'étape 1 du chemin les traduit (modèle consenti, sinon phrases simples) sur la pièce
  principale, au lieu de l'ambiance « naturel » déduite. Le tout est journalisé (V2) et
  annulable en un pas.

C'est côté serveur pour rester déterministe et testable, et pour que rien ne dépende d'un
onglet resté ouvert.

## 3. L2 — Questions guidées → `DesignIntent`

| Question | Valeurs | Où ça va | Source |
| --- | --- | --- | --- |
| Quel espace ? | Appartement, Maison, Studio, Pièce : chambre / séjour / cuisine / salle de bain / bureau, Combles / grenier | Pièce : `room_type` (vocabulaire `ROOM_TYPES`) ; Combles : `level.kind = attic` et `room_type = attic` ; Appartement / Maison / Studio : programme L3 (type de logement) | `user_defined` |
| Surface (m²) | 8 à 400 | Programme L3 ; avec une photo, garde-fou d'échelle (voir plus bas) | `user_defined` |
| Chambres | 0 à 6 | Programme L3 seulement | `user_defined` |
| Style | Laisser choisir, Naturel, Chalet, Scandinave, Industriel, Contemporain | `intent.style` (« Laisser choisir » = pas de valeur, déduit) | `user_defined` |
| Plus d'options (repliées) | Format paysage 4:3 / portrait 4:5 ; lumière (matin, midi, heure dorée, soir) ; ambiance (chaleureux, lumineux, épuré, cosy, brut) | `views.aspect`, `light.time`, `mood` | `user_defined` |
| Vos envies et contraintes… | Texte libre | Consigne V1.5 → palette, matières, meubles, zones, à éviter ; texte gardé dans `instruction` | traduite (`translated_by`) |

Règles :

- une réponse guidée prime sur ce que le texte en dit (même champ) ; le texte complète ;
- rien de choisi n'est réécrit plus tard par le chemin (règle V1 inchangée) ;
- une réponse vide reste `inferred`.

**Surface et photo** (proposition) : si une seule pièce est construite depuis une photo et
que sa surface estimée s'écarte de plus de 15 % de la surface donnée, l'échelle est corrigée
d'un facteur √(S donnée / S estimée), borné entre 0,6 et 1,6. Ce n'est fait que si la photo
ne montre qu'une pièce. La correction est dite dans le bandeau « Plan recalibré » et se défait
en un clic, comme Recalibrer. Sinon, la surface n'est qu'indicative. À valider : c'est une
correction automatique d'échelle.

## 4. L3 — Plan depuis un programme, sans fichier (à cadrer, pas à coder)

Entrée : surface totale, type de logement, nombre de chambres, et les pièces citées dans le
texte (« cuisine ouverte », « coin bureau »). Sortie : un niveau avec murs extérieurs,
cloisons de 7 cm, portes de 83 cm, pièces typées, puis le même chemin automatique. Tout
reste gratuit et local.

### Programme

Surfaces de départ par pièce, ajustées pour que la somme fasse la surface donnée :

| Pièce | Surface de départ |
| --- | --- |
| Séjour et cuisine ouverte | 25 à 35 m² |
| Chambre | 9 à 12 m² (9 m² au minimum, 2,40 m de large) |
| Salle de bain | 4 à 6 m² |
| WC | 1,5 m² |
| Entrée et dégagement | 8 à 12 % de la surface |
| Bureau, s'il est demandé | 7 m², ou un coin dans le séjour (zone V1.2) |

### Trois approches

1. **Découpage récursif (« slicing » / treemap équilibré)**
   - Principe : le rectangle d'emprise (rapport 1,3 à 1,6) est coupé en deux, puis chaque
     part encore en deux, selon les surfaces à placer ; les regroupements suivent les zones
     jour et nuit. Un dégagement est inséré le long d'une coupe pour desservir les chambres,
     puis les portes sont posées sur les cloisons communes.
   - Pour : instantané (moins de 50 ms), déterministe, sans dépendance, explicable,
     testable ; le plan reste rectangulaire, comme la plupart des T2 et T3.
   - Contre : uniquement des rectangles ; les adjacences ne sont qu'approchées (séjour côté
     façade, chambres groupées) ; pas de forme en L.
2. **Graphe d'adjacence et placement sur grille**
   - Principe : les pièces sont des nœuds, avec les adjacences voulues (cuisine–séjour,
     entrée–séjour, chambres–dégagement, salle de bain–dégagement). Le placement se fait sur
     une grille de 30 cm, avec OR-Tools CP-SAT (Apache 2.0, local) ou un recuit simulé.
     Les contraintes portent sur les surfaces à ±10 %, la largeur minimale, les adjacences et
     l'accès depuis l'entrée.
   - Pour : respecte vraiment les adjacences ; permet les formes en L et plusieurs variantes
     (comme Aménager).
   - Contre : quelques secondes de calcul, une dépendance de plus (OR-Tools pèse environ
     40 Mo), plus de réglages, et des échecs possibles à expliquer.
3. **Modèle local (en option, plus tard)**
   - Principe : modèles de recherche qui génèrent des plans depuis un graphe (House-GAN++,
     HouseDiffusion, Graph2Plan). Ils sont entraînés sur RPLAN, des plans d'appartements
     chinois dont la licence est réservée à la recherche, non commerciale.
   - Contre : licences des poids et des données à vérifier une par une, téléchargement à
     faire accepter par le PO, sorties en images à vectoriser, style de plan éloigné du bâti
     français. Je ne le recommande pas pour l'instant (« gratuit avant tout », poids jamais
     livrés).

**Recommandation** : (1) d'abord, assez bon pour un plan de départ qu'on corrige ensuite dans
Éditer ; puis (2) si les essais montrent des adjacences ratées. (3) reste une piste
documentée.

### Limites honnêtes

- Un plan généré n'est pas un relevé. Il est marqué `generated` (provenance existante), et le
  bandeau le dit.
- Sans photo, le rendu montre un logement plausible, pas celui de l'utilisateur.
- Les façades, fenêtres et l'orientation sont inventées : une fenêtre par pièce de vie, côté
  séjour au sud par défaut ; elles se corrigent dans Éditer.
- Le chemin automatique travaille aujourd'hui sur la pièce principale. Pour un appartement
  entier, le rendu de plusieurs pièces est un sujet à part : séjour d'abord, puis une
  chambre.

### Essai proposé (une demi-journée, avant tout code produit)

- Cinq programmes : studio 30 m², T2 45 m², T3 60 m² (l'exemple d'augus), T4 80 m², maison
  110 m² sur un niveau.
- Approche (1) en script hors produit.
- Mesures :
  - écart de surface par pièce ;
  - toutes les pièces accessibles par une porte ;
  - largeurs minimales respectées ;
  - temps de calcul ;
  - chemin automatique complet sur une copie (rendus).
- Livrables : planches plan + rendus, puis décision (1) seul, ou (1) puis (2).

## 5. Découpage proposé

| Lot | Contenu | API |
| --- | --- | --- |
| L1a | Accueil : colonne des projets, onglet texte, dépôt multi-fichiers, fil de progression, échec, arrivée « Vos rendus » ; orchestration partagée | `instruction` sur `/autopilot` : relance nécessaire |
| L1b | Sans fichier avant L3 : pièce rectangulaire depuis la surface, ou consigne gardée | – |
| L2 | Questions guidées, « plus d'options », garde-fou de surface (si validé) | `answers` sur `/autopilot` : relance nécessaire |
| L3-essai | Essai découpage récursif (§ 4), planches | – |
| L3 | Générateur retenu, intégré au chemin | à préciser |

Chaque lot comprend un e2e (envoi avec photo, sans fichier, échec, multi-photos, DXF) et des
captures en 1280 / 1920, clair / sombre.

## 6. Correspondance avec l'accueil et l'écran Importer actuels (rien de perdu)

| Aujourd'hui | Où c'est avec la nouvelle page d'accueil |
| --- | --- |
| Accueil : déposer une photo ou un plan crée le projet et construit le plan | Zone de dépôt, bouton « Importer un plan » et envoi ; en plus, le texte ou les réponses partent dans l'intention, puis le chemin mène à « Vos rendus » |
| Accueil : formats JPG, PNG, WEBP, DXF, PDF vectoriel, SVG, 50 Mo, EXIF retiré | Mêmes formats et mêmes contrôles ; la ligne de la zone de dépôt affiche DXF et PDF en plus de la liste d'augus |
| Accueil : nom + « Créer » (projet vide) | Lien « Commencer par un plan vide » sous la zone de dépôt ; nom du projet modifiable ensuite dans l'atelier (barre du haut) |
| Accueil : nom du projet repris du fichier (« WhatsApp Image… ») | Nom tiré du texte (« Grenier chaleureux »), sinon du fichier |
| Accueil : cartes des projets (vignette, nom, n photos, n murs, date, supprimer) | Liste « Vos projets » : nom, corbeille avec confirmation ; date, nombre de photos et de murs dans l'infobulle. La vignette disparaît de la liste (demande d'augus : nom et corbeille) ; elle peut revenir au survol si on la regrette |
| Accueil : bandeau « l'API locale ne répond pas » | Inchangé, au-dessus du titre |
| Accueil : état de l'installation (doctor) | Réglages, en pied de colonne (avec le thème) ; une alerte reste visible au centre s'il manque quelque chose de bloquant |
| Accueil : « tout reste sur cette machine », note « jamais un relevé certifié » | Pied de colonne, et sous la zone de dépôt |
| Plusieurs photos (2.5) | Plusieurs fichiers joints en pastilles avant l'envoi ; le premier construit le plan, les suivants sont ajoutés au projet (vues comparées au plan) ; ensuite, le « + » de la bande photo dans Éditer, comme aujourd'hui |
| Importer : DXF/PDF lus, puis correction des calques et de l'échelle papier | Inchangé dans l'écran Importer du projet (étape 1, toujours accessible) ; l'écran d'arrivée « Vos rendus » ajoute un lien « Corriger la lecture du plan » quand le plan vient d'un fichier vectoriel |
| Importer : plan estimé, « Recalibrer », « ce n'est pas un plan / une photo » | Inchangé dans Éditer (bandeau) ; avec une surface donnée, garde-fou d'échelle (§ 3, à valider) |
| Importer : progression de la construction du plan, échec expliqué | Fil de progression sous la carte (§ 2) et message d'échec avec formulaire éditable |
| Importer : « Prendre une photo » (téléphone) | Inchangé dans Importer ; mobile gelé |
| Importer : liste des images importées | Inchangé dans Importer et dans la bande photo |
| Chemin automatique désactivé (Réglages du projet) | Respecté : l'envoi construit le plan et ouvre Éditer, sans rendus ; l'accueil le dit (« chemin automatique coupé dans les réglages ») |