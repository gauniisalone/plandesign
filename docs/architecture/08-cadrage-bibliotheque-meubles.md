# Cadrage : bibliothèque de mobilier (demande d'augus du 3 octobre 2026)

Note courte, avant tout code. Demande : remplacer le panneau Meuble par une vraie bibliothèque
(cartes illustrées, filtre par pièce, « choisissez un meuble, puis cliquez sur le plan ») et
montrer de vrais modèles 3D low-poly au lieu de boîtes (capture de référence
`data/bugs/2026-10-03-reference-bibliotheque-meubles.png`, hors git).

## 1. Source retenue : Kenney, « Furniture Kit »

- Page officielle : <https://kenney.nl/assets/furniture-kit> ; téléchargement
  `kenney_furniture-kit.zip` (5,1 Mo, SHA-256
  `e67652d0932cee41683f74711c03d3e192a2af9979ef8e6b237711f5482d46b0`), vérifié le 3 octobre 2026.
- **Licence : Creative Commons Zero (CC0 1.0)**, vérifiée trois fois : page de l'asset
  (« Creative Commons CC0 »), page d'aide de Kenney (« public domain licensed (CC0) …
  attribution is not required … even in commercial projects ») et fichier `License.txt` du zip
  (« License: (Creative Commons Zero, CC0) … free to use in personal, educational and commercial
  projects. Support us by crediting Kenney … (this is not mandatory) »). Aucune obligation
  d'attribution, usage commercial libre, redistribution libre : compatible avec le paquet
  `openplan-studio` et sans contrainte pour l'utilisateur. Le logo Kenney n'est pas utilisé.
- Contenu : 140 modèles (GLB, OBJ, FBX, DAE, STL) et 560 vignettes isométriques rendues depuis
  les modèles (215 × 190 px, quatre orientations). Style low-poly coloré, celui de la capture.
- Modèles mesurés : 100 à 800 faces, 7 à 50 Ko par GLB ; repère Y vers le haut ; **échelle non
  métrique** (lit double ≈ 0,96 × 1,13) : chaque modèle sera mis à l'échelle du meuble (largeur,
  profondeur, hauteur du catalogue, donc des cotes saisies), pas pris tel quel.
- Alternatives regardées et écartées : Quaternius (CC0 mais peu de mobilier d'intérieur),
  Poly Pizza (licences mélangées, CC-BY fréquent), Sketchfab (licences au cas par cas). Kenney
  est la seule source CC0 cohérente qui couvre cuisine et salle de bain.

## 2. Poids

Environ 40 modèles retenus : ≈ 0,9 Mo de GLB + 0,25 Mo de vignettes, soit **≈ 1,2 Mo** dans le
dépôt (`assets/furniture/`), très en dessous de 50 Mo. Le zip complet n'entre pas dans le dépôt.

## 3. Meubles proposés (40, dont les onze actuels)

| Pièce | Meubles (modèle Kenney) |
|---|---|
| Séjour | canapé 2 places (loungeSofa), canapé 3 places (loungeSofaLong), canapé d'angle (loungeSofaCorner), fauteuil (loungeChair), table basse (tableCoffee), meuble TV (cabinetTelevision), bibliothèque (bookcaseOpen), table et chaises (table), chaise (chair), table ronde (tableRound), lampadaire (lampRoundFloor), tapis (rugRectangle) |
| Chambre | lit double (bedDouble), lit simple (bedSingle), lit superposé (bedBunk), table de chevet (cabinetBedDrawer), armoire (bookcaseClosedDoors), commode (sideTableDrawers), rangement bas sous pente (bookcaseOpenLow) |
| Bureau | bureau (desk), bureau d'angle (deskCorner), chaise de bureau (chairDesk) |
| Cuisine | meuble bas (kitchenCabinet), meuble à tiroirs (kitchenCabinetDrawer), évier (kitchenSink), cuisinière (kitchenStove), réfrigérateur (kitchenFridgeLarge), réfrigérateur sous plan (kitchenFridgeSmall), meuble haut (kitchenCabinetUpper), comptoir de bar (kitchenBar), tabouret de bar (stoolBar) |
| Salle de bain | WC (toilet), douche (shower), baignoire (bathtub), lavabo (bathroomSink), meuble de salle de bain (bathroomCabinetDrawer), lave-linge (washer), sèche-linge (dryer) |

Chaque entrée garde le contrat actuel : dimensions min / défaut / max, dégagements, ancrage
(libre, dos au mur, mur d'égout), étiquette française, et gagne `room` (séjour, chambre, bureau,
cuisine, salle de bain), `model` (fichier GLB), `preview` (vignette) et `source` (Kenney + URL +
date de vérification). **Les onze identifiants actuels sont conservés** (`bed_double_160`,
`desk_140`…) : les projets existants restent valides sans migration ; ils reçoivent simplement
un modèle.

## 4. Impact

- **Licences** : registre `assets/furniture/SOURCES.yaml` (fichier → source, licence, URL,
  SHA-256 du zip, date) et `docs/licenses/README.md` ; test qui refuse un asset du catalogue sans
  licence acceptée (CC0 seulement pour un modèle tiers) ou sans entrée dans le registre, et un
  GLB sans source.
- **Scène et 3D** : le prisme d'un meuble est remplacé par le modèle, mis à l'échelle de sa boîte
  et tourné de son lacet (repère Y-up → Z-up) dans `scene3d` ; le GLB exporté, l'aperçu 3D du
  navigateur et le rendu Blender (import du GLB par meuble) montrent le même modèle. Les règles
  d'aménagement restent calculées sur la boîte (rien ne change pour les collisions).
- **Plan 2D** : contour du meuble + symbole simple par famille (lit : oreillers ; canapé :
  dossier et accoudoirs ; table : chaises ; WC, lavabo, douche, évier : formes usuelles des
  plans d'architecte), pas une boîte nue.
- **Vignettes** : les vignettes isométriques de Kenney, rendues depuis les mêmes modèles (CC0).
- **Panneau** : bibliothèque en cartes (vignette, nom, pièce), filtre par pièce, recherche ;
  carte choisie → clic dans le plan pour poser (accrochage au mur et règles en direct comme le
  glisser-déposer de 3.6) ; Échap annule.
- **Assistant, générateur, règles** : même catalogue ; les nouvelles catégories reçoivent des
  synonymes français (phrases-types et prompt).

## 5. Découpage

| Étape | Contenu |
|---|---|
| F1 | Catalogue élargi (40 entrées, onze identifiants gardés), modèles et vignettes dans `assets/furniture/`, registre des sources, test de licence, docs/licenses |
| F2 | Modèles dans la scène : `scene3d` (GLB exporté, aperçu 3D), Blender (import par meuble), tests de volume et de cohérence trimesh / Blender |
| F3 | Panneau bibliothèque : cartes, filtre, recherche, pose par clic avec accrochage et règles en direct, Échap ; scénario navigateur |
| F4 | Symboles 2D par famille dans le plan, le SVG et le PDF |
| F5 | Assistant, phrases-types, générateur et règles sur les nouvelles catégories ; essai réel ; docs |

## 6. Bilan (3 octobre 2026)

Livré en cinq commits (F1 c1d8e68, F2 306a1fa, F3 30a17c8, F4 5f13226, F5) : 34 meubles (et non
40 : comptoir de bar, tabouret, meuble haut de cuisine et tapis écartés, les deux derniers en
réserve), modèles low-poly dans la vue 3D, le GLB et Blender, bibliothèque en cartes avec pose
par clic, symboles 2D dans le plan, le SVG et le PDF.

**F5, même catalogue partout :**

- **Phrases-types** : synonymes français pour les 34 familles (évier, cuisinière, frigo, WC,
  douche, baignoire, lavabo, meuble vasque, lave-linge, table basse, lits superposés…) ; une
  demande de dispositions garde tous les meubles nommés.
- **Repère « coin »** (`corner`) : « ajoute une douche dans le coin » vise un angle de la pièce
  (à 30 cm près), compris des phrases-types, du prompt et du générateur.
- **Usage de la pièce** : le type de la pièce (ou, à défaut, son nom : « Cuisine », « Salle de
  bain »…) écarte les familles hors usage, avec la raison, dans le générateur et l'assistant :

  | Usage | Familles écartées | Exception |
  |---|---|---|
  | chambre, séjour, bureau | cuisine, salle de bain | — |
  | cuisine | chambre, salle de bain | lave-linge, sèche-linge |
  | salle de bain | chambre, cuisine, séjour, bureau | — |
  | grenier, couloir, autre, inconnu | rien | — |

  La pose à la main (bibliothèque) reste libre : l'utilisateur décide.
- **Modèle** : la catégorie écrite avec un mot français (« wc ») est rattachée par les
  synonymes ; un repère écrit par l'utilisateur et oublié par le modèle est complété depuis la
  phrase. Essai réel : `docs/evaluation/protocol.md` §3 quindecies.

**Réserve** : vignettes servies avec jeton (fetch + URL blob), choix de la taille avant de poser,
meuble haut (pose en hauteur), tapis (objet au sol traversable).
