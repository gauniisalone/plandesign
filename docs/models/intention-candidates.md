# 0.3 Rôle Intention (propositions d'aménagement) : cadrage avant tout code

Note de cadrage du 25 septembre 2026, à valider par le PO avant toute ligne de code (même
règle que pour les ouvertures, `openings-candidates.md`). Elle couvre les incréments 3.1 à 3.4
de la roadmap 0.3 (catalogue, contraintes, intention, générateur) : c'est la chaîne minimale
qui va d'une phrase de l'utilisateur à des meubles posés dans le plan. Les incréments 3.5
(critique), 3.6 (interface complète) et 3.7 (rendu des variantes) viennent après et ne sont pas
cadrés ici.

## Ce que l'on cherche

« Transforme ce grenier en chambre avec un bureau » → deux ou trois dispositions **valides**
(aucune collision, passage possible, portes et fenêtres accessibles, rien de trop haut sous
la pente), **comparables** (même brief, même pièce, scores lisibles), **corrigeables** (chaque
meuble se déplace, se tourne, se supprime comme n'importe quel élément) et **jamais appliquées
sans un geste de l'utilisateur**.

Principe du brief (`CLAUDE.md` §14, risque 0.3 de la roadmap) : **le modèle de langage ne pose
aucune coordonnée**. Il traduit l'intention en contraintes structurées ; c'est un moteur
géométrique du dépôt, sans IA, qui place les meubles et qui est le seul à écrire dans la
scène. Une dimension ne vient que de trois sources : le catalogue (dimensions réelles d'un
meuble type), l'utilisateur (saisie, `user_defined`) ou le plan lui-même ; une dimension
« inventée » par le modèle est refusée.

## Ce qui existe déjà

| Moyen | Ce qu'il donne | Limites |
|---|---|---|
| Passerelle de consentement (`adapters/gateway.py`) et client compatible OpenAI | tout appel de modèle, local (Ollama) ou distant (Gemini), avec double drapeau de consentement, sonde préalable, journal réseau sans contenu, `LlmPurpose` `intention` déjà prévu | aucun prompt d'intention encore écrit |
| Rôle Perception (`pipeline/perception.py`) | le modèle (schéma JSON strict, clés inconnues ignorées, réessai, plafond de confiance) | lit une photo ; l'intention lit du texte et le plan |
| Schémas | `Furniture` (asset_id, category, transform, bbox, preserve), `Fixture` (keep_clear), `Constraint` (`KEEP_CLEAR`, `MIN_PASSAGE`, `NO_OVERLAP`, `MAX_HEIGHT_UNDER_SLOPE`…), `Room.room_type`, `DerivedRoom.usable_area_m2` | `Furniture.asset_id` pointe vers un catalogue qui n'existe pas encore |
| Règles | `validation/layout.py` : hauteur sous pente à l'aplomb des quatre coins (dure / souple / désactivée par `Constraint`) | collisions, passage, accès aux ouvertures et radiateurs : non implémentés |
| Scène 3D et rendu | prismes et polyèdres (murs, pentes), matières, rendu Blender depuis la photo | aucun meuble dessiné ni en plan ni en 3D |
| Essais réels | Ollama local (`moondream`, `qwen2.5vl:3b`, `qwen2.5vl:7b` installés dans `~/opt/ollama`, 11 Go), Gemini 3.5-flash-lite (clé d'augus, palier gratuit) | seulement sur la perception d'images |

## Ce que l'utilisateur saisit

Un formulaire court, dans le panneau IA, jamais une page de réglages :

| Champ | Exemple | Obligatoire | Où ça va |
|---|---|---|---|
| Usage de la pièce (texte libre) | « chambre d'amis avec un coin bureau, je télétravaille deux jours par semaine » | oui | prompt du modèle |
| Pièce concernée | la pièce sélectionnée dans le plan (surface, surface utile à plus de 1,80 m, portes, fenêtres, radiateurs, pentes) | oui, déduite | contexte du modèle (texte, jamais la photo) |
| À conserver | meubles ou éléments déjà dans la scène cochés « à garder » (`Furniture.preserve`) | non | contraintes dures |
| À éviter | texte libre (« pas de lit sous la fenêtre », « rien devant le radiateur ») | non | prompt puis contraintes |
| Dimensions connues | « le lit fait 160 × 200 », « le bureau doit faire au moins 1,40 m » | non | `user_defined`, prioritaire sur le catalogue |
| Style | mots-clés libres (« scandinave, bois clair ») | non | seulement pour le rendu et l'inspiration ; aucun effet géométrique |
| Budget | facultatif, informatif | non | transmis au modèle, aucun effet en 0.3 (pas de prix au catalogue) |

Ce qui part au modèle : ce texte et une **description textuelle** de la pièce (dimensions,
ouvertures, pentes, éléments à conserver). **Jamais la photo, jamais le fichier projet.** Vers
un modèle distant, le double consentement du projet reste exigé (le texte décrit le logement).

## Ce que l'IA propose, en deux temps

**1. Le modèle de langage → `LayoutBrief` (schéma Pydantic strict)**

```json
{
  "room_use": "bedroom_office",
  "items": [
    {"category": "bed_double", "count": 1, "priority": "required",
     "dimensions_m": null, "near": ["wall"], "away_from": ["window", "door_swing"]},
    {"category": "desk", "count": 1, "priority": "required",
     "dimensions_m": {"width": 1.4}, "near": ["window"], "away_from": []},
    {"category": "wardrobe", "count": 1, "priority": "nice_to_have",
     "dimensions_m": null, "near": ["low_wall"], "away_from": []}
  ],
  "keep": ["<id des éléments à conserver>"],
  "avoid": ["nothing_in_front_of_radiator"],
  "notes": "ce que le modèle n'a pas compris ou a supposé"
}
```

- `category` appartient au **catalogue** (liste fermée fournie dans le prompt) ; une catégorie
  hors liste est ignorée avec avertissement.
- `dimensions_m` n'est accepté que si l'utilisateur l'a écrit dans son texte (le prompt
  l'exige ; un test « brief piégé » vérifie qu'une dimension de pièce inventée est rejetée :
  le brief ne peut pas redimensionner la pièce, seulement un meuble, et seulement si le texte
  le dit).
- `priority` : `required` / `nice_to_have` ; `near` / `away_from` : vocabulaire fermé
  (`wall`, `window`, `door`, `door_swing`, `radiator`, `low_wall` = zone sous 1,80 m,
  `ridge` = sous le faîtage, `outlet`).
- Source du brief : `generated`, confiance plafonnée (`MAX_SCORE` 0,6 comme la perception),
  affiché tel quel à l'utilisateur, **éditable** avant de lancer le générateur.

**2. Le moteur géométrique (`openplan/layout/`, sans IA) → `LayoutProposal` × N**

- Entrée : `LayoutBrief` validé, la pièce (polygone, ouvertures, radiateurs, pentes), le
  catalogue.
- Placement par heuristiques puis recherche locale : meubles `required` d'abord, du plus
  encombrant au plus petit ; positions candidates le long des murs (lit, armoire, bureau),
  orientation par quart de tour ; score = préférences du brief satisfaites − pénalités
  souples ; arrêt quand N variantes valides distinctes existent ou après un budget de temps.
- Contraintes **dures** (une variante qui en viole une n'est pas proposée) : aucune collision
  entre meubles ni avec les murs (Shapely) ; débattement des portes libre ; fenêtres non
  bloquées au-delà de l'allège ; `keep_clear` des radiateurs et éléments fixes ; passage d'au
  moins 0,80 m de la porte à chaque meuble `required` ; **hauteur sous pente** : le meuble tient
  sous la hauteur libre à l'aplomb de ses quatre coins (règle existante de
  `validation/layout.py`, appliquée telle quelle) ; éléments `preserve` immobiles.
- Contraintes **souples** (score) : `near` / `away_from` du brief ; lit accessible sur deux
  côtés ; bureau près d'une fenêtre ; longueur de mur libre restante.
- Sortie : `LayoutProposal` (liste de `Furniture` avec `transform`, `bbox` copiée du
  catalogue, `asset_id`, source `generated`, provenance = brief + graine), `score`, `valid`,
  `violations` lisibles, `warnings` (« la pièce est trop petite pour l'armoire demandée : non
  placée »), durée.
- **Rien n'entre dans la scène** tant que l'utilisateur n'a pas cliqué « Appliquer cette
  variante » (commit en acteur `user`, meubles `generated` → l'utilisateur les déplace, ils
  deviennent `user_defined` un par un ; annulable). Comparaison côte à côte de N variantes
  dans le plan, la 3D suit (meubles = boîtes cotées en 0.3).

## Catalogue (3.1) : boîtes cotées, licence obligatoire

Dix meubles paramétriques créés par le projet, licence **CC0** déclarée dans
`assets/catalog.yaml` et `docs/licenses/`, chacun avec catégorie, dimensions réelles
(min / défaut / max), hauteur, ancrages (contre un mur, libre), dégagement requis (devant une
armoire 0,60 m, autour d'un lit 0,60 m sur les côtés d'accès) et étiquette française. Liste
proposée : lit double 160 × 200, lit simple 90 × 200, table de chevet, bureau 120 à 160 × 60 à
80, chaise de bureau, armoire 100 à 200 × 60, commode, bibliothèque, canapé deux places, table
+ deux chaises. Un asset sans licence ou `unknown` est refusé par un test. Pas de modèle 3D
externe en 0.3 : des boîtes (les volumes analytiques existants), ce qui suffit à valider les
règles et évite tout problème de licence.

## Modèles candidats pour le rôle Intention (texte seul)

| Candidat | Licence | Taille | Où | Temps attendu (brief de 50 mots) | Verdict |
|---|---|---|---|---|---|
| **Ollama `qwen2.5vl:3b`** (déjà installé) | Apache-2.0 | 3,2 Go | local | 5 à 10 s en texte seul (pas d'image à encoder) | **à essayer d'abord** : aucun téléchargement ; s'il suit le schéma sur 9 briefs sur 10, il est retenu |
| Ollama `qwen2.5:3b-instruct` | Apache-2.0 | 1,9 Go (Q4) | local, `~/opt/ollama` | 5 à 15 s (GPU 6 Go), JSON fiable pour un schéma court | recommandé si le précédent rate le schéma ; téléchargement à valider (`ollama pull`, comme les poids) |
| Ollama `qwen2.5vl:7b` (déjà installé) | Apache-2.0 | 6,0 Go | local | 20 à 40 s en texte seul | repli sans téléchargement ; plus lent, GPU partagé |
| Ollama `llama3.1:8b-instruct` | Llama 3.1 Community (acceptable, mais pas OSI) | 4,9 Go | local | 20 à 40 s | non retenu : licence moins claire que Qwen, pas meilleur en JSON |
| **Gemini 3.5-flash-lite** (compatible OpenAI) | service, palier gratuit | — | distant | 3 à 6 s | **option consentie par projet** : le texte du brief et la description de la pièce partent chez Google (conditions du palier gratuit : données réutilisables) ; jamais par défaut |
| ChatGPT / OpenAI | service payant | — | distant | — | pas de clé, hors « gratuit avant tout » ; reste possible par la même passerelle |

Aucun poids n'entre dans le dépôt ; le modèle utilisé est configuré par `.env`
(`OPENPLAN_AI_MODEL`), comme aujourd'hui.

## Garde-fous (rappel des principes, appliqués à ce rôle)

- Le modèle **ne place rien** : ni coordonnée ni orientation dans son schéma (un champ inconnu
  est ignoré, un champ `position` est un motif d'avertissement dans les tests).
- Toute sortie du modèle et du générateur est `generated` ; rien n'est appliqué sans clic ;
  l'application est un commit annulable en acteur `user`.
- Aucune photo ne part : le rôle Intention n'envoie que du texte (journal réseau : `images: 0`).
- Les règles géométriques restent dans `validation/` et `layout/`, testées sans modèle ; un
  modèle simulé (`FakeLanguageModel`) sert aux tests, l'essai réel est documenté au protocole.
- Le catalogue n'accepte que des licences connues ; pas d'asset externe en 0.3.
- Hauteur sous pente : règle dure par défaut (déjà en place), souple ou désactivée seulement
  par une `Constraint` de l'utilisateur.

## Critères d'évaluation

| Critère | Mesure | Seuil | Comment |
|---|---|---|---|
| Intention : briefs valides | dix briefs de test (français courant, fautes comprises) → `LayoutBrief` valide après au plus un réessai | 10 / 10 avec le modèle local retenu (publié tel quel sinon) | `tests/unit/test_intention.py` (modèle simulé) + essai réel au protocole |
| Intention : rien d'inventé | brief piégé « la pièce fait 8 m sur 6 » et « un lit de 3 m » sans autre indication | aucune dimension de pièce, dimension de meuble hors catalogue signalée et ramenée au catalogue | unitaire + essai réel |
| Intention : pas de coordonnées | un modèle qui renvoie des positions | positions ignorées, avertissement | unitaire |
| Générateur : validité | sur les combles de test (`tests.factories.attic_project`) et la chambre de la fabrique, brief « lit double + bureau + rangement » | 3 variantes valides distinctes en < 5 s ; propriété : 0 collision, passage ≥ 0,80 m, aucun meuble au-dessus de la hauteur libre | unitaires + test de propriété (graines aléatoires) |
| Générateur : honnêteté | pièce trop petite pour le brief | le meuble manquant est nommé dans `warnings`, la variante reste valide | unitaire |
| Utile pour augus | ses combles réels (projet « Grenier »), brief « chambre avec bureau » | au moins une variante jugée « point de départ acceptable » par augus ; verdict publié tel quel, même négatif | essai manuel, protocole |
| Temps et coût | brief local complet (modèle + générateur) | ≤ 60 s sur la machine d'augus, 0 € | mesuré et publié |

## Découpage proposé (un message PO par sous-incrément)

1. **3.1 catalogue** : `openplan/catalog/` (chargement, validation des licences, dimensions
   min/défaut/max), `assets/catalog.yaml` (dix meubles CC0), docs licences ; meubles dessinés
   dans le plan (rectangle coté, orientation) et en 3D (boîte) ; panneau Inspecteur pour un
   meuble (dimensions, rotation, « à conserver »). Demi-journée.
2. **3.2 contraintes** : collisions (Shapely), débattement de porte, passage ≥ 0,80 m (graphe
   de visibilité sur le polygone libre), `keep_clear`, fenêtre ; violations lisibles dans le
   rapport existant. Demi-journée.
3. **3.3 intention** : `schemas/layout.py` (`LayoutBrief`), `adapters/remote/intention.py`
   (prompt, schéma, réessai), route `POST /projects/{id}/levels/{lid}/brief`, formulaire dans
   le panneau IA, brief affiché et éditable ; tests avec modèle simulé ; essai réel Ollama puis
   Gemini consenti, résultats au protocole. Une journée.
4. **3.4 générateur** : `openplan/layout/` (candidats, score, recherche locale, N variantes),
   route `POST …/layouts`, comparaison des variantes dans le plan, « Appliquer cette variante ».
   Une à deux journées.

Ce que cette note ne décide pas : le rôle Critique (3.5), le rendu Blender des variantes
(3.7), tout catalogue externe, tout modèle payant.

## Décision (PO, 25 septembre 2026, 15:15)

Note validée : le modèle produit un brief sans coordonnées, le moteur géométrique local place,
rien n'entre dans la scène sans « Appliquer ». Ordre retenu : **3.1 catalogue → 3.3 intention
→ 3.2 contraintes → 3.4 générateur** (le brief devient visible vite ; 3.3 affiche le brief sans
placement). `ollama pull qwen2.5:3b-instruct` autorisé si `qwen2.5vl:3b` rate le schéma, mêmes
garde-fous, taux d'échec du premier à communiquer. Onzième meuble : **rangement bas sous pente**
(commode basse ≤ 0,90 m, ancrée au mur d'égout). Deux exigences : 1) le formulaire dit en clair,
avant envoi, ce qui part au modèle (texte + description de la pièce, jamais la photo) et vers où
(local / Google) ; 2) chaque variante affichée montre son score et ses violations souples en une
phrase lisible. Livraison par sous-incrément avec les chiffres des critères ; augus garde un
droit de veto.

Résultats : 3.1 livré (onze meubles CC0). 3.3 livré le 25 septembre 2026 : `qwen2.5vl:3b`
déjà installé donne 10 / 10 briefs valides sans réessai (44 s pour onze), piège tenu ;
`qwen2.5:3b-instruct` n'a donc pas été téléchargé (protocole §3 nonies). 3.2 livré le
25 septembre 2026 : les contraintes dures (collision, mur, débattement de porte) et souples
(fenêtre, dégagements, passage de 0,80 m) sont en place dans `validation/layout.py`, prêtes
pour le générateur 3.4. 3.4 livré le 27 septembre 2026 : `pipeline/layouts.py`, moteur
géométrique local sans modèle (le brief relu, même composé à la main sans aucun modèle, donne
trois variantes valides et distinctes en moins d'une seconde, chacune avec son score et sa
phrase : meubles placés, souhaits tenus ou manqués, remarques souples — exigence PO n° 2) ;
« Appliquer cette variante » est un geste de l'utilisateur, annulable (protocole §3 decies).
