# 2.4 Ouvertures (portes, fenêtres) : candidats avant tout code

Note de cadrage du 25 septembre 2026, à valider par le PO avant tout téléchargement de
poids (même règle que pour l'image d'inspiration). Licences relues sur les fiches Hugging
Face le jour même ; tailles en poids déduites du nombre de paramètres (fp32), à confirmer au
téléchargement.

## Ce que l'on cherche

Pour chaque photo : les portes et fenêtres visibles, en rectangle image, puis leur projection
sur le mur hôte du plan → `Opening` de source `observed` (largeur, hauteur, allège, position le
long du mur). Critère d'acceptation (roadmap 2.4) : sur la fixture rendue, porte et fenêtre
trouvées, largeur à ±15 % ; sur photos réelles, rappel publié tel quel.

## Ce qui existe déjà

| Moyen | Ce qu'il donne | Limites |
|---|---|---|
| Rôle **Perception** (Gemini 3.5-flash-lite, consentement par projet) | rectangles porte/fenêtre bien cadrés (essai du 24 sept. : fenêtre exacte sur la fixture, fenêtre et mur de pierre du grenier) | distant : photo envoyée chez Google, palier gratuit non garanti ; pas de projection sur le mur |
| Rôle Perception (Ollama `qwen2.5vl:7b`, local) | rectangles utilisables | ≈ 3 min par photo sur processeur, 6 Go, pas installé par défaut |
| Heuristique lignes (`heuristic_box`) | arêtes sol-mur seulement | aucune ouverture |
| Profondeur (Depth Anything) | plans sol/murs | une fenêtre est dans le plan du mur : invisible en profondeur ; une porte ouverte fait un trou |

La détection locale, gratuite et sans consentement manque : c'est l'objet de 2.4.

## Candidats

| Candidat | Licence (fiche) | Paramètres / poids | VRAM attendue | Zéro-shot (« a door », « a window ») | Intégration | Verdict |
|---|---|---|---|---|---|---|
| **OWLv2 base** `google/owlv2-base-patch16-ensemble` | apache-2.0 | 0,2 Md ≈ 0,6 Go fp32 (≈ 0,3 Go fp16) | ≈ 1,5 Go à 960 px ; processeur 5 à 15 s | oui, natif (requêtes texte) | `transformers` déjà installé (extra `neural`), `Owlv2ForObjectDetection` | **recommandé** : le plus simple, licence claire, pas de réglage |
| Grounding DINO tiny `IDEA-Research/grounding-dino-tiny` | apache-2.0 | 0,2 Md ≈ 0,7 Go | ≈ 2 Go ; processeur 10 à 30 s | oui (BERT + Swin) | `transformers` (`AutoModelForZeroShotObjectDetection`) | solution de repli si OWLv2 rappelle mal les fenêtres ; plus lourd |
| OWLv2 large `google/owlv2-large-patch14-ensemble` | apache-2.0 | 0,4 Md ≈ 1,6 Go | ≈ 3 Go | oui | idem | seulement si la base ne suffit pas : GPU 6 Go partagé avec la profondeur et l'inspiration |
| RT-DETRv2 (`PekingU/rtdetr_v2_r18vd`) | apache-2.0 | 20 M | faible | **non** : classes COCO, ni porte ni fenêtre | fine-tuning nécessaire (jeu de données à trouver, licences des données) | écarté pour 0.2 |
| SAM 2 hiera small (déjà au registre, `planned`) | apache-2.0 | 46 M, 185 Mo | ≈ 2,5 Go | non : segmente ce qu'on lui pointe | utile ensuite pour affiner le contour d'une ouverture détectée | pas un détecteur, plus tard |
| Heuristique sans poids : rectangles verticaux des familles de points de fuite + contraste + trou de profondeur (porte ouverte) | Apache-2.0 (code du dépôt) | 0 | 0 | — | 2 à 3 jours, fragile (placards, tableaux, miroirs) | repli si aucun poids n'est accepté |

Exclus d'office : tout modèle non commercial (CC-BY-NC), AGPL (YOLOv8 d'Ultralytics), et les
services distants comme détecteur par défaut.

## Précision attendue et garde-fous

- OWLv2 en zéro-shot rappelle bien les portes et fenêtres visibles de face ou en biais modéré ;
  faux positifs connus : tableaux, miroirs, portes de placard, écrans. Filtrage géométrique prévu :
  rectangle dont les bords verticaux suivent la verticale des points de fuite, pied au sol pour une
  porte (bord bas sur l'arête sol-mur), allège au-dessus du sol pour une fenêtre, largeur projetée
  plausible (0,6 à 2,5 m). Ce qui ne passe pas le filtre est proposé quand même, à confiance basse.
- Projection : rectangle image → mur hôte le plus proche dans le repère des points de fuite (arête
  sol-mur déjà connue) → position le long du mur et largeur en mètres, à l'échelle de la hauteur de
  prise de vue, donc mêmes ±15 % que le reste tant que rien n'est mesuré ; hauteur d'allège depuis
  le bord bas. Source `observed`, confiance = score du modèle × filtre.
- Aucun envoi réseau : poids récupérés une fois par `openplan models pull owlv2_base@1.0`
  (entrée `planned` au registre jusqu'à l'accord), `HF_HUB_OFFLINE=1` ensuite.

## Découpage proposé

1. **2.4a** : adaptateur `adapters/neural/openings.py` (OWLv2, requêtes « a door », « a window »,
   seuil 0,2, fusion des boîtes chevauchantes), entrée de registre, `models pull`, test unitaire avec
   détecteur simulé et test d'intégration sur la fixture rendue (porte + fenêtre, ignoré sans poids).
2. **2.4b** : projection sur le mur hôte → `Opening observed`, route `POST …/images/{img}/openings`
   et bouton « Portes et fenêtres », rapport avec superposition ; largeur à ±15 % sur la fixture ;
   lignes `fenetre_largeur` dans `openplan evaluate`.

## Décision (PO, 25 septembre 2026, 14:15)

OWLv2 base retenu, téléchargement autorisé par `openplan models pull owlv2_base@1.0` avec les
garde-fous habituels (registre, licence affichée, jamais livré). Grounding DINO tiny seulement
si le critère de la fixture n'est pas atteint, en prévenant avant. Pas de repli heuristique pour
l'instant. Pour 2.4b : les ouvertures détectées restent `observed` et corrigeables, jamais
imposées ; le rapport dit ce qui a été filtré et pourquoi. Résultat 2.4a : critère atteint
(protocole §3 octies), Grounding DINO non nécessaire.

Résultat 2.4b (25 septembre 2026) : `pipeline/openings.py`, ouvertures `observed`
corrigeables, sept motifs de filtrage nommés dans le rapport et l'interface, largeur à +8 %
sur le rendu habillé (critère ±15 % atteint). Reste à mesurer : le rappel sur photos réelles.
