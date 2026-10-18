# Premier essai : de la photo du grenier au plan PDF

Écrit pour quelqu'un qui n'est pas développeur. Tout se passe sur votre machine : aucune photo
ne quitte l'ordinateur, aucun compte, aucune clé (le détail est à la fin, « Ce qui part, et
où »). Comptez cinq minutes : on dépose une photo,
le plan se construit tout seul, on donne une longueur connue, on exporte.

## Lancer PlanDesign

Depuis Windows, une seule chose à faire : double-cliquer sur `start-openplan.cmd` (à la
racine du projet, accessible dans l'explorateur par
`\\wsl.localhost\Debian\home\<utilisateur>\plandesign`). Une fenêtre
noire s'ouvre, construit l'interface (30 secondes la première fois) puis affiche :

```
PlanDesign : ouvrir http://localhost:8000/ (Ctrl+C pour arrêter)
```

Le navigateur s'ouvre tout seul sur cette adresse ; sinon, la taper. Laisser la fenêtre noire
ouverte : la fermer arrête le logiciel.

Équivalent dans PowerShell, si vous préférez :

```
wsl -d Debian -- bash -lc "cd ~/plandesign && make start"
```

## Les quatre étapes

### 1. Déposer la photo du grenier

La page d'arrivée est la **galerie de vos espaces de travail** (un projet = un espace de travail, avec sa
vignette : son dernier rendu, sinon son plan). Cliquer **+ Nouveau projet** : l'espace de travail du
projet s'ouvre : à gauche les cadres **Photos de la pièce** et **Inspirations**, au centre le
**Plan**, à droite les **Rendus**, et en bas le **compositeur** (vos exigences et « Générer »). **Déposer la photo du grenier** dans le cadre « Photos de la pièce »
(ou **Importer › Photo ou plan de la pièce** dans la colonne d'outils de gauche). La photo est importée et le plan se construit
tout seul (bascule « Générer automatiquement au dépôt d'une photo », activée par défaut) ; la
progression s'affiche dans le cadre Plan. Rien n'est demandé : l'appareil est supposé tenu à
1,50 m du sol, et tout se corrige après.

Ce que vous devez voir, quinze secondes plus tard : le **plan** de la
pièce (murs en pointillé : ils sont estimés), la caméra de la photo, et un bandeau « **Plan
estimé** : vérifiez l'échelle » avec le bouton **Recalibrer**. Sur le grenier, les deux pentes
du toit sont reconnues : le plan montre les égouts en pointillé orange et la ligne « 1,80 m sous
pente », la pièce s'appelle « Combles ». Si l'image a été mal comprise (une photo prise pour un
plan dessiné, ou l'inverse), le bandeau propose « ce n'est pas une photo » / « ce n'est pas un
plan » : un clic la relit autrement.

Vous pouvez aussi déposer un **plan** : l'image d'un plan (JPG, PNG, ou un PDF scanné exporté en
image) est redressée, son échelle devinée (cotes lues si Tesseract est installé, sinon épaisseur
des murs ou largeur de porte : le bandeau le dit) et ses traits épais deviennent des murs
estimés ; un plan **DXF**, **PDF vectoriel** ou **SVG** donne ses murs tout de suite (échelle
papier lue ou proposée). Pour corriger après coup les calques retenus ou l'échelle papier :
**Importer › Photo ou plan de la pièce** (colonne de gauche ; dans le plan, **Ajouter une
source** en haut), bloc « Corriger la lecture du plan ». Sans photo ni
plan, le cadre Plan vide propose **✎ Tracer moi-même** (les murs à la main) ou **Générer le
plan** d'après vos exigences (le compositeur) ; le nom du projet se change d'un double-clic sur sa carte,
dans la galerie.

Si la bascule est activée, rien d'autre n'est à faire : le plan construit, les **rendus des
vues** suivent tout seuls et arrivent dans le cadre Rendus (zéro geste après le dépôt).
Bascule désactivée, **Générer maintenant** dans le compositeur lance la même chaîne (un
geste). Si le plan ne se construit pas, le projet, la photo et vos exigences restent, avec un
message qui dit pourquoi ; l'image d'inspiration de secours de l'ancien accueil se demande
désormais depuis Paramètres avancés › Outils experts › Photos.

Un projet s'ouvre sur son **espace de travail** (voir plus bas), cadré sur le cadre **Rendus**
dès qu'un rendu existe ; le **plan** s'édite depuis son cadre, et **← Espace de travail**, en
haut, y ramène. Les rendus se regardent en grand dans la **visionneuse** : **Voir en grand**
dans l'en-tête du cadre Rendus, un double-clic sur un rendu ou une vignette de la bande sous
le plan ; ← → passent d'une vue à l'autre, Échap ferme. **+ Nouveau plan**, en haut de
l'espace de travail, ajoute une variante de ce plan, un nouvel étage ou un plan vide : chaque
plan a sa rangée, son plan et ses rendus. Dans le plan :
un canevas quadrillé plein écran ; en haut le nom du projet, **Ajouter une source** (photos ou plan), le bouton qui replie le panneau de droite et l'engrenage
**Paramètres avancés** ; dessous la palette (sélection, main, mur, porte, fenêtre,
**Recalibrer**, pièces, **Meuble**, **Aménager**, annuler, rétablir, supprimer) ; en bas à
gauche la bande des photos (le **+** ajoute une photo : elle est analysée toute seule et
complète le plan) ; en bas à droite le zoom, les vues Plan / Plan + 3D / 3D, le menu Exporter et
« copier le lien », et à gauche de la barre du bas le **nom et la hauteur sous plafond du
niveau**. Le **panneau de droite** (la fiche de ce qui est sélectionné ; sans sélection, le
niveau et les vérifications) est **fermé au départ** et ne s'ouvre jamais tout seul : son
icône en haut à droite l'ouvre, comme « Réglages et galerie » ou « Changer l'ambiance ». Une photo peut aussi être glissée directement sur le canevas.

### 2. Recalibrer

Tout est à l'échelle près : donnez **une** longueur que vous connaissez. Bouton **Recalibrer**
(bandeau ou palette), puis au choix :

- deux clics sur les deux bouts d'une longueur connue du plan (un mur, une porte), sa valeur
  réelle, et **Appliquer** : la carte annonce d'abord par combien le plan sera multiplié et
  signale une valeur aberrante ;
- la **hauteur sous plafond** dans la même carte (**Appliquer la hauteur**) : la profondeur d'une
  photo se trompe d'un facteur global, la hauteur corrige donc aussi le plan ;
- ou sélectionner un mur, saisir sa **longueur réelle** dans sa fiche, **Ajouter**, puis
  **Calibrer le plan**.

Murs, meubles, vues et fond de plan suivent ensemble. Ce que vous devez voir : « Plan mis à
l'échelle × 1,200 » avec un lien **annuler**, le bandeau « plan estimé » disparaît, les cotes du
plan changent. Les pentes du toit se corrigent en cliquant une pente dans le plan (hauteur à
l'égout, inclinaison, hauteur au faîtage dans sa fiche) ; les murs, en glissant leurs sommets.

### 3. Vérifier et compléter

Vue **Plan + 3D** : les murs, et pour le grenier les deux versants et le pignon en volume. Les
traits en pointillé sont des estimations, le trait plein ce que vous avez saisi ou mesuré.
Portes et fenêtres vues sur la photo sont posées sur leur mur (si les poids du détecteur sont
là) : cliquez-en une pour la corriger ou la supprimer, ou dessinez-les avec la palette. Les
**Paramètres avancés › Diagnostic** disent ce qui a été fait étape par étape (lignes, distances,
pentes, ouvertures), la méthode gardée et celle mise de côté.

### 4. Exporter le plan en PDF

Barre du bas, menu **Exporter**, lien **PDF** : un plan coté à l'échelle (1:50 ou 1:100 selon la
taille), avec les cotes, la ligne 1,80 m, un cartouche (projet, date, échelle) et une légende qui
dit ce qui est estimé (« env. ») et ce qui est mesuré. **DXF** donne le même plan pour un
logiciel de dessin, **JSON à partager** une copie du projet sans historique ni chemin de photo.

Ce que vous devez voir : le PDF s'ouvre dans le navigateur ou se télécharge, avec le rectangle
des combles et ses cotes.

**Rendus** : vos images en grand dans la **visionneuse**, une à la fois (une vue dont le plan, l'ambiance ou le point de vue ont changé depuis son rendu est marquée **périmée** ; l'en-tête du cadre Rendus, celui de la visionneuse et la **bande de rendus** sous le plan les refont d'un clic : **Re-rendre les n vues** ; **Affiner cette vue**, sous l'image, ouvre la 3D depuis sa caméra avec la pièce choisie) et, dans le volet **Rendus** (**Réglages et galerie** sur le cadre), vos **points de vue** (la caméra de chaque photo,
plus les points de vue que vous ajoutez et placez au glisser dans le plan : le rond déplace, la
pointe vise), une vignette 3D pour chacun, le bouton **Rendre** (qualité rapide / normale /
fine ; la durée et le périphérique sont annoncés avant de lancer ; le rendu tourne en fond, la
page reste utilisable) et la **galerie** des rendus à télécharger. Sans Blender, les vignettes
restent et le bouton le dit. Le menu **Éclairage** choisit l'heure du jour (matin, midi, soir,
nuit, ou neutre) ; dites où est le **nord** de votre plan (Paramètres avancés › Réglages, en
haut par défaut, boussole en haut à droite du plan) pour que le soleil vienne du bon côté.

## L'espace de travail du projet

L'espace de travail (l'éditeur, ouvert par **Ouvrir l'éditeur →**) réunit tout le projet sur un même espace, comme dans Figma ou Miro : les photos, la note de votre demande, les rendus, le plan et les
fichiers joints. À la première ouverture, tout est déjà posé : les photos et les rendus dans
leurs cadres, la note sous les photos, le plan à droite. Une nouvelle photo ou un nouveau
rendu y entre tout seul ; un rendu refait prend la place de l'ancien.

- **Bouger, agrandir** : glissez un élément ; la poignée en bas à droite le redimensionne.
  Redimensionner le cadre du plan ne change jamais ses cotes : seule sa taille à l'écran
  change.
- **Sélectionner à plusieurs** : Maj + clic, ou un rectangle tracé sur le fond.
- **Ranger** : l'outil Cadre (F) trace un cadre nommé ; ce qui est lâché dedans lui
  appartient et bouge avec lui.
- **Écrire** : l'outil Note (N), puis un clic ; double-clic sur une note pour la modifier.
- **Exigences : le compositeur** flotte en bas de l'espace de travail. Onglet **Prompt libre** : vos
  envies en texte libre ; onglet **Questions guidées** : espace, surface, chambres, style,
  « plus d'options » (format, lumière, ambiance, meubles voulus, à éviter). La case Gemini
  (texte seul) et la ligne « compris : … » sont communes. Tout est gardé dans le projet et
  lu par le chemin automatique au prochain plan ; « Nouvelle variante » envoie le texte à
  l'assistant. Le × le referme : plus rien de vos exigences dans l'espace de travail, la pastille
  **Générer** le rouvre.
- **Importer** : glissez des photos ou des fichiers dans l'espace de travail, collez-les (Ctrl+V),
  ou **Importer** dans la colonne de gauche : « Photo ou plan de la pièce » (le plan se
  construit) ou « Fichiers dans l'espace de travail ». Une photo entre dans le projet ; un autre fichier est joint tel
  quel (50 Mo au plus) et se télécharge depuis sa carte.
- **Le plan, à votre main ou généré** : tant qu'il est vide, le cadre Plan propose
  **✎ Tracer moi-même** (les outils du plan) ou **Générer le plan** : d'après la première
  photo de la pièce, sinon d'après vos exigences (logement décrit ou surface) ; la
  progression s'affiche dans le cadre, les rendus suivent si le chemin automatique est actif.
- **Lancer** : en bas du compositeur, « Générer automatiquement au dépôt d'une photo »
  (activée : déposer une photo de la pièce suffit, le plan puis les rendus suivent) et
  « Générer maintenant ».
- **Deux cadres à remplir** : **Photos de la pièce** reçoit les photos réelles (elles font le
  plan) ; **Inspirations** reçoit vos images d'ambiance : jointes au projet, montrées dans l'espace de travail et cochées Référence, leur allure guide les rendus contextualisés. Un fichier
  déposé dans un cadre y prend sa place.
- **Vidéos, PDF, liens** : une vidéo mp4 ou webm (200 Mo au plus) se lit dans l'espace de travail ; un
  PDF montre sa première page, rendue sur la machine, et **Ouvrir** l'affiche dans un nouvel
  onglet ; l'outil **Lien** (L), ou une adresse collée, pose une carte avec le domaine et le
  titre saisi : la page n'est jamais récupérée, elle s'ouvre au clic. Un fichier joint autrement
  (par l'API) arrive de lui-même dans le cadre **Fichiers**.
- **Rendu contextualisé** : sélectionnez une photo ou un rendu de vue 3D (le plan 2D n'est
  pas un guide d'image : s'il est choisi, une vue 3D rendue est proposée) puis **Rendu
  contextualisé…**. Depuis une photo, la source recommandée est la **vue 3D de l'aménagement**
  (l'image montre alors des meubles) ; « cette photo telle quelle » ne fait que restyler la
  pièce, objets compris, guidée par la profondeur seule avec des réglages plus libres. Écrivez votre intention (style, ambiance, matériaux, contraintes), choisissez
  un budget (lu comme une contrainte de matières, rien de plus) et **Construire le prompt** :
  le volet montre ce que le plan apporte (pièce et usage, dimensions avec « ≈ » quand elles
  sont estimées, portes et fenêtres avec leur côté, lumière, ce qu'il faut garder) et le prompt
  final, modifiable. **Générer** produit jusqu'à trois variantes à la même graine,
  conservatrice, créative et radicale, qui arrivent dans le cadre **Moodboard** avec leur
  badge « proportions estimées », leur source, leur horodatage et leur prompt (dépliable).
  Une traduction française du prompt construit s'affiche en lecture seule au-dessus de
  l'anglais (que le modèle lit). Le volet compte les jetons (« environ N sur 77 ») : le modèle
  coupe la fin au-delà, un avertissement le dit si votre modification déborde. Une génération
  ne modifie jamais le plan ni la 3D.
- **Références d'allure** : sélectionnez des images de l'espace de travail (photos, rendus, variantes,
  images jointes) et cochez **Référence** dans la barre de sélection : leur allure (matières,
  ambiance) guide les variantes, jamais leur géométrie (IP-Adapter local, poids à télécharger
  une fois avec `openplan models pull ip_adapter_sd15@1.0`, 2,6 Go). Quatre au plus, poids
  réglable (0,6 par défaut) ; sans référence, le texte seul guide. Les cartes des variantes
  disent quelles références les ont guidées.
- **Trier les variantes** : sur chaque carte du moodboard, ☆ **À conserver** marque la
  variante retenue (étoile, contour), 💬 **Commenter** attache une remarque à la carte
  (enregistrée avec l'espace de travail), ⧉ **Dupliquer** en pose une copie à côté. Un commentaire ne
  modifie rien d'autre.
- **Ordonner, verrouiller, dupliquer, supprimer** : la barre qui apparaît en bas, au-dessus du compositeur, dès qu'un élément est
  sélectionné, ou ] et [, Ctrl+L, Ctrl+D, Suppr. Le plan ne se supprime pas de l'espace de travail.
- **Se déplacer** : molette pour faire défiler, Ctrl + molette pour zoomer, espace + glisser
  ou l'outil Main (H) ; Ctrl+0 montre tout.
- **Aller plus loin** : double-clic sur le plan (ou « Modifier le plan ») : la vue zoome dans
  son cadre et le plan s'édite en grand, avec la 3D dans sa barre des vues ; **← Espace de travail**
  rend l'espace de travail à la même position et au même zoom ;
  double-clic sur un rendu pour le retrouver en grand dans Rendus.

**Annuler et rétablir agissent sur l'espace actif.** Dans l'espace de travail, Ctrl+Z et Ctrl+Maj+Z
(ou Ctrl+Y) défont et refont les gestes de l'espace de travail (déplacer, agrandir, ranger, écrire),
sans toucher au plan. Pendant l'édition du plan, les mêmes touches défont et refont les
gestes sur le plan, sans toucher à l'espace de travail. Les deux historiques sont séparés.

L'indicateur en haut à droite dit l'état réel : « Enregistrement… » pendant l'envoi,
« Enregistré » une fois la réponse reçue, « Échec de l'enregistrement » avec « réessayer »
sinon. Si l'espace de travail a été enregistré entre-temps ailleurs (un autre onglet), il est rechargé
et le message le dit : rien n'est écrasé en silence. L'espace de travail est gardé à part du plan
(`board.json` à côté de `project.json`) et fait partie de l'export « Archive (.zip) ».

## Pour aller plus loin, si le cœur vous en dit

- **Une seconde photo** de la même pièce (bouton **+** de la bande) : elle est comparée au plan
  toute seule et ajoute sa vue. Dans la fiche de la vue : **Garder cette photo** (le plan la
  suit), **Écarter cette photo** (elle reste, sans peser) et **Tourner la vue** (la caméra a été
  placée du mauvais côté d'une pièce symétrique : un quart de tour à chaque clic).
- **Paramètres avancés** (engrenage) : **Réglages** (unités d'affichage, le mètre par défaut ;
  nord du plan), **Assistant IA** (modèle, consentement, journal des envois), **État de
  l'installation**, **Diagnostic**, puis **Outils experts (rarement utiles)**, repliés : photos
  (fond du plan, vues, suppression, image d'inspiration), calibration détaillée (toutes les
  mesures, prévisualiser, facteur), Blender (fichier `.blend`, rendu depuis la photo), légende
  des provenances.
- **Meuble** (palette) : choisissez un meuble du catalogue (onze boîtes cotées, dont un
  rangement bas sous pente), **Ajouter au plan**, puis déplacez-le, tournez-le et cochez « à
  conserver » dans sa fiche. Le meuble apparaît aussi en 3D.
- **Aménager** (palette), en trois pas. **1 Votre souhait** : écrivez ce que vous voulez faire
  de la pièce (« chambre d'amis avec un coin bureau, le lit fait 160 × 200 »). **Voir ce qui
  partira** montre mot pour mot le texte qui serait envoyé (votre souhait + les cotes du plan,
  jamais la photo) et vers où (votre machine avec Ollama, ou Google si vous avez choisi
  Gemini et autorisé l'envoi : l'autorisation est demandée là, au moment d'envoyer).
  **Envoyer au modèle** rend le brief. **2 Le brief** : la liste des meubles du catalogue,
  leur importance et vos préférences, à relire et corriger ; il ne place rien, et ce qui a été
  écarté de la réponse du modèle est listé avec le motif. Sans modèle, cliquez **+ ajouter un
  meuble** pour composer le brief à la main.
- **3 Les dispositions** (**Proposer des dispositions**) : un moteur géométrique local, sans aucun
  modèle, place les meubles du brief dans la pièce et rend trois variantes sans collision,
  hors des débattements de porte et sous la hauteur libre. Chaque variante montre son score et
  une phrase (meubles placés, souhaits tenus ou manqués, remarques : fenêtre masquée, passage,
  dégagement). **Voir sur le plan** la dessine en pointillé orange ; rien n'entre dans le plan
  tant que vous ne cliquez pas **Appliquer cette variante** (meubles « générés », annulables,
  déplaçables ensuite comme les autres).
- **Image d'inspiration (locale)** (Outils experts › Photos) : une image générée sur la machine à
  partir de la profondeur (Stable Diffusion, poids installés ici) ; elle est marquée
  « générée », ce n'est jamais une mesure. Le panneau dit d'abord sur quoi elle sera calculée :
  **GPU** (une dizaine de secondes) ou **processeur** (plusieurs minutes). Pendant le calcul,
  « Génération en cours… » compte les secondes ; vous pouvez continuer à travailler, l'image
  apparaît toute seule à la fin.

## Ce qui ne marchera pas, et pourquoi

Avant de chercher : `uv run openplan doctor` dit ce qui est là (Python, dossier des projets,
front, port, GPU, Blender, poids, assistant IA, accès) et **la commande pour chaque manque**.
Le même bilan est dans les **Réglages** de la galerie (roue dentée en haut à droite, « État de
l'installation »).

| Sans… | Ce qui manque | Ce qui marche quand même |
|---|---|---|
| Blender (installé ici dans `~/opt/blender`) | Panneau Blender : fichier `.blend`, rendu photoréaliste « Rendre depuis la photo » (message clair « Blender introuvable ») | La 3D dans le navigateur (GLB), l'export GLB, la case « Matières » |
| Ollama ou Gemini configuré | Panneau **IA** : « aucun modèle configuré », les boutons Perception restent inactifs | Tout le reste : la profondeur, les combles, les plans, l'inspiration ne passent par aucun assistant |
| Poids de profondeur (`openplan models pull …`, déjà faits ici) | Distances et pentes de toit à l'import : le bandeau d'arrivée le dit une fois, avec la commande | Le plan depuis les lignes de la photo, tout le reste |
| Poids OWLv2 (`openplan models pull owlv2_base@1.0`, déjà faits ici) | Portes et fenêtres posées toutes seules : dit une fois avec la commande | Les ouvertures se dessinent à la main (palette) |
| Tesseract (`sudo apt install tesseract-ocr`) | Cotes lues sur l'image d'un plan : l'échelle vient de l'épaisseur des murs ou d'une porte, dit dans le bandeau | Recalibrer corrige l'échelle en un geste |
| Poids Stable Diffusion (licence acceptée ici) | **Image d'inspiration** répond « licence à accepter / poids absents » | Tout le reste |
| Carte graphique | Rien ne manque, tout est plus lent : construction du plan depuis une photo ≈ 1 min au lieu de 15 s, inspiration plusieurs minutes, rendu Blender ≈ 45 s | — |

Un modèle distant (Gemini) n'est jamais appelé sans avoir coché « autoriser les modèles
distants » puis confirmé le consentement (demandé dans la fiche Aménager au moment d'envoyer,
révocable dans Paramètres avancés › Assistant IA) ; la clé, si vous en mettez une, va
dans le fichier `.env` et nulle part ailleurs.

## Ce qui part, et où

La mention en bas de la galerie dit toujours l'état réel ; elle change dès qu'un réglage
change.

| Où tourne PlanDesign | Vos photos et plans | Votre texte | Rapports d'erreur |
|---|---|---|---|
| Sur votre machine (`make start`) | restent sur la machine | ne part pas, sauf case Gemini cochée : le texte seul part chez Google | jamais |
| En ligne (app.plandesign.fr) | gardés sur le serveur PlanDesign, nulle part ailleurs | idem : seulement avec la case Gemini, texte seul | un plantage envoie un rapport technique (type d'erreur, ligne de code, route) sans vos textes, photos, noms de projet ni chemins |

**Case Gemini** (Réglages de la galerie, ou compositeur) : en palier gratuit, Google peut
utiliser le texte envoyé pour améliorer ses produits. Les photos, les plans et les noms de
projet ne partent jamais par cette case. Décochée, le texte est lu par les phrases simples de
PlanDesign, sans service extérieur.

**Photos vers un modèle distant** : seulement si vous l'autorisez projet par projet dans
Paramètres avancés › Assistant IA, sans « texte seul » ; la bande des photos et l'écran
« Ajouter une source » le disent alors.

## Arrêter

Fermer la fenêtre noire (ou Ctrl+C dedans). Les projets restent dans `data/` du dépôt ; le
bouton « supprimer » d'un projet efface tout son dossier, photos comprises.
