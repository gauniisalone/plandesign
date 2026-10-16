# 10 — Cadrage V1 (vocabulaire de design) et V2 (journal local)

Statut : **note de cadrage, à valider par le PO avant tout code** (4 octobre 2026).
Cible : la North Star d'augus — déposer une photo d'une pièce ou un plan 2D et obtenir
plusieurs rendus réalistes « qualité Pinterest », multi-vues, sans autre geste. Mesure
proposée : part des imports qui donnent au moins 3 vues publiables, sans geste après le dépôt,
en moins de 5 minutes.

Point de départ mesuré (état des lieux du 4 octobre, grenier d'augus, photo 2) : 3 min 09 s de
calcul de bout en bout, mais ≈ 27 à 32 gestes après le dépôt (Aménager ≈ 15, points de vue ≈ 4,
rendre et finir ≈ 6), et des rendus loin de Pinterest : 0,11 meuble/m², meubles sur 8 à 12 % du
cadre, vue à 6–8 m, ambiances qui ne changent que la suspension, le tapis et le linge.

## 1. V1 — une seule représentation de l'intention de design

### Ce qui existe et qu'elle unifie

| Aujourd'hui | Où | Devient |
|---|---|---|
| `LayoutBrief` (meubles voulus, près/loin, priorités) | `schemas/layout.py` | `DesignIntent.furniture` + zones |
| Intentions typées de l'assistant | `schemas/assistant.py` | inchangées ; une nouvelle intention `set_intent` modifie le `DesignIntent` |
| Ambiance de la pièce (C3) | `Room.metadata["ambiance"]` | `DesignIntent.style` (migré) |
| Style et prompt de la finition IA (D5) | `pipeline/finish.py` | dérivés du `DesignIntent` |
| Matière par élément | `metadata["finish"]` | défauts dérivés du style, l'override par élément reste |

### Le schéma (Pydantic, versionné, `schemas/intent.py`)

`DesignIntent` (version 1), un par pièce, rangé dans la `Room` (champ `intent`, migration de
schéma 0.3.0 → 0.4.0 qui reprend `metadata.ambiance` sans perte) :

- `room_type` : vocabulaire fermé existant (`ROOM_TYPES`) ; `usage` : texte court
  (« chambre d'amis », « bureau et lecture ») ;
- `style` : naturel, chalet, scandinave, industriel, contemporain (extensible : un style est
  une entrée de registre, pas un `Literal` figé) ;
- `mood` : chaleureux, lumineux, épuré, cosy, brut (2 au plus) ;
- `palette` : 3 à 5 couleurs nommées (le registre les traduit en RGB) ;
- `materials` : par surface — `floor`, `walls`, `gables`, `slopes`, `timber`, `furniture` —
  chacune optionnelle (sinon le style décide) ;
- `decor_density` : sobre, normal, riche ;
- `light` : heure (matin, midi, heure dorée, soir) et chaleur (froide, neutre, chaude) ;
- `furniture` : souhaités (catégorie, nombre) et à éviter ;
- `zones` : liste de zones voulues (couchage, lecture, bureau, rangement, détente) ; vide = le
  moteur décide d'après le type et la surface ;
- `views` : ensemble, zone, détail (cochés par défaut) ; format 4:3 ou 4:5 ;
- `source` et `confidence` par champ, comme toute donnée du projet (`inferred` quand le moteur
  l'a déduit, `user_defined` quand l'utilisateur l'a dit ou choisi).

Jamais de coordonnée dans l'intention : elle dit *quoi*, le moteur géométrique décide *où*.

### Traduire une consigne libre, une fois

« grenier chaleureux, bois et lin, deux lits » → `DesignIntent`, par :
1. le **modèle local** quand il est disponible (même passerelle que l'assistant, texte seul,
   jamais la photo, consentement par projet pour un modèle distant) ;
2. sinon des **phrases simples** (repli hors ligne, comme `assistant_phrases`) : mots du style,
   matières, couleurs, nombres de meubles.

La traduction est faite une fois, montrée dans la fiche « Ambiance » et corrigible champ par
champ ; elle n'est refaite que sur une nouvelle consigne. Tests : 15 consignes de référence
(françaises, variées : « chambre d'ado industrielle », « bureau scandinave lumineux sous les
toits », « salon chalet avec cheminée »…), chacune avec l'intention attendue, en mode phrases
(obligatoire, hors ligne) et en mode modèle (si présent).

### Ce que l'intention pilote (le cœur de V1)

| Champ | Consommateur | Changement |
|---|---|---|
| `room_type`, `usage`, `zones`, `furniture` | **brief par zones** (nouveau, devant le générateur) | densité cible 0,3 à 0,5 meuble/m² utile ; un grenier de 73 m² → 3–4 zones, 15–25 meubles ; zones placées par baies le long du faîtage |
| idem | générateur de dispositions | **lits et rangements bas contre les jambettes** (tête sous pente, meubles de 0,6–1,2 m dans la bande basse), plus seulement au pignon |
| `style`, `materials` | matières par surface | sol, murs, pignons, pentes, charpente (brute / blanchie / sombre) ; 3–4 matières Poly Haven CC0 de plus (bois peint blanc, béton de sol, chaux, chêne foncé), téléchargement autorisé par le PO |
| `style` | finition des meubles | recoloration des modèles Kenney (chêne clair, blanc, bois foncé, noir et bois) |
| `style`, `mood`, `palette`, `decor_density` | habillage (C3) | textiles, tirages, accents, nombre d'objets ; suspension et garde-corps |
| `light` | éclairage du rendu | préréglage de jour et température, exposition auto conservée |
| `views` | **3 vues automatiques** (nouveau) | ensemble depuis l'arrivée (porte ou trémie), vue de zone (à 3 m, à 1,2 m de haut, 24–28 mm), détail (lit et chevet, 2 m, 35 mm) ; 4:5 en option |
| tout | prompt de la finition IA | construit depuis l'intention (style, matières, ambiance) |

### Le chemin « zéro geste »

dépôt → plan automatique (existe) → intention par défaut déduite (type de pièce reconnu, style
naturel, densité normale ; tout `inferred`) → brief par zones → disposition appliquée d'office
(un commit d'historique, annulable) → 3 vues automatiques → 3 rendus enchaînés → finition.

**Finition IA : enchaînée par défaut (décision d'augus du 4 octobre vers 23:45, révise D5).**
Sur les 3 vues automatiques, la finition suit chaque rendu Blender ; l'image Blender est
toujours gardée à côté, l'image finie est marquée « générée », et tout s'annule (supprimer la
finition, revenir au rendu, couper l'enchaînement par un réglage du projet). Le bouton
« Finition IA » reste pour les vues ajoutées à la main.

Estimation sur la machine d'augus : 3 à 4 minutes, dont ≈ 1 min 30 de finition.

### Rendu IA à la place de la finition (demande d'augus) : un essai comparatif d'abord

augus demande un « rendu IA » : génération complète (txt2img) guidée par notre géométrie, comme
la piste b de la note 09, mais à partir de la scène actuelle (charpente, meubles réels,
escalier, décor). Avant de choisir, un **essai comparatif** sur le grenier d'augus (photo 2),
avec une **disposition dense par zones** (au moins 15 meubles, posée à la main pour l'essai si
le brief par zones n'est pas encore là), sur les 3 vues automatiques :

- **A — Blender + finition**, force 0,25 et 0,35 (ControlNet profondeur + arêtes, existant) ;
- **B — rendu IA** : txt2img SD 1.5 guidé par la profondeur et les arêtes (canny) de notre
  rendu, à conditionnement fort (poids ControlNet 1,0–1,2), plus une carte de segmentation si
  un ControlNet « seg » ouvert et local est disponible (sinon l'essai le dit et s'en passe) ;
  prompt construit depuis l'ambiance de la pièce.

Mesures, par vue et par méthode : temps, VRAM de pointe, **fidélité** — meubles à leur place
(boîtes de nos meubles reprojetées sur l'image, recouvrement avec ce qu'une détection OWLv2
retrouve), **nombre d'éléments inventés** (objets détectés sans meuble de la scène dessous),
charpente et ouvertures conservées — et une planche A/B des 3 vues. Poids : SD 1.5 et les deux
ControlNet sont déjà installés ; un ControlNet « seg » demanderait l'accord du PO avant
téléchargement.

Proposition du PO, à confirmer par l'essai : livrer **les deux sorties par vue** — l'image A
(cotes tenues) et l'image B marquée « inspiration, cotes non garanties ».

### Premier lot de V1 (proposition, un commit par point)

1. Schéma `DesignIntent`, migration 0.4.0 (ambiance reprise), fiche « Ambiance » lisible ;
   **couloir neutre derrière les portes** (défaut e de l'état des lieux : une porte donnait sur
   le ciel HDRI).
2. Brief par zones et densité cible ; lits et rangements bas contre les jambettes.
3. Ambiance → matières par surface et recoloration des meubles (+ matières CC0).
4. 3 vues automatiques ; « rendre toutes les vues » enchaîné.
5. Traduction de consigne (phrases + modèle local) et ses 15 tests ; prompt de finition dérivé.
6. Chemin zéro geste (finition enchaînée comprise), mesuré par la North Star sur les photos de
   `data/eval`.

L'essai comparatif A/B (ci-dessus) se fait en parallèle, sans code produit : un script dans le
dossier de travail, une planche et des chiffres ; le rendu IA n'entre dans le produit qu'après
lecture de l'essai.

## 2. V2 — journal local des demandes (pour un ajustement léger plus tard)

But : garder de quoi ajuster un jour un petit modèle ouvert à la traduction des consignes,
**sans entraînement maintenant**.

- **Où** : dans le dossier du projet, `journal/design.jsonl`, une ligne par événement.
- **Quoi** : la consigne (texte), sa traduction (`DesignIntent` et intentions de l'assistant),
  le résultat (identifiants des éléments posés, des rendus), puis ce que l'utilisateur en a fait :
  gardé (rendu conservé, variante appliquée), corrigé (champ de l'intention modifié, meuble
  déplacé), annulé (Annuler, variante écartée, rendu supprimé). Horodatage, version du schéma,
  modèle utilisé (ou « phrases »).
- **Jamais** : aucune photo, aucune image, aucune coordonnée de la photo, aucune clé, aucun nom
  de fichier personnel. Seulement du texte et des identifiants internes.
- **Rien ne quitte la machine** : pas d'envoi, pas de télémétrie ; le réglage existant
  `privacy.telemetry` reste à faux et sans effet sur le journal.
- **Désactivable** : réglage par projet `privacy.design_journal` (actif par défaut, coupé en un
  clic dans Réglages ; couper vide le journal du projet sur demande).
- **Export** : `openplan journal export <projet|--all> > demandes.jsonl` (JSONL, schéma documenté
  et versionné), pour qu'augus décide plus tard de l'usage.
- **Tests** : aucune photo ni chemin d'image dans une ligne ; réglage coupé = rien d'écrit ;
  export relisible ; une annulation est rattachée à la demande qui l'a produite.

## 3. Hors périmètre

Entraînement ou ajustement d'un modèle (plus tard, sur décision) ; LLM maison (gelé par augus) ;
mobile (gelé) ; détection d'escalier sur photo (réserve S1).

## 4. Questions au PO

1. D'accord pour ranger l'intention **dans la pièce** (une par pièce), avec migration 0.4.0 ?
2. Le premier lot ci-dessus, dans cet ordre, et l'essai A/B en parallèle ?
3. Journal V2 actif par défaut (coupable) ou inactif par défaut (à activer) ?
