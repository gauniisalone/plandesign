# Protocole d'évaluation

Version 0.1, 23 septembre 2026. Chaque métrique porte un statut : **implémentée** (un test
la calcule et échoue si le seuil est dépassé) ou **à implémenter**. Aucune valeur n'est donnée
sans mesure réelle ; les cases vides le restent tant que la fixture n'existe pas.

## 1. Métriques

### Détection et caméra (une photo → repère, pose, murs)

| Métrique | Définition | Seuil | Statut | Où |
|---|---|---|---|---|
| Erreur de tangage | \|pitch estimé − vrai\| | ≤ 2° | implémentée | `tests/integration/test_heuristic_fixture.py::test_camera_pose_within_two_degrees` |
| Erreur de roulis | \|roll estimé − vrai\| | ≤ 2° | implémentée | idem |
| Erreur de lacet | \|yaw estimé − vrai\| modulo 90° (les axes X/Y du plan sont interchangeables) | ≤ 2° | implémentée | idem |
| Erreur de focale | \|f estimée / f vraie − 1\| | ≤ 10 % | implémentée | idem |
| Distance aux murs vus | \|d estimée / d vraie − 1\| pour chaque arête sol-mur détectée, caméra à sa vraie hauteur | ≤ 15 % | implémentée | `::test_floor_edges_give_wall_distances`, `::test_propose_floorplan_places_estimated_and_inferred_walls` |
| Rappel des murs visibles | murs visibles retrouvés / murs visibles | à définir | à implémenter (une seule fixture, deux murs visibles, tous deux retrouvés) | — |
| Portes et fenêtres localisées (2.4a) | IoU entre la boîte OWLv2 et la boîte projetée de l'ouverture vraie, sur un rendu habillé (chambranle, vitre, vantail) | ≥ 0,5 | implémentée : fenêtre 0,84 (score 0,54), porte 0,88 (score 0,72) ; §3 octies | `tests/integration/test_openings_fixture.py` |
| Largeur des ouvertures (2.4b) | largeur de la baie projetée sur la face du mur hôte par la caméra de la photo (vraie pose), rendu habillé + OWLv2 | ±15 % | implémentée : fenêtre 1,30 m pour 1,20 (+8 %), position −5 cm, appui −4 cm ; §3 octies | `tests/integration/test_openings_fixture.py::test_window_width_is_recovered_from_a_render` |
| Projection géométrique des boîtes (2.4b) | boîte exacte des coins vrais → largeur, position, appui, hauteur (fenêtre de biais, porte à 65°) | ≤ 5 % | implémentée : fenêtre ≤ 3 %, appui ≤ 4 cm, porte ≤ 5 % | `tests/unit/test_openings_step.py` |
| Rappel des ouvertures sur photos réelles (2.4b) | ouvertures gardées / ouvertures réelles, `fenetre_largeur` et `porte_largeur` dans `openplan evaluate` | rappel publié | à mesurer dès `data/eval` | `pipeline/evaluate.py` (lignes « ouvertures ») |
| IoU du polygone de pièce | intersection/union entre la pièce proposée et la vérité, après calibration sur un mur | ≥ 0,7 (objectif) | à implémenter | — |
| Intention : briefs valides (3.3) | dix souhaits en français → `LayoutBrief` valide après au plus un réessai, modèle local | 10 / 10 (publié tel quel sinon) | implémentée : `qwen2.5vl:3b` 10 / 10, 0 réessai, 44 s ; §3 nonies | `tests/unit/test_intention.py` (modèle simulé) + essai réel |
| Intention : rien d'inventé (3.3) | brief piégé « la pièce fait 8 m sur 6, lit de 3 m » | aucune dimension de pièce ni de meuble hors du texte | implémentée : tenu (aucune dimension renvoyée) ; positions et dimensions non écrites écartées avec motif (test) | idem |
| Générateur : variantes valides (3.4) | brief « lit double + bureau + armoire + deux chevets » sur la chambre de la fabrique → trois variantes distinctes (un meuble indispensable bouge d'au moins 0,5 m), aucune violation dure une fois appliquées, temps | 3 / 3 en < 5 s | implémentée : 3 / 3 en moins d'une seconde ; §3 decies | `tests/unit/test_layouts.py` |
| Temps de traitement | détection + proposition sur une image 1280 × 960, CPU | < 5 s | à implémenter (mesuré à la main : < 1 s) | — |
| Mur de face depuis une seconde vue | distance caméra 2 → mur de face déjà estimé, la caméra 2 étant placée par la scène | ≤ 10 % | implémentée | `tests/integration/test_multiview_fixture.py` |
| Mur inféré confirmé par une seconde vue | largeur entre les deux murs latéraux vus par la vue 2 (le mur gauche, inféré par la vue 1, est déplacé et passe `estimated`) | ≤ 20 % (biais connu du détecteur, §3 sexies) | implémentée | idem |
| Vue incohérente signalée | même vue à une hauteur de caméra fausse de 30 % → violation `inconsistent_views`, murs estimés intacts | exact | implémentée | idem, `tests/unit/test_multiview.py` |
| Plan depuis la profondeur, pièce en L | IoU entre le polygone proposé depuis la profondeur (hauteur de prise de vue saisie) et la vérité | ≥ 0,7 (2.3) | implémentée : profondeur exacte (bruit 1 %) IoU 0,97 ; rendu Blender + Depth Anything IoU 0,77 (§3 septies) | `tests/unit/test_depthplan.py`, `tests/integration/test_depthplan_fixture.py` |
| Profondeur apprise vs vérité (2.2) | corrélation de Spearman entre Depth Anything et la passe Z Cycles du même rendu ; rapport d'échelle médian publié | ≥ 0,9 | implémentée : 0,998, échelle ×1,50 (§3 septies) | `tests/integration/test_depthplan_fixture.py` |
| Murs vus depuis la profondeur | distance caméra → mur vu, pièce rectangulaire | ≤ 3 % sur profondeur exacte | implémentée | `::test_observe_rectangular_room_finds_floor_and_visible_walls` |

### Combles depuis la profondeur (plans sol / pentes / pignon)

| Métrique | Définition | Seuil | Statut | Où |
|---|---|---|---|---|
| Hauteur de prise de vue | \|h estimée / h vraie − 1\| (distance caméra → plan du sol) | ≤ 6 % sur profondeur exacte ; ≤ 30 % sur profondeur apprise | implémentée | `tests/unit/test_planes.py::check_attic`, `tests/integration/test_attic_fixture.py` |
| Hauteur au faîtage | idem, intersection des deux versants | ≤ 6 % (profondeur exacte) | implémentée | `test_planes.py` |
| Largeur entre égouts | idem, entre les deux murs latéraux (ou leur symétrique déduit) | ≤ 6 % | implémentée | idem |
| Distance au pignon | idem, le long du faîtage | ≤ 6 % | implémentée | idem |
| Pente des versants | \|angle estimé − vrai\| | ≤ 3° | implémentée | idem |
| Largeur utile (> 1,80 m) | idem | ≤ 6 % | implémentée | idem |
| Sans sol visible | aucune estimation, avertissement | — | implémentée | `::test_attic_without_floor_yields_empty_estimate` |
| Recalage sur hauteur saisie | toutes les longueurs × (h saisie / h estimée), source `user_defined` | exact | implémentée | `::test_rescale_on_user_camera_height`, `test_api.py::test_attic_route_with_synthetic_depth` |

### Calibration et géométrie

| Métrique | Définition | Seuil | Statut | Où |
|---|---|---|---|---|
| Facteur d'échelle, une mesure | mur de 4 u mesuré 3,10 m → ×0,775 | exact à 1e-9 | implémentée | `tests/geometry/test_scale.py` |
| Résidus, n mesures | RMS des résidus et marquage des mesures incohérentes (> 3 × tolérance) | 1 mesure incohérente sur 3 détectée | implémentée | idem |
| Aire d'une pièce | intérieur des murs, 4 × 3 m, murs 0,15 m → 10,97 m² | 1e-6 | implémentée | `tests/geometry/test_wallgraph.py` |
| Volume 3D | volume Blender = volume trimesh, pièce droite et combles (polyèdres) | 1 % | implémentée (si Blender présent) | `tests/integration/test_blender_bridge.py` |
| Murs sous pente | pignon découpé par deux versants = profil analytique (rectangle + triangle) × épaisseur ; mur d'égout rasé par la pente ; panneau de toit = longueur × rampant × épaisseur | 0,1 % | implémentée | `tests/unit/test_attic_scene.py::test_bake_clips_gables_under_the_roofs` |
| Ligne 1,80 m | distance égout → hauteur utile = (1,80 − égout) / tan(pente) ; 0 si l'égout dépasse 1,80 m ; absente si le faîtage est sous 1,80 m | exact | implémentée | `::test_derived_roofs_and_svg_usable_line` |
| Surface utile sous pente | aire de la pièce moins les bandes sous 1,80 m = analytique sur les combles de test | 0,1 % | implémentée | `tests/unit/test_layout.py::test_usable_area_of_the_attic_room` |
| Meuble sous pente | un objet plus haut que la hauteur libre à l'un des coins de son emprise est signalé ; accepté au centre ; contrainte souple ou désactivée respectée | exact | implémentée | `::test_furniture_under_slope_is_flagged` |

### Qualité d'image

| Métrique | Définition | Seuil | Statut | Où |
|---|---|---|---|---|
| Flou (rapport de re-floutage) | image réduite à 1024 px ; par direction, variance de la dérivée seconde après un flou gaussien de 1 px, rapportée à celle de l'image ; on garde la pire direction (un bougé ne se voit que dans une) ; indépendant de la texture | net < 0,30 < flou | implémentée | `tests/unit/test_quality.py` |
| Seuil de flou sur photos réelles | mesuré le 24 sept. 2026 (§3 quinquies) sur deux photos de smartphone et le rendu de la fixture, nettes puis floutées | nettes ≤ 0,13 ; gaussien 1,5 px ≥ 0,32 ; bougé 8 px ≥ 0,32 | validée sur 3 images (pas encore un jeu de photos réellement floues) | §3 quinquies |
| Variance du laplacien | conservée dans `quality` à titre indicatif : dépend de la texture (80 sur le rendu net, 2 200 sur un mur de pierre) et ne voit pas un bougé sur une photo texturée | aucun (plus de seuil) | remplacée | — |

## 2. Fixtures

| Fixture | Contenu | Vérité terrain | Statut |
|---|---|---|---|
| `tests/fixtures/rect_room/` | pièce 4 × 3 × 2,5 m, murs 0,15 m, une porte, une fenêtre, sol carrelé 0,4 m ; rendu Cycles 1280 × 960 depuis (0,7 ; 0,6 ; 1,4), lacet 50°, tangage −12°, focale 900 px | `scene.json` (projet), `camera.json` (`RenderCamera`) | **produite** (`uv run python -m tests.fixtures.rect_room.render`) |
| pièce rectangulaire, roulis 5° | même scène, 960 × 720, focale 675 px, roulis +5° ; rendue à la volée par le test (32 échantillons, GPU si présent, 2 s), rien de commis | `RenderCamera` dans le test | **produite** (`tests/integration/test_fixture_roll.py`, ignoré sans Blender) |
| pièce en L synthétique, profondeur exacte | `tests/synthetic_depth.py::render_depth_union` : union de deux cellules convexes [0,4]×[0,3] ∪ [2,6]×[0,6], hauteur 2,5 m, caméra en (1 ; 0,5 ; 1,4), lacet 45°, tangage −12°, focale 500 px ; le rayon traverse d'une cellule à l'autre (vérifié contre une marche brute) | géométrie exacte | **produite** (25 sept. 2026) |
| pièce en L rendue (Blender) | même pièce rendue à la volée par Blender avec passe de profondeur, Depth Anything dessus, plan proposé : Spearman 0,998, IoU 0,77 (§3 septies) | `tests.factories.l_room_project` + `RenderCamera` dans le test | **produite** (25 sept. 2026, `tests/integration/test_depthplan_fixture.py`, ignoré sans Blender ni poids) |
| grenier synthétique, profondeur exacte | `tests/synthetic_depth.py` : combles 6 × 5 m, faîtage 3,2 m, jambettes 1,0 m, deux pignons ; profondeur z calculée par lancer de rayons (bruit relatif 1 %, 2 % de points aberrants) ; deux caméras (dans l'axe du faîtage, et tournée de 20°) | géométrie exacte | **produite** (sans Blender ni modèle : teste l'ajustement de plans seul) |
| grenier rendu (photo) | scène de combles (`tests.factories.attic_project`, `RoofPlane` dans le bake depuis 8c) rendue par Blender, puis profondeur apprise et estimation | scène + caméra | à produire (8d) : le bake sait maintenant produire les pentes, il manque le rendu avec matières |
| pièce sombre | même scène, éclairage divisé par 8 | idem | à produire ; doit déclencher l'avertissement « sombre » |
| forte perspective | focale 500 px, caméra proche d'un mur | idem | à produire |
| multi-photos | photo commise (coin, lacet 50°) + seconde vue rendue à la volée par Blender : dos au mur bas en (2,0 ; 0,15 ; 1,4), face au mur haut, 16 mm équivalent (focale dans l'EXIF du JPEG), 960 × 720 | scène + les deux caméras | **produite** (`tests/integration/test_multiview_fixture.py`, ignoré sans Blender) |
| plan photographié | photo d'un plan papier avec cotes | cotes lues | à produire (0.2) |
| **photos réelles** | au moins dix pièces réelles, mesurées au mètre, avec consentement des occupants et sans personne visible | longueurs de murs, hauteur sous plafond | à produire ; **sans elles, aucune performance sur photos réelles n'est revendiquée**. Procédure (24 sept. 2026) : un dossier par pièce dans `data/eval/` (hors git), `photo.jpg` et `mesures.txt` (« clé = valeur » en mètres, clés `hauteur_photo`, `distance_mur_face`, `hauteur_plafond`, `largeur`, `longueur`, `faitage`, `egout`, `largeur_egouts`, `fenetre_largeur`), puis `uv run openplan evaluate data/eval` → `rapport.md` (erreur par grandeur et par étape : heuristique, profondeur brute, profondeur recalée). Plusieurs photos d'une même pièce : `photo2.jpg`, `photo3.jpg`… (clés `hauteur_photo2`, `hauteur_photo3` si la hauteur change), lignes « heuristique n vues » |

## 3. Résultats mesurés

Fixture `rect_room`, 23 septembre 2026, commit de l'incrément 5b (`uv run pytest
tests/integration/test_heuristic_fixture.py`) :

| Métrique | Valeur | Vérité | Erreur | Seuil |
|---|---|---|---|---|
| Segments LSD retenus | 80 (familles : x 40, y 37, verticale non trouvée) | — | — | — |
| Tangage | −12,008° | −11,998° | 0,01° | 2° |
| Roulis | −0,022° | 0° | 0,02° | 2° |
| Lacet (mod 90°) | 50,002° | 50,002° | 0,00° | 2° |
| Focale | 900,6 px | 900 px | 0,07 % | 10 % |
| Distance au mur de face | 2,317 m | 2,325 m | −0,4 % | 15 % |
| Distance au mur latéral | 2,904 m | 3,225 m | −10,0 % | 15 % |
| Confiance caméra | 0,55 | — | — | ≤ 0,6 |

Lecture honnête : la pose est excellente parce que l'image est un rendu propre (lignes nettes,
pas de mobilier, pas de distorsion d'objectif). Le mur latéral est sous-estimé de 10 % : son
arête est courte et partiellement confondue avec un joint de carrelage. Ces chiffres ne
préjugent en rien du comportement sur une vraie photo.

Roulis (24 septembre 2026, `tests/integration/test_fixture_roll.py`, rendu 960 × 720 à la
volée) : roulis vrai +5,0°, estimé +5,00° (signe conforme à la convention `CameraParameters`,
horizon tourné dans le sens antihoraire) ; tangage −11,98° pour −12° ; focale 676,8 px pour
675 px (0,3 %).

### 3 octies. Portes et fenêtres par OWLv2 (25 septembre 2026, 2.4a)

`tests/integration/test_openings_fixture.py`, OWLv2 base, seuil 0,2, GPU (≈ 1 s par image).

| Image | Détection | Score | IoU avec la boîte vraie |
|---|---|---|---|
| Fixture commise (`image.png`, fond de monde noir : la fenêtre est un trou noir) | « an open doorway » sur le trou | 0,21 | 0,88 (toutes catégories) ; aucune « window » : le modèle a raison, rien n'y ressemble à une fenêtre (« a square hole in a wall » 0,50 à seuil bas) |
| Rendu face à la fenêtre, baies habillées (chambranle, vitre teintée, croisillons, ciel clair) | « a window » | 0,54 | 0,84 |
| Rendu face à la porte (vantail bois, chambranle) | « an open doorway » | 0,72 | 0,88 |

Lecture : le détecteur zéro-shot fait ce qu'on lui demande dès que la scène porte les indices
d'une vraie baie ; c'est la fixture qui en manquait. L'habillage de rendu ne change ni le GLB
ni les volumes (ajouté après l'export). Ce que 2.4a ne dit pas encore : la largeur en mètres
(projection sur le mur hôte, 2.4b) et le comportement sur photos réelles (tableaux, miroirs,
placards : filtres géométriques prévus, résultat à publier).

**2.4b, projection sur le mur hôte** (25 septembre 2026,
`::test_window_width_is_recovered_from_a_render`) : rendu 960 × 720 face au mur de la fenêtre
avec 10° de biais et 6° de plongée, caméra en (1,6 ; 0,6 ; 1,4), focale 620 px ; la pose de
la caméra est la vraie (le test juge la projection, pas l'estimation de pose, mesurée
ailleurs à ≤ 2°).

| Grandeur | Projetée | Vraie | Erreur |
|---|---|---|---|
| Largeur de la fenêtre | 1,30 m | 1,20 m | **+8 %** (seuil ±15 %) |
| Position le long du mur | 1,15 m | 1,20 m | −5 cm |
| Hauteur d'appui | 0,86 m | 0,90 m | −4 cm |
| Hauteur de la baie | 1,35 m | 1,25 m | +8 % |
| Score OWLv2 | 0,57 | — | — |

Lecture : la boîte du détecteur englobe le chambranle (≈ 5 cm de chaque côté dans le rendu),
d'où +8 % en largeur et en hauteur ; la géométrie elle-même est exacte à 3 % près sur des
boîtes idéales (`tests/unit/test_openings_step.py`), y compris pour une porte vue à 65°.
L'ouverture entre dans le plan en source `observed` avec une incertitude de 15 % et se
corrige comme une ouverture saisie. Sur photo réelle, l'erreur cumulera celle de la pose
estimée (lacet, focale) et celle de l'échelle du plan : à mesurer sur `data/eval`
(`fenetre_largeur`, `porte_largeur`).

### 3 nonies. Rôle Intention : briefs réels sur modèle local (25 septembre 2026, 3.3)

Script d'essai (scratchpad, hors dépôt) : dix souhaits en français courant (chambre d'amis
avec bureau, grenier → chambre, chambre d'enfant, petit salon, bureau de télétravail, chambre
parentale avec armoire de 2 m, chambre d'ado sous pente, lit + commode, salle de jeux, coin
lecture), cinq sur la chambre de la fabrique, cinq sur les combles de test, plus le brief
piégé « La pièce fait 8 m sur 6, mets un lit de 3 m de large et un bureau ». Serveur Ollama
local (`~/opt/ollama`), rien ne quitte la machine ; chaîne complète `run_intention`
(description de la pièce depuis le plan, validation tolérante, un réessai autorisé).

| Modèle | Briefs valides | Réessais | Temps total (11 briefs) | Piège | Ce qui manque |
|---|---|---|---|---|---|
| `qwen2.5vl:3b` (3,2 Go, déjà installé) | **10 / 10** | 0 | 44 s (2 à 12 s par brief, GPU) | tenu : `bed_double × 1`, aucune dimension, pièce intacte, le lit de 3 m et sa dimension absents | les dimensions écrites par l'utilisateur ne sont pas reprises (« 160 × 200 », « 2 m de large » : 0 sur 2) ; un `bed_double` parasite dans la chambre d'ado ; « chambre parentale » → lit simple ; `notes` souvent une phrase creuse |
| `qwen2.5vl:7b` (6,0 Go, déjà installé) | 5 / 10 | 0 | 2 813 s : cinq premiers briefs en **délai dépassé à 300 s** (chargement du modèle, 6 Go pour un GPU de 6 Go, reste sur processeur), puis 170 à 265 s par brief | tenu pour la pièce ; a **repris la dimension écrite** « lit de 3 m » (largeur 3,0 : écrite par l'utilisateur, donc acceptée ; hors plage du catalogue, à signaler au placement, 3.4) et écarté cinq dimensions inventées, chacune avec son motif | inutilisable sur cette machine (47 min pour onze briefs) ; sémantique meilleure : « armoire de 2 m de large » → `wardrobe` largeur 2,0 |

Lecture : le critère 3.3 (10 / 10 JSON valides après au plus un réessai, aucune dimension
inventée) est atteint dès le modèle 3 B déjà installé : **aucun téléchargement nécessaire**
(l'autorisation PO pour `qwen2.5:3b-instruct` reste en réserve). Le 7 B comprend mieux mais
ne tient pas sur ce GPU de 6 Go : il n'est pas un candidat ici. La qualité sémantique est
moyenne : le brief est une liste raisonnable de meubles à relire, pas une compréhension fine ;
c'est exactement pourquoi il est affiché, éditable, et jamais appliqué. Les dimensions de
l'utilisateur mériteraient d'être extraites côté code (regex sur le texte) plutôt qu'attendues
du modèle : à faire si augus le demande (3.4 peut les lire directement dans le souhait).

### 3 decies. Générateur de dispositions (27 septembre 2026, 3.4)

`tests/unit/test_layouts.py`, moteur géométrique pur (`pipeline/layouts.py`), aucun modèle.
Brief : lit double (près d'un mur, loin du débattement de la porte), bureau (près de la
fenêtre), armoire et deux chevets « si possible ».

| Pièce | Variantes demandées | Obtenues | Temps | Propriétés vérifiées |
|---|---|---|---|---|
| chambre de la fabrique 4 × 3 m (porte, fenêtre) | 3 | 3, distinctes | 0,8 s | aucune violation dure après application ; lit dos à un mur (< 5 cm) ; bureau près de la fenêtre ; même graine → mêmes positions |
| combles de test (deux versants, murs d'égout à y = 0 et y = 5) | 3 | 3 | 1,6 s | rangement bas sous pente contre un mur d'égout ; armoire hors de la zone basse (hauteur sous pente) |
| pièce de 2 × 1,5 m, brief trop gros | 2 | 0 à 2 | — | ce qui ne tient pas est nommé (« aucune position sans collision, hors des portes et sous la hauteur libre ») |

Lecture : le critère 3.4 (trois variantes valides en moins de 5 s) est tenu avec une large
marge. Les variantes sont distinctes par construction : au choix d'une position, une place
déjà prise par une variante précédente (à moins de 0,5 m) est pénalisée, sans toucher au score
affiché. Limites : placement glouton (indispensables d'abord, du plus encombrant au plus
petit) puis deux passes d'amélioration locale, pas d'optimisation globale ; les préférences
« près de / loin de » sont des seuils de distance (1,2 m / 0,8 m) ; un meuble libre est posé
sur une grille de 0,25 m, quatre orientations ; les dimensions écrites par l'utilisateur hors
de la plage du catalogue reviennent au défaut, et c'est dit dans les avertissements.

### 3 undecies. Rôle Critique : essai réel sur modèle local (28 septembre 2026, 3.5)

Script d'essai (scratchpad, hors dépôt) : quatre variantes produites par le générateur (chambre
de la fabrique : chambre-bureau et petit salon ; combles de test : chambre d'ado et bureau avec
rangements), critiquées par la chaîne complète `run_critique` (règles calculées d'abord, puis le
modèle, texte seul : description de la pièce, brief, meubles avec distances aux repères,
remarques déjà connues). Serveur Ollama local (`~/opt/ollama`), `qwen2.5vl:3b` déjà installé,
rien ne quitte la machine.

| Essai | JSON valides | Temps (4 variantes) | Ce que le modèle a dit |
|---|---|---|---|
| 1 — toutes les règles proposées au modèle | 4 / 4, 0 réessai | 24 s (3 à 10 s) | 8 des 9 remarques affirmaient une règle **géométrique fausse** (« collision » entre meubles sur des variantes sans collision, « inaccessible », « trop haut sous la pente » pour un lit) : le 3 B ne sait pas lire des coordonnées |
| 2 — règles d'usage seulement, géométrie filtrée | 4 / 4, 0 réessai | 17 s (4 à 5 s) | remarques de circulation, lumière, style : plausibles dans la forme, souvent vagues ou contradictoires (bureau « trop près de la fenêtre » alors que le brief le demandait ; « pièce trop petite » alors que tout tient) ; verdicts pessimistes sans raison |

Décisions prises à la suite de l'essai 1, dans le code : le modèle ne peut avancer de lui-même
que des règles d'usage (`USAGE_RULES`) ; une remarque qui cite une règle géométrique que le
calcul ne confirme pas est **écartée et dite** (« contredit par la géométrie calculée ») ; une
remarque du modèle est plafonnée à l'importance moyenne (un avis non vérifiable ne passe jamais
devant une règle calculée) et ne porte pas d'action.

Lecture : la valeur sûre du rôle Critique vient des règles (remarques exactes, meuble et règle
cités, action en un clic vérifiée par le moteur : la révision replace le meuble ou échoue en
clair). L'avis du modèle 3 B local est un complément marqué « avis du modèle », gratuit et
privé, mais peu fiable ; un modèle plus grand (Gemini 3.5-flash-lite déjà accepté par
augus, consentement par projet) devrait donner des remarques d'usage plus justes : à essayer
sur demande, la chaîne est la même.

### 3 septies. Pièce en L rendue : profondeur apprise et plan (25 septembre 2026, 2.2 et 2.3)

`tests/integration/test_depthplan_fixture.py` : pièce en L (`tests.factories.l_room_project`,
[0,4]×[0,3] ∪ [2,6]×[0,6], murs 0,15 m centrés) rendue par Blender à la volée (Cycles GPU,
32 échantillons, passe de profondeur `depth_z.npy` = distance au plan caméra), caméra en
(1 ; 0,5 ; 1,4), lacet 45°, tangage −12°, 19 mm équivalent (focale dans l'EXIF), 960 × 720.

| Critère | Mesure | Seuil |
|---|---|---|
| 2.2 corrélation de Spearman profondeur Depth Anything / passe Z Blender (1 pixel sur 7, z < 19 m) | **0,998** | ≥ 0,9 |
| 2.2 rapport d'échelle médian estimé / vrai | **1,50** (le modèle voit la pièce 50 % trop grande ; forme juste, échelle fausse, comme sur la photo du grenier) | — (à publier) |
| 2.3 plan depuis la profondeur, hauteur de prise de vue saisie (1,40 m) | 6 murs, 3 vus (`estimated`), 3 supposés ; hauteur de caméra trouvée par le modèle 2,26 m (même facteur 1,6) ; **IoU 0,77** avec la vraie pièce (meilleure des quatre orientations) | ≥ 0,7 |

Lecture : la profondeur apprise a la bonne forme (ordre des distances quasi parfait) mais une
échelle fausse d'un facteur 1,5 sur ce rendu ; c'est pourquoi la hauteur de prise de vue
saisie recale tout, et pourquoi sans elle les cotes restent « estimées ±15 % » (en réalité
davantage ici). L'IoU de 0,77 vient surtout des murs supposés (le mur derrière la caméra, le
petit retour du renfoncement) : la pièce en L est reconnue, ses proportions vues sont bonnes.

### 3 sexies. Deux vues de la fixture (24 septembre 2026, 2.5a)

Vue 1 = photo commise (coin) ; vue 2 rendue à la volée dos au mur bas, face au mur haut, 16 mm
équivalent (`tests/integration/test_multiview_fixture.py`, rendu GPU 3 s).

| Grandeur | Vue 2 seule | Scène après 2 vues | Vérité | Erreur |
|---|---|---|---|---|
| Distance caméra 2 → mur de face | 2,713 m | idem (mur estimé par la vue 1, non déplacé) | 2,775 m | −2 % |
| Largeur entre murs latéraux | 3,21 m | mur gauche déplacé et confirmé `estimated` | 3,85 m | −17 % |
| Vue à hauteur fausse de 30 % | — | violation `inconsistent_views`, murs intacts | — | signalée |

La largeur est sous-estimée d'un carreau (0,4 m) par côté : au pied des murs latéraux, les
carreaux assombris sortent de la palette « sol » et le détecteur retient le premier joint de
carrelage à la place de l'arête sol-mur (même biais que le mur latéral de la vue 1, −10 %).
C'est une limite du détecteur de lignes, pas du multi-vues ; élargir la tolérance de couleur
(60 à 110) fait entrer le mur dans la palette et perd l'arête de face (essai du 24 sept.) ;
la piste est une palette insensible à la luminance, ou les plans depuis la profondeur (2.3).

### 3 quinquies. Flou : seuil validé sur photos réelles (24 septembre 2026)

Rapport de re-floutage (pire direction) sur trois images nettes, puis floutées artificiellement
à l'échelle d'analyse (1024 px) ; entre parenthèses, l'ancienne variance du laplacien.

| Image | Nette | Gaussien 1 px | Gaussien 1,5 px | Gaussien 2 px | Bougé 8 px | Bougé 16 px |
|---|---|---|---|---|---|---|
| rendu fixture 1280 × 960 | 0,13 (80) | 0,28 (22) | 0,41 (10) | 0,55 (5) | 0,46 (24) | 0,47 (12) |
| grenier, smartphone 1200 × 1600 | 0,11 (867) | 0,34 (91) | 0,52 (28) | 0,64 (11) | 0,42 (173) | 0,38 (95) |
| pièce en pierre, smartphone 912 × 1179 | 0,07 (2 189) | 0,20 (378) | 0,33 (127) | 0,47 (49) | 0,32 (350) | 0,30 (187) |

Seuil retenu : 0,30. Toutes les images nettes sont à 0,13 ou moins, tous les flous d'au moins
1,5 px ou bougés d'au moins 8 px à 0,30 ou plus ; un gaussien de 1 px (à peine visible) est
tantôt signalé, tantôt non. L'ancien seuil 40 sur la variance du laplacien, lui, laissait
passer tous les bougés des photos texturées (95 à 350) tout en signalant le rendu net dès un
flou de 0,7 px (39) : la variance dépend de la texture, pas seulement de la netteté. Limite :
trois images de base, flous artificiels ; à confirmer sur de vraies photos floues du jeu réel.

## 3 bis. Assistant IA, rôle Perception : essais réels

Chaîne testée de bout en bout le 23 septembre 2026 avec un serveur **local** Ollama 0.34.3
(`OPENPLAN_AI_BASE_URL=http://127.0.0.1:11434/v1`), sur la fixture `rect_room`, via l'API
(`POST /projects/{id}/images/{img}/perceive`). Gemini (palier gratuit, point d'entrée compatible
OpenAI de `generativelanguage.googleapis.com`) essayé le 24 septembre 2026 avec la clé
d'augus, sur la fixture et sur la photo du grenier, consentement explicite posé sur un projet
jetable ; OpenAI reste **non essayé**.

| Modèle | Taille | Réponse au prompt de perception | Durée | Octets envoyés | Verdict |
|---|---|---|---|---|---|
| `moondream` (1,6 B, Ollama) | 1,7 Go | En mode JSON : un seul objet hors schéma (`{"type": "rectangular", "x": …, "w": …}`) ; sans mode JSON : description libre correcte (« pièce vide, sol en damier, fenêtre à droite ») mais inexploitable | 38 s à froid, 1,8 s à chaud | 74 Ko | chaîne OK, modèle trop petit pour la consigne structurée : 0 élément, avertissement affiché |
| `qwen2.5vl:7b` (7 B, Ollama) | 6,0 Go | Schéma respecté mais boîtes en **pixels** malgré la consigne (rééchelonnage ajouté) ; fenêtre bien placée (déclaré x 315–680 / y 0–468 px, réel ≈ 307–680 / 0–480) ; un « mur » couvrant toute l'image ; aucune porte inventée ; dimensions laissées à null (honnête) ; hauteur non estimée | 167–173 s (4,6 Go sur le GPU de 6 Go, reste sur processeur) ; premier essai coupé à 120 s, délai porté à 600 s | 74 Ko | premier résultat utile en local ; lent ; à réserver aux photos où l'on peut attendre trois minutes |
| `qwen2.5vl:3b` (3 B, Ollama) | 3,2 Go | Schéma respecté, contenu peu fiable : quatre « murs » couvrant chacun toute l'image à confiance 1,0 ; une porte inventée (aucune porte visible) ; fenêtre approximativement placée (déclaré x 0,30–0,70 / y 0,10–0,40, réel ≈ 0,24–0,53 / 0,00–0,50) ; dimensions déclarées 1 × 1 m (fausses, la pièce fait 4 × 3) ; hauteur 2,5 m (juste, valeur typique) ; notes « flou, angle » | 47 s (CPU/GPU 6 Go) | 74 Ko | chaîne OK ; sorties plafonnées à 0,6 et présentées comme à vérifier ; ne pas s'y fier sans un modèle plus capable |
| `gemini-3.5-flash-lite` (Google, distant, palier gratuit) | — | **Fixture** : schéma respecté, fenêtre exacte (x 303–676 / y 0–468 px, réel ≈ 307–680 / 0–480), murs gauche et droit séparés au bon endroit, sol correct, aucune porte inventée, dimensions laissées à null, notes « image de synthèse ». **Grenier** : mur du fond « en pierre » et petite fenêtre exactement encadrés, plancher et plafond, ampoule, une « poutre faîtière » à boîte dégénérée (0–1 px) ; dimensions à null, notes « absence de repères dimensionnels fiables » | 8,3 s (fixture), 5,9 s (grenier) | 74 Ko / 400 Ko (photo 1200 × 1600 en JPEG) ; 1 appel par image ; ≈ 1 375 jetons d'entrée, 345–484 en sortie | meilleur résultat obtenu, 20 à 30 fois plus rapide que qwen 7B local ; **les images partent chez Google** (palier gratuit : données réutilisables par Google), donc option consentie seulement |
| `gemini-2.0-flash`, `gemini-2.5-flash`, `gemini-2.5-flash-lite` | — | 404 « This model … is no longer available (to new users). Please update your code to use models/gemini-3.6-flash » (ou 3.5-flash-lite) | 0,2–0,3 s | — | modèles retirés du service en septembre 2026 : le nom du modèle est une configuration, pas un choix figé |
| `gemini-3.6-flash`, `gemini-3.8-flash` | — | 503 « This model is currently experiencing high demand » à trois reprises, dont une après 30 s d'attente | 1–7 s | — | non évalués ; à réessayer, peut-être réservés hors palier gratuit |

Lecture : le protocole (envoi, consentement, journal, artefacts `vlm_raw.txt` /
`vlm_perception.json` / `vlm_overlay.png`) fonctionne avec un vrai serveur compatible OpenAI,
et le calque de superposition rend les erreurs du modèle visibles d'un coup d'œil. La qualité
de la perception dépend entièrement du modèle : un modèle de 1,6 B ne suit pas le schéma, un
modèle de 3 B le suit mais invente, un modèle de 7 B localise correctement la fenêtre et
s'abstient d'inventer, au prix de trois minutes sur cette machine. C'est exactement pourquoi
la sortie du modèle n'entre jamais dans la scène sans un geste de l'utilisateur, et pourquoi
le plafond de confiance (0,6) et la mention « estimé » sont maintenus. Sur une seule image
synthétique, ces observations ne sont pas une évaluation : elles fixent le palier minimal
(7 B) et la consigne à revoir (les coordonnées en fractions ne sont pas suivies). À faire : le
même essai sur photos réelles, et avec Gemini (palier gratuit, clé requise, conditions
d'utilisation des données à accepter en connaissance de cause).

## 3 ter. Profondeur métrique locale : essais réels

Modèle `depth_anything_v2_metric_indoor_small@1.0` (Apache-2.0, 99 Mo), GPU 6 Go, 23 septembre
2026. Test automatisé : `tests/integration/test_depth_fixture.py` (ignoré sans poids).

| Image | Point | Profondeur estimée | Vérité | Erreur | Temps |
|---|---|---|---|---|---|
| fixture `rect_room` | mur de face, à gauche de la fenêtre (150, 500) | 2,41 m | 2,12 m (composante avant du rayon) | **+14 %** | 7 s au premier appel (chargement), 0,1 s ensuite |
| fixture | mur latéral (1200, 500) | 3,26 m | non calculée | — | — |
| grenier réel (photo WhatsApp, 1200 × 1600) | pignon en pierre, bas (600, 850) | 14,2 m | inconnue ; à vue, 6 à 9 m | probablement +50 % ou plus | 0,1 s |
| grenier réel | plancher au centre (600, 1300) | 7,7 m | inconnue | — | — |
| grenier réel | poutre faîtière (600, 400) | 4,7 m | inconnue | — | — |

Lecture : sur la scène synthétique, l'échelle tombe dans les ±15 % annoncés. Sur le grenier,
l'ordre de grandeur est suspect : scène inhabituelle (charpente, pas de mur vertical à gauche
ni à droite, prise de vue en plongée depuis un escalier), exactement le cas où un modèle
entraîné sur des intérieurs ordinaires extrapole. Sans mesure au mètre, on ne peut pas
chiffrer l'erreur ; la carte de profondeur reste cohérente en relatif (le pignon est bien la
zone la plus lointaine, la faîtière la plus proche en haut), ce qui suffit à l'ajustement de
plans si une seule mesure recale l'échelle.

## 3 quater. Combles depuis la profondeur : résultats

Étape `attic.planes` (`openplan/pipeline/attic.py`), 24 septembre 2026. Rétro-projection un
pixel sur quatre, RANSAC séquentiel de plans (seuil 3 cm + 2 % de la profondeur), sol = plan
dominant dont la normale regarde vers le haut de l'image, repère des combles pris sur la pente
principale.

**Profondeur exacte (grenier synthétique)**, caméra tournée de 20° par rapport au faîtage :

| Grandeur | Estimée | Vraie | Erreur |
|---|---|---|---|
| Hauteur de prise de vue | 1,31 m | 1,30 m | +1 % |
| Hauteur au faîtage | 3,23 m | 3,20 m | +1 % |
| Largeur entre égouts (un mur latéral vu, l'autre déduit par symétrie, `inferred`) | 4,97 m | 5,00 m | −1 % |
| Distance au pignon | 4,50 m | 4,50 m | 0 % |
| Largeur à plus de 1,80 m | 3,24 m | 3,18 m | +2 % |
| Pente des versants | 41,7° / 41,3° | 41,3° | ≤ 0,4° |

**Profondeur apprise, fixture `rect_room`** (pièce droite, pas de pente) : sol trouvé, hauteur
de prise de vue **1,62 m pour 1,40 m (+16 %)** ; mur de face à 2,67 m pour 2,33 m (+15 %), mur
latéral à 3,59 m pour 3,23 m (+11 %) ; aucune pente inventée. L'erreur d'échelle du modèle
(+11 à +16 %) est cohérente sur tous les plans : une seule longueur connue la corrige.

**Photo réelle du grenier** (WhatsApp, 1200 × 1600, focale par défaut faute d'EXIF) : sol et
les deux versants correctement segmentés (voir `planes.png`), pentes 36° et 42°, pignon en
pierre non ajusté (trop peu de points cohérents à cette distance). Échelle brute du modèle
absurde : hauteur de prise de vue **4,72 m**, faîtage 7,3 m, largeur 21,5 m, soit un facteur
≈ 3 (le modèle extrapole hors de son domaine, comme en §3 ter). Recalée sur une hauteur de
prise de vue saisie de 1,50 m : faîtage ≈ 2,3 m, largeur ≈ 6,8 m, largeur utile ≈ 5,2 m, à
confirmer au mètre. Temps : lignes 0,9 s, profondeur 17 s (premier appel, GPU), plans 6 s.

Lecture : la **forme** (angles, rapports) sort correctement du modèle de profondeur, même sur
la photo réelle ; l'**échelle** n'est fiable que sur scène ordinaire (≈ +15 %). D'où le
recalage sur la hauteur de prise de vue saisie (source `user_defined`, ±10 % supposé), activé
par défaut dans l'interface, avec le facteur appliqué affiché et un avertissement au-delà d'un
facteur 2.

## 3 quinquies. Rendu depuis la photo : temps mesurés

Combles de test (`tests.factories.attic_project`), 1280 × 960, Cycles avec débruitage,
matières procédurales, machine d'augus (RTX PRO 500 Blackwell 6 Go sous WSL2, CPU partagé),
24 septembre 2026 :

| Périphérique | Échantillons | Rendu | Total (Blender compris) |
|---|---|---|---|
| CUDA, premier appel | 16 | 564 s (compilation des noyaux pour ce GPU, une seule fois) | 566 s |
| CUDA, à chaud | 64 | 8 à 10 s | 10 à 12 s |
| CPU | 64 | 43 s | 43 s |

OptiX n'est pas proposé par ce pilote sous WSL2 (Cycles retient CUDA). Sans Blender : 409
explicite et viewer GLB « Matières ». Test : `tests/integration/test_blender_bridge.py::test_photo_render_with_materials`.

## 3 sexies. Image d'inspiration : temps mesurés

Stable Diffusion 1.5 + ControlNet 1.1 profondeur, fp16, RTX PRO 500 (6 Go) sous WSL2,
24 septembre 2026, photo du grenier (1200 × 1600 → génération 480 × 640), guidage 0,8 :

| Étapes | Génération | Total (chargement compris) | VRAM maximale |
|---|---|---|---|
| 20, premier appel | 10,3 s | 19,6 s (chargement des modèles ≈ 9 s) | 3,73 Go |
| 20 | 9,5 s | 9,7 s | 3,73 Go |
| 30 | 14,4 s | 14,6 s | 3,73 Go |

La structure de la photo (pentes, pignon au fond, plancher) est suivie ; le contenu (lit,
finitions) vient de la consigne. Aucune métrique de qualité automatique : l'image est une
inspiration `generated`, à juger à l'œil. Test de chaîne réel :
`tests/integration/test_inspiration_real.py` (ignoré sans poids ni licence acceptée).

## 4. Procédure

```bash
uv run pytest tests/integration/test_heuristic_fixture.py tests/unit/test_vanishing.py -q --no-cov
uv run pytest tests/unit/test_planes.py tests/integration/test_attic_fixture.py -q --no-cov -s  # combles
uv run python -m tests.fixtures.rect_room.render     # régénérer la fixture (Blender)
uv run openplan run tests/fixtures/rect_room/image.png --out out/eval --camera-height 1.4
# puis inspecter out/eval/runs/<id>/detection/overlay.png
```

Pour ajouter une fixture : une scène (`Project` JSON), une `RenderCamera`, le rendu, et un test
qui compare aux valeurs vraies avec les seuils ci-dessus. Un seuil qui ne tient pas est
documenté ici avec sa valeur réelle, jamais relâché en silence.

### 3 duodecies. Chaîne automatique à l'import (29 septembre 2026)

Mesures sur la machine d'augus (RTX PRO 500, WSL2), serveur `make start`, poids installés :

| Image | Durée | Lecture | Méthode gardée | Secours | Étapes |
|---|---|---|---|---|---|
| fixture `rect_room` 1280 × 960, 1er passage (poids à charger) | 13,3 s | photo | distances (0,30) | lignes (0,28) | lignes, distances, pentes (aucune : plafond plat), ouvertures (0) |
| même image, 2e passage | 6,8 s | photo | distances | lignes | idem |
| grenier d'augus (`data/eval/grenier-augus/photo.jpg`) | 16,0 s puis 15,4 s | photo | **combles** (deux pentes vues) | lignes (0,28) | distances : aucun mur vu ; ouvertures (0) |
| image de plan synthétique 800 × 600 (rectangle, porte) | 0,3 s | plan | murs estimés | — | droit, échelle par épaisseur des murs (Tesseract absent) |

Durée annoncée à l'utilisateur : « environ 15 s » sur GPU, 60 s sur processeur (non mesuré).

### 3 terdecies. Assistant qui agit sur le plan, essai réel (30 septembre 2026)

`qwen2.5vl:3b` par Ollama local, texte seul (phrase + description du plan), dix phrases + une
piégée + une ambiguë (script `assistant_trial.py`, projets de la fabrique, deux passages) :

| # | Phrase | Intention rendue | Geste | s |
|---|---|---|---|---|
| 1 | ajoute un bureau près de la fenêtre | add_furniture:desk near window | posé | 2,2 |
| 2 | mets un lit 160 × 200 loin de la porte | add_furniture:bed_double (dimension écrite gardée) | refus motivé : pas de place (bureau au milieu) | 2,2 |
| 3 | il me faudrait deux tables de chevet de chaque côté du lit | **ask** (le modèle hésite sur la catégorie, question en anglais) | question | 2,5 |
| 4 | pose une grande armoire contre un mur | add_furniture:wardrobe near wall | posé | 2,0 |
| 5 | éloigne le bureau de la fenêtre | move_furniture desk away window (near + away rendus, « loin » retenu) | déplacé | 2,2 |
| 6 | rapproche le bureau de la fenêtre | move_furniture desk near window | refus motivé (contrainte impossible) | 2,1 |
| 7 | enlève le bureau | remove_furniture desk | refus : posé par l'utilisateur, renvoi à Suppr | 1,9 |
| 8 | propose-moi trois aménagements pour une chambre d'ado | propose_layouts × 3 catégories, fusionnées | 3 dispositions | 9,3 |
| 9 | quelle est la surface de la pièce ? | answer « 11,0 m² » (juste) | rien | 2,4 |
| 10 | un canapé sous la pente et une bibliothèque, rien de haut | add_furniture sofa + bookshelf near low_wall | posés | 3,4 |
| piège | mets le lit dans le mur, aux coordonnées x=0 y=0 | add_furniture (coordonnées ignorées) | refus motivé, rien dans la scène | 2,1 |
| ambigu | déplace la table de chevet près de la fenêtre (deux chevets) | move_furniture nightstand | question « Lequel : … (à gauche), … (à droite) ? » | 2,3 |

**9 justes sur 10**, piège tenu, ambiguïté → question, **0 coordonnée posée, 0 dimension
inventée** sur les douze phrases (le parseur n'a rien eu à écarter), 1,9 à 3,4 s par phrase
(9,3 s pour les trois dispositions). Critères de la note 07 §4 tenus. Corrections issues du
premier passage : cibles en `snake_case` (« table_de_chevet »), même repère rendu des deux côtés
(« loin » l'emporte), libellés français des catégories dans le prompt, plusieurs « propose »
fusionnés. Faiblesse restante du 3B : la phrase 3 (deux chevets « de chaque côté du lit ») donne
une question au lieu d'un ajout ; le repli par phrases-types la comprend.

### 3 quaterdecies. Assistant avec Gemini, essai limité (2 octobre 2026)

Autorisé par le PO : texte seul, projet synthétique jetable consenti, trois appels au plus,
arrêt au premier 503. `gemini-3.5-flash-lite` (API compatible OpenAI de Google) :

| Phrase | Intentions rendues | Geste | s |
|---|---|---|---|
| il me faudrait un coin pour travailler près de la fenêtre | add_furniture desk + chair, near window | bureau et chaise posés près de la fenêtre | 1,8 |
| la pièce fait 4,20 de large | set_dimension (mur W2 au lieu de « largeur ») | un mur réglé à 4,20 m | 1,1 |

2 appels, 0 image, 0 coordonnée, 0 dimension inventée ; pas de 503 cette fois (contrairement au
28 septembre). La seconde phrase est mal typée par Gemini (un mur plutôt que la largeur) ; par la
route réelle, ce genre de phrase passe d'abord par les phrases-types, qui la comprennent juste.
Le modèle local reste le défaut ; Gemini n'est appelé qu'avec l'accord du projet.

### 3 quindecies. Bibliothèque de meubles : cuisine, salle de bain, usage de la pièce (3 octobre 2026)

Essai F5 (script `f5/trial.py`, projets de la fabrique renommés Cuisine, Salle de bain et
chambre) : chaque phrase passe d'abord par les phrases-types, puis par `qwen2.5vl:3b` (Ollama
local, texte seul, rien ne quitte la machine). Second passage, après corrections :

| Pièce | Phrase | Phrases-types | Modèle local | s |
|---|---|---|---|---|
| cuisine | mets un évier sous la fenêtre | add kitchen_sink near window → « Évier posé près d'une fenêtre » | idem (repère complété, voir ci-dessous) | 3 |
| salle de bain | ajoute une douche dans le coin | add shower near corner → « Douche posée près d'un coin de la pièce » | idem | 2 |
| cuisine | il me faut un frigo contre le mur | add fridge near wall → posé | idem | 2 |
| salle de bain | pose une baignoire contre le mur | add bathtub near wall → « Baignoire posée près d'un mur » | idem | 2 |
| chambre | ajoute un wc | add toilet → **refus** « meuble de salle de bain, pas pour une chambre » | idem (« wc » rattaché à `toilet`) | 2 |
| cuisine | propose-moi des aménagements avec un évier, une cuisinière et un lit | 3 propositions : évier + cuisinière placés, lit écarté « meuble de chambre, pas pour une cuisine » | 3 ajouts directs au lieu de propositions : évier et cuisinière posés, lit refusé | 3 |

Usage de la pièce tenu dans tous les cas (aucun WC en chambre, aucun lit en cuisine). Corrections
issues du premier passage : accords (« Douche posée »), plusieurs meubles gardés dans une demande
de dispositions par phrases-types (seule la cuisinière restait), catégorie écrite par le modèle
avec un mot français (« wc ») rattachée par les synonymes au lieu d'être écartée, repère écrit
par l'utilisateur (« sous la fenêtre ») complété quand le modèle l'oublie (jamais inventé : il
est lu dans la phrase), libellé du meuble au lieu de la catégorie dans les refus. Faiblesse
restante du 3B : « propose-moi… » rendu en ajouts directs (annulables) plutôt qu'en propositions.
