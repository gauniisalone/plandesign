# Parcours Upload → Edit → Render : note de cadrage (27 septembre 2026)

Réponse aux trois directives d'augus du 27 septembre 20:56 (relayées par le PO) : le fil
conducteur de l'interface devient **Upload → Edit → Render**, les outils d'expert passent
dans **Paramètres avancés**, le panneau latéral est allégé. Cette note ne change aucun code :
elle propose, augus valide. Elle s'appuie sur la maquette Lovable d'augus
(`flatplan-studio.lovable.app`, appliquée le 27 sept. 18:47 : accueil « Créez votre OpenPlan »
avec zone de dépôt, atelier à canevas plein écran, palette flottante, aperçu 3D en volet).
La maquette couvre **Upload** et **Edit** ; **Render** n'y figure pas encore, il est proposé ici.

## 1. Le fil en trois temps

Un bandeau d'étapes, toujours visible en haut de l'atelier : **1 Importer · 2 Éditer · 3
Rendre** (libellés français ; « Upload / Edit / Render » en anglais si l'on garde la langue de
la maquette). L'étape courante est colorée, les autres cliquables. Le projet mémorise l'étape
atteinte ; l'accueil rouvre un projet à sa dernière étape.

### Écran 1 : Importer (accueil, existe)

- Zone de dépôt : **une photo** (JPG, PNG, WEBP) **ou un plan 2D existant** (image, PDF, SVG,
  DXF ; cadrage §4). Le fichier est reconnu à l'import et le projet part sur la bonne piste.
- Photo → le moteur devine : proposition de plan depuis la photo (points de fuite) puis, si
  les poids sont installés, plan depuis la profondeur ; le résultat est **estimé** et le dit.
- Plan 2D → le plan devient un fond à l'échelle ; les murs sont proposés quand le format le
  permet (DXF, PDF vectoriel), sinon tracés par-dessus le fond.
- Sous la zone : « Créer un projet vide » (tracer soi-même), puis les projets existants.
- Rien d'autre : pas de hauteur de photo, pas de profondeur, pas de combles à cet écran. La
  seule question posée, après une photo, est **« À quelle hauteur teniez-vous l'appareil ? »**
  avec une valeur par défaut (1,50 m) et une explication d'une ligne.

### Écran 2 : Éditer (atelier, existe)

- Canevas plein écran, palette flottante : sélection, main, mur, porte, fenêtre, pièce
  (détecter), meuble (catalogue), annuler, rétablir, supprimer.
- Barre du bas : zoom, recadrer, **Aperçu 3D** (volet droit), exporter, copier le lien.
- Panneau de droite = **Inspecteur contextuel** seulement : ce qui est sélectionné (mur,
  ouverture, pièce, meuble, sommet, photo) et, sans sélection, le niveau (hauteur, unités) et
  la liste des vérifications. Le catalogue de meubles s'ouvre depuis l'outil « meuble ».
- **Photos** : vignettes en bas à gauche du canevas (bande), une vignette = fond du plan par
  un clic, « voir dans le plan » par un autre. Les actions d'analyse quittent le panneau.
- **Aménager** (rôle Intention + générateur) : une action de la palette « Aménager… » qui
  ouvre une fiche en trois pas (souhait → brief à relire → variantes à appliquer). C'est un
  sous-parcours d'Éditer, pas un panneau permanent. Les garde-fous restent (aperçu de ce qui
  part au modèle, consentement distant, rien n'entre sans « Appliquer »).
- **Paramètres avancés** : bouton engrenage dans la barre du haut, fermé par défaut (§3).

### Écran 3 : Rendre (nouveau)

- Une liste de **points de vue** : les caméras des photos (existent déjà dans la scène) et des
  caméras libres que l'on pose dans le plan (position + visée, par glisser). « Ajouter un point
  de vue » dans la palette de cet écran.
- Pour chaque point de vue : vignette (viewer 3D, immédiat), puis **Rendu réaliste** (Blender,
  qualité rapide / normale / fine, GPU si disponible, temps annoncé). Les rendus s'empilent
  dans une galerie, téléchargeables, avec leur point de vue et leur date.
- Options visibles : plafonds et toits, matières (bois, pierre, enduit), heure du jour
  (éclairage). L'image d'inspiration (diffusion locale) devient une option de cet écran,
  marquée « générée, pas une mesure ».
- Ce qui existe déjà côté moteur : `render_photo_view` (rendu par la caméra d'une photo),
  matières procédurales, GPU. Ce qui manque : rendu depuis une caméra libre (généralisation
  de la même fonction), galerie, pose de caméra dans le plan (l'élément `Camera` existe).

## 2. Inventaire des outils actuels et destination

Légende : **reste** (chemin principal, même endroit), **déplacé** (nouvel emplacement),
**avancé** (panneau Paramètres avancés), **supprimé**.

| Outil aujourd'hui | Où | Destination | Pourquoi |
|---|---|---|---|
| Sélection, main, mur, porte, fenêtre | palette | reste | cœur d'Éditer |
| Détecter les pièces | palette | reste, renommé « Pièces » | un clic, résultat visible |
| Annuler, rétablir, supprimer la sélection | palette | reste | |
| Zoom −/%/+, recadrer | barre du bas | reste | |
| Plan / Plan + 3D / 3D, Afficher la 3D | barre du bas | déplacé : un seul bouton **Aperçu 3D** (volet) ; le plein écran 3D devient l'écran Rendre | trois modes pour un besoin |
| Exporter (SVG, PDF, DXF, GLB, JSON, JSON à partager) | barre du bas | reste (Éditer) ; PDF/DXF/SVG aussi en fin de Rendre | |
| Copier le lien | barre du bas | reste | |
| Plafonds et toits, Matières | en-tête du volet 3D | reste dans le volet ; aussi dans Rendre | |
| Niveau : hauteur, unités | inspecteur sans sélection | reste | |
| Calibration (mesure d'un mur, prévisualiser, calibrer) | inspecteur | déplacé : « Longueur réelle » dans la fiche du mur sélectionné applique directement ; prévisualiser et facteur → avancé | un non-expert saisit une longueur, c'est tout |
| Vérifications (violations) | inspecteur | reste, réduit à un compteur cliquable dans la barre du bas + liste dans l'inspecteur | |
| Légende Provenance | inspecteur | déplacé : infobulle « ? » près du compteur de vérifications | encombrant en permanence |
| Catalogue de meubles, Ajouter au plan | inspecteur | déplacé : outil « Meuble » de la palette (fiche flottante) | |
| Fiche Sommet / Mur / Ouverture / Pièce / Meuble / Vue | inspecteur | reste (contextuel) | |
| Import de photo (fichier), hauteur photo | panneau Photos | déplacé : dépôt à l'écran Importer et sur le canevas ; hauteur demandée une fois après import | |
| Recaler les combles sur cette hauteur | panneau Photos | avancé | |
| Mettre en fond du plan / retirer, réglages du fond (échelle, position, rotation, opacité) | panneau Photos | déplacé : bande de vignettes ; réglages du fond dans la fiche « Photo » de l'inspecteur | |
| Proposer un plan depuis la photo / Confronter cette photo à la scène | panneau Photos | déplacé : automatique à l'import (Importer) ; « Confronter » dans la fiche Photo | |
| Garder cette photo / Écarter / Réintégrer / Tourner la vue | fiche vue | reste dans la fiche Photo, sous « Cette photo contredit le plan » quand c'est le cas seulement | |
| Vue précédente / suivante, Voir dans le plan | panneau Photos | déplacé : bande de vignettes | |
| Plan depuis la profondeur | panneau Photos | déplacé : automatique à l'import si poids présents, sinon avancé | |
| Profondeur (carte, artefacts) | panneau Photos | avancé | diagnostic |
| Combles (estimation + formulaire + Créer les combles) | panneau Photos | avancé, avec un raccourci « Cette pièce est sous les toits » dans le niveau qui ouvre le formulaire | expert, mais utile au grenier d'augus |
| Portes et fenêtres (OWLv2) | panneau Photos | déplacé : automatique à l'import si poids présents ; relance depuis la fiche Photo | |
| Image d'inspiration | panneau Photos | déplacé : écran Rendre | |
| Assistant IA : statut, consentement, journal réseau | panneau IA | avancé (consentement rappelé au moment d'un envoi distant) | |
| Analyser (Perception), Appliquer la hauteur, Créer le rectangle | panneau IA | avancé | résultats faibles en local (protocole §3 bis) |
| Intention : souhait, aperçu, envoi, brief, + ajouter un meuble, Proposer des dispositions, variantes | panneau IA | déplacé : fiche « Aménager » en trois pas depuis la palette | |
| Blender : construire .blend, rendu depuis la photo, qualité, GPU | panneau Blender (mode 3D) | déplacé : écran Rendre | |
| Barre d'état du canevas (coordonnées, aide) | bas du canevas | reste | |
| Raccourcis clavier V H W D F, Suppr, Ctrl+Z/Y | atelier | reste, listés dans l'infobulle « ? » | |

Aucun outil n'est supprimé : tout reste atteignable, mais quinze actions quittent le chemin
principal.

## 3. Paramètres avancés

- Accès : engrenage dans la barre du haut ; fermé par défaut ; l'ouverture est mémorisée par
  navigateur (préférence), pas dans le projet.
- Contenu, par blocs repliés : **Photo** (hauteur de prise de vue, recaler l'échelle,
  profondeur, plan depuis la profondeur, portes et fenêtres, confrontation des vues) ;
  **Combles** (estimation et formulaire) ; **Calibration** (prévisualisation, facteur, RMS,
  mesures) ; **Assistant IA** (modèle configuré, consentement, journal réseau, perception) ;
  **Données** (provenance et confiance de l'élément sélectionné, artefacts des étapes).
- Règle : un outil avancé produit toujours un résultat visible dans Éditer (murs, ouvertures,
  pentes), jamais un état caché.

## 4. Import d'un plan 2D existant : cadrage

Objectif : partir d'un plan que l'utilisateur possède déjà et arriver au même point qu'après
une photo, avec une **échelle** et des **murs** dans la scène.

| Format | Ce qu'on en tire | Avec quoi | Licence | Fiabilité attendue |
|---|---|---|---|---|
| Image (JPG, PNG, WEBP : scan, capture, photo de plan) | fond de plan redressé ; échelle par **une cote saisie** (deux clics sur le plan + longueur) ou par une cote lue ; murs à tracer par-dessus (existe), puis vectorisation heuristique en second temps | Pillow/OpenCV (redressement, seuillage, squelette des traits épais) ; Tesseract pour lire les cotes | Pillow HPND, OpenCV Apache-2.0, Tesseract Apache-2.0 | échelle exacte avec une cote saisie ; murs auto « estimés », à corriger |
| PDF vectoriel (export d'un logiciel) | segments de murs (lignes épaisses parallèles), cotes texte → échelle automatique | pdfplumber / pdfminer.six (extraction des tracés et du texte) | MIT | bonne : les murs sont des tracés |
| PDF raster (scan) | comme une image | pdfplumber → image | MIT | comme l'image |
| SVG | tracés → murs comme le PDF vectoriel | analyse XML (bibliothèque standard) | — | bonne |
| DXF | LINE / LWPOLYLINE des calques de murs → murs ; cotes DIMENSION → échelle | ezdxf (déjà utilisé pour l'export) | MIT | très bonne ; calques à choisir dans une liste |

- **Aucun modèle appris** dans la première version : l'heuristique et les formats vectoriels
  couvrent l'essentiel, sans poids ni licence à négocier. Un modèle de reconnaissance de plans
  ne serait ajouté qu'après vérification de sa licence au moment du choix (les jeux de données
  connus, CubiCasa5k par exemple, sont non commerciaux : à écarter ; PyMuPDF est AGPL : écarté).
- Provenance : murs importés d'un vecteur = `observed` (relevés d'un document, pas mesurés
  par nous), murs vectorisés d'une image = `estimated`, échelle par cote saisie =
  `user_defined`, par cote lue = `observed` avec la valeur lue affichée pour contrôle.
- Critères : fixture « plan PDF vectoriel » (4 murs, une cote) → 4 murs et échelle à 1 % ;
  fixture « scan de plan » → échelle exacte après une cote saisie, fond aligné ; DXF d'un
  logiciel courant → murs importés sans intervention.
- Tailles : 50 Mo comme les photos ; PDF limité à la première page choisie.

## Décision (augus, 27 septembre 2026, 21:10)

Note validée. 1) Libellés français « Importer · Éditer · Rendre ». 2) Rendre : Blender seul
d'abord ; l'inspiration reste dans Paramètres avancés puis rejoindra Rendre. 3) Import de plan :
fond + cote saisie + DXF / PDF vectoriel, sans modèle ; vectorisation d'image ensuite. 4) La
fiche « Aménager » remplace le panneau IA ; consentement au moment de l'envoi distant, journal
réseau dans Paramètres avancés. 5) Ordre **E1 → U1 → R1**. Exigences : aucune fonction perdue,
un scénario navigateur « non-expert » par bloc, liste des outils déplacés à chaque livraison.

### E1 livré (27 septembre 2026)

Inspecteur contextuel, outils Meuble et Aménager dans la palette, « Calibrer le plan » dans
la fiche du mur, bande de vignettes, compteur de vérifications, Paramètres avancés (Photo,
Calibration, Assistant IA, Données). Écarts assumés par rapport au §2, à reprendre en U1 :
les réglages du fond (opacité, largeur, position, rotation) et la suppression d'une photo
restent dans Paramètres avancés › Photo (pas encore de fiche « Photo » dans l'inspecteur) ;
le mode 3D plein écran reste dans la barre du bas jusqu'à l'écran Rendre (R1).

### U1 livré (28 septembre 2026)

Bandeau des trois temps (navigable : « Importer » rouvre l'écran d'import du projet courant,
exigence PO), écran Importer du projet, import DXF (unités, double trait, calques, murs
observés), outil Cote guidé (deux clics + longueur, aperçu immédiat de l'échelle, alerte si
valeur aberrante, exigence PO). Reste pour U1b : PDF vectoriel et SVG (même structure
`PlanSegment`), vectorisation heuristique d'une image de plan. « Rendre » ouvre pour l'instant
la 3D plein écran avec le panneau Blender ; l'écran complet est R1.

### R1 livré (28 septembre 2026)

Écran Rendre conforme au §1 : points de vue (caméras des photos + points de vue libres posés
au glisser dans le plan, rond = position, pointe = visée), vignette du viewer 3D par point de
vue (sans Blender), rendu Blender par point de vue en tâche de fond (qualité rapide / normale /
fine, durée et périphérique annoncés avant de lancer, progression), galerie (point de vue,
date, téléchargement). Options d'éclairage (heure du jour) non faites ; inspiration hors
périmètre (décision d'augus) et restée dans Paramètres avancés › Photo ; l'ancien panneau
Blender (.blend, rendu depuis la photo) est dans Paramètres avancés › Blender.

### U1b livré (28 septembre 2026)

PDF vectoriel (pdfplumber MIT ; échelle papier lue ou devinée, calques par épaisseur de
trait, murs pleins), SVG (tracés droits, transformations, groupes nommés, unités physiques),
vectorisation heuristique d'une image de plan mise en fond et calée avec l'outil Cote (murs
estimés). Le §4 est couvert, sans modèle appris. Cotes texte lues comme échelle : non (les
cotes « 3,20 » près des traits ne servent pas encore) ; PDF scanné = image.

### R1b livré (28 septembre 2026) : éclairage

Option « heure du jour » de l'écran Rendre : préréglages matin / midi / soir / nuit (PO :
lisibles, pas une heure numérique), nord du plan réglable (en haut par défaut) avec boussole
et phrase d'orientation ; le soleil suit le nord. Neutre = l'éclairage d'atelier d'avant.

### Correctif superpositions (28 septembre 2026, 13:24 → 13:49)

Bug d'augus (`data/bugs/2026-09-28-1300-superpositions-trait-violet.png`). Le trait violet
était l'aiguille de la boussole tirée vers le coin : le serveur du port 8000 tournait encore le
code Python de la veille (pas de `north_deg` → `NaN`) ; serveur relancé, boussole robuste.
Disposition : une seule rangée en haut (retour · titre tronqué avec infobulle · 1 Importer /
2 Éditer / 3 Rendre · panneau · réglages), palette dessous, barre du bas à droite, bande des
photos à gauche, boussole en haut à droite ; sous 1 150 px de scène la bande monte et les
libellés se compactent, sous 820 px le titre disparaît, sous 760 px les étapes gardent leur
numéro (le libellé reste dans le nom accessible). « Recadrer » place le plan dans la zone
libre. Scénario navigateur 1366 × 768 : zones deux à deux disjointes, sommets et boussole hors
des barres. Aucune fonction déplacée ni retirée. L'écran Importer gagne « Plan depuis la
profondeur (recalé) » pour les photos sans focale (échelle ×3 du grenier).

### 3.5 livré (28 septembre 2026) : critique d'une variante

Dans la fiche Aménager, chaque variante a « Avis sur cette variante ». Les remarques viennent
d'abord des règles locales (gratuites, sans modèle : violations géométriques, souhaits du brief
non tenus), puis du modèle s'il est configuré et autorisé (texte seul, consentement par projet
comme pour le brief). Exigences PO tenues : chaque remarque cite le meuble et la règle, la liste
est triée par importance, et une action en un clic (« Déplacer X loin d'une fenêtre »,
« Retirer X ») recalcule une variante révisée qui remplace l'ancienne dans la liste, en fantôme,
à relire ; rien n'entre dans le plan sans « Appliquer cette variante ». Aucun outil déplacé.

### 3.6 livré (28 septembre 2026) : meubles déplaçables, comparaison côte à côte

Dans le plan, un meuble se déplace à la souris (outil Sélection) : son dos s'accroche au mur
le plus proche et il se tourne face à la pièce (Alt pour poser librement) ; le rond devant le
meuble le fait tourner par pas de 15°. Pendant le geste, les règles d'aménagement sont
vérifiées en direct : le meuble rougit et dit la règle dure en deux mots ; au dépôt, une
position invalide n'est pas posée et le plan l'écrit (« Non posé : meuble dans un mur »).
Dans la fiche Aménager, « comparer » dessine une seconde variante en vert à côté de la
variante orange et ouvre un tableau côte à côte. Aucun outil déplacé. Le déplacement dans la
3D n'est pas fait (la 3D reste un aperçu).

### 3.7 livré (28 septembre 2026) : vignette 3D par variante

Sous chaque variante de la fiche Aménager, « Vignette 3D » demande à Blender une image de la
variante sans l'appliquer (copie de la scène le temps du rendu) : point de vue depuis un coin
de la pièce, grand angle, 640 × 480, une trentaine de secondes au plus sur processeur, moins de
dix secondes sur GPU ; la durée est annoncée, la page reste utilisable, l'image apparaît sous la
variante et dans la galerie de l'écran Rendre (« Variante n »). Sans Blender, la fiche le dit
et l'aperçu 3D du plan reste. Aucun outil déplacé.

## 5. Questions à trancher (tranchées ci-dessus)

1. Libellés : « Importer · Éditer · Rendre » ou « Upload · Edit · Render » ?
2. Rendre : commencer par Blender seul (existe) ou inclure d'emblée l'image d'inspiration ?
3. Import de plan : première version avec **fond + cote saisie + DXF/PDF vectoriel** (sans
   modèle), la vectorisation d'image en second ; d'accord ?
4. La fiche « Aménager » en trois pas remplace le panneau IA : le journal réseau et le
   consentement ne sont plus visibles en permanence, seulement au moment d'un envoi distant et
   dans les paramètres avancés. Acceptable ?
5. Ordre de réalisation proposé : **U1** bandeau des trois étapes + import de plan (fond +
   cote + DXF/PDF) → **E1** inspecteur contextuel, bande de vignettes, paramètres avancés,
   fiche Aménager → **R1** écran Rendre (points de vue, galerie, caméras libres). Chaque bloc
   livré séparément avec ses tests et son scénario navigateur.

## Import automatique (29 et 30 septembre 2026)

Directives d'augus du 29 septembre 2026 (17:40), qui remplacent les lignes du §2 et du §3 sur
les analyses de photo : **le plan est déjà construit en arrivant sur Éditer**, quelle que soit
l'entrée, **aucun bouton d'analyse**, **un seul outil de correction : Recalibrer**, Paramètres
avancés réduits. Livré en cinq sous-étapes (A1 → A5, commits `e9fa3c1`, `989e082`, A4, A5).

- **Chaîne automatique** (`openplan/pipeline/autoplan.py`, `POST …/images/{id}/autoplan`,
  tâche de fond à étapes lisibles). Photo : lignes de fuite → candidat ; distances (si poids) →
  candidat recalé sur un appareil supposé à 1,50 m ; combles si deux pentes opposées sont vues,
  sinon plafond plat (hauteur estimée) ; le meilleur candidat par score est appliqué, l'autre
  gardé en secours dans le run ; portes et fenêtres ensuite. Image de plan (fond clair, peu
  saturé) : redressement (Hough), échelle devinée dans l'ordre cotes lues par Tesseract →
  épaisseur des murs ≈ 20 cm → largeur de porte ≈ 0,80 m mesurée sur l'encre → taille
  plausible ; murs `estimated`. Ce qui manque (poids, Tesseract) est dit une fois avec la
  commande, jamais bloquant. Mesuré sur la machine d'augus : 7 à 16 s sur GPU pour une photo,
  0,3 s pour une image de plan.
- **Front** : tout fichier déposé (accueil, Importer, bande des photos, canevas) construit le
  plan ; progression avec durée annoncée, navigation possible pendant le calcul ; arrivée sur
  Éditer recadrée ; bandeau « Plan estimé » avec **Recalibrer** et « ce n'est pas une photo /
  un plan » (relecture imposée, tant que rien n'a été modifié) ; plans DXF / PDF / SVG appliqués
  tout de suite, correction après coup des calques et de l'échelle papier dans un bloc replié
  de l'écran Importer.
- **Recalibrer** (`POST …/recalibrate`, `pipeline/recalibrate.py`) : deux points du plan, un
  mur ou la hauteur sous plafond ; murs, ouvertures, meubles, vues et fond mis à l'échelle
  ensemble ; facteur annoncé, geste utilisateur annulable ; remplace l'outil Cote du fond. La
  saisie d'une longueur réelle dans la fiche d'un mur reste.
- **Paramètres avancés** : Réglages (unités, nord), Assistant IA, État de l'installation,
  Diagnostic (replié : étapes, méthode gardée et secours), puis « Outils experts (rarement
  utiles) » replié en dernier (photos et vues, calibration détaillée, Blender, légende).
- **Retiré** : les sept boutons d'analyse, la question sur la hauteur de l'appareil, le
  formulaire des combles (les pentes se corrigent par leur fiche : égout, inclinaison,
  faîtage), les rapports d'analyse (remplacés par Diagnostic), l'outil Cote.
