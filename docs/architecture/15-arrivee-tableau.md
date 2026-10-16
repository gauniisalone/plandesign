# 15 · Arrivée sur le tableau (chantier T)

**Principe (augus, 15 octobre 2026) : un projet = un tableau ; l'utilisateur centralise tout
sur le tableau.**

Directive d'augus (15 octobre 2026, ~14:55) : « Arrivé sur plandesign.fr, on atterrit directement
sur le tableau plutôt que l'input texte de contexte. Le tableau a un + pour nouveau projet.
L'utilisateur peut rentrer ses exigences, ses photos d'inspiration et réelles, tout ceci
directement sur le tableau. Il peut faire lui-même le plan ou notre outil de génération de plan
le fait pour lui. »

Livrable de cette note : maquette (`maquette.html`, captures `maquette_gallery.png`,
`maquette_empty.png`, `maquette_filled.png`, dans le scratchpad du chantier), correspondance
avec l'accueil actuel, lots estimés. **Maquette validée par augus le 15 octobre (~15:50)**,
décisions au § 6 ; code T1 → T6 dans l'ordre. Rien sur le VPS.

## 1. Ce que montre la maquette

| Écran | Contenu |
|---|---|
| Galerie (`#gallery`) | « Vos tableaux » : une carte par projet dont la vignette est le tableau lui-même (photos, note, plan, rendus en miniature), état et date ; première carte « + Nouveau projet ». Pied : mentions de confidentialité réelles (machine / Gemini texte seul). plandesign.fr (B1+) arrivera ici après connexion. |
| Tableau vide (`#empty`) | Créé par « + ». Note **Exigences** (mêmes champs que les questions guidées : espace, surface, chambres, style, budget, texte libre, « plus d'options » (format, lumière, ambiance, meubles voulus, à éviter), case Gemini texte seul) ; cadres **Photos de la pièce** et **Inspirations** vides avec consigne de dépôt ; cadre **Plan** vide avec deux boutons : « ✎ Tracer moi-même » et « Générer le plan » ; cadre **Rendus** vide. Barre du haut : « Ajouter une source ＋ » (DXF, PDF, images, comme aujourd'hui), assistant « / », indicateur d'enregistrement. |
| Tableau rempli (`#filled`) | Exigences remplies avec la ligne « compris : grenier · chaleureux · 2 lits · bois · lin » (lecteur actuel), deux photos réelles (multi-photos), deux inspirations cochées « Référence » (A2), plan « pas encore » avec « Générer le plan et les rendus », et la barre du **déclencheur** : bascule « Générer automatiquement au dépôt d'une photo » (reco PO, activée par défaut) **et** bouton « Générer maintenant ». Les deux variantes sont là pour qu'augus tranche. |

## 2. Correspondance avec l'accueil actuel

| Aujourd'hui (accueil `Home.tsx`) | Demain (tableau) | Ce qui change dans le code |
|---|---|---|
| Titre « Que voulez-vous concevoir ou planifier ? », onglets Texte libre / Questions guidées | Note **Exigences** du tableau (champs guidés + texte libre dans une seule note, « plus d'options » replié) | nouvelle sorte d'élément `requirements` (ou note à champs) ; le schéma `GuidedAnswers` existant est réutilisé tel quel |
| Zone de texte « Votre projet » / « Vos envies et contraintes » (4 000 car.) | Texte libre de la note Exigences | identique (même `pending_instruction`) |
| Case « Mieux comprendre mon texte avec Gemini — texte seul » | Même case, dans la note Exigences | identique (`home_settings.gemini_text`) |
| Ligne « Compris : … (phrases simples / Gemini) » | Même ligne, en tête de la note, mise à jour après une pause dans la frappe | identique (`/home/read` ou équivalent) |
| « Importer un plan ou des photos » + glisser-déposer sur la carte, puces de fichiers | Dépôt dans les cadres **Photos de la pièce** (réelles) et **Inspirations** (références d'allure) ; « Ajouter une source ＋ » en haut pour DXF/PDF/images | le tableau sait déjà déposer/coller (B2, C1) ; une **sorte de cadre** décide : photo réelle → image du projet (plan), inspiration → image jointe cochée Référence |
| Bouton « Envoyer » (crée le projet, lance le chemin zéro geste) | Bascule « Générer automatiquement au dépôt d'une photo » **ou** bouton « Générer le plan (et les rendus) » dans le cadre Plan | le chemin zéro geste existant (`autopilot`) est lancé depuis le tableau ; la bascule est un réglage du projet (par défaut : selon décision d'augus) |
| Plan : construit automatiquement (photo → estimé ; texte → proposé, L3) | Cadre **Plan** : « Générer le plan » (même chemin) ou « ✎ Tracer moi-même » (outils du cadre, déjà là : W2) | aucun nouveau moteur ; un état « plan vide » du cadre avec ses deux boutons |
| Écran d'arrivée / progression (« Création en cours », étapes) | Progression dans le cadre Plan puis Rendus (« 3 vues arriveront ici ≈ 3 min »), indicateur du haut | la barre d'état existante, affichée dans le tableau |
| Rendus → écran de résultats | Inchangé : cadre **Rendus** + écran de résultats (double-clic) | rien |
| Colonne « Projets » (liste, renommer, supprimer) | **Galerie de tableaux** (vignette = tableau), renommer/supprimer gardés | vignette : rendu SVG léger du tableau (positions + miniatures) côté serveur, ou capture du canvas côté client |
| Pied : mentions de confidentialité (machine / Gemini texte seul) | Même pied sur la galerie et dans le tableau | identique |
| Réglages (⚙, modèle distant) | ⚙ sur la galerie | identique |

Rien de perdu : multi-photos (cadre Photos de la pièce), DXF/PDF/SVG (« Ajouter une source »),
consigne gardée (texte de la note = `pending_instruction`), respect de la demande (lecteur
inchangé), case Gemini texte seul, mentions de confidentialité, écran de résultats, assistant.

## 3. Zéro geste : rester mesurable

- Avec la bascule (reco PO) : dépôt d'une photo réelle = déclencheur → plan estimé puis rendus,
  comme aujourd'hui « Envoyer » ; mesure identique (temps, gestes = 0 après le dépôt).
- Avec le bouton : un geste de plus (« Générer »), mesuré comme tel. La bascule « pas de
  génération automatique » reste disponible dans les deux cas (projets où l'on veut d'abord
  tracer).
- Un texte seul (sans photo) ne déclenche rien tout seul : « Générer le plan » produit le plan
  proposé (L3), comme aujourd'hui.

## 4. Lots proposés

| Lot | Contenu | Estimation |
|---|---|---|
| T1 | Note **Exigences** sur le tableau : élément `requirements` (champs guidés + texte + case Gemini + ligne « compris »), lecture après une pause, écriture dans `pending_instruction` / intention ; schéma de tableau 2 | 1 journée |
| T2 | Cadres **Photos de la pièce** / **Inspirations** typés : un dépôt dans l'un entre dans le projet (photo réelle), dans l'autre devient image jointe cochée Référence ; « Ajouter une source ＋ » dans la barre du tableau (DXF/PDF/images) | ½ journée |
| T3 | Cadre **Plan** vide : « Tracer moi-même » (entre dans le cadre avec les outils) / « Générer le plan » (photo → estimé, texte → proposé) ; progression dans le cadre | 1 journée |
| T4 | Déclencheur : bascule « générer au dépôt d'une photo » (réglage projet) + bouton « Générer maintenant » ; chemin zéro geste lancé depuis le tableau ; mesure (`journal`) conservée | ½ journée |
| T5 | **Galerie de tableaux** à la place de la colonne Projets : vignette du tableau, renommer, supprimer, « + Nouveau projet » → tableau vide ; l'ancien accueil disparaît (route `/` = galerie, `/p/<id>` = tableau) | 1 journée |
| T6 | Guide, mentions de confidentialité, captures, mesure à froid du zéro geste sur le tableau (photo 2 et vraie photo), CHANGELOG | ½ journée |

Total : 4 ½ journées. Ordre : T1 → T2 → T3 → T4 → T5 → T6 ; un commit et un message par lot ; e2e
et captures sur créneau.

## 5. Questions posées à augus

1. Déclencheur par défaut : bascule « au dépôt d'une photo » activée (reco PO) ou bouton seul ?
2. La note Exigences : une seule note à champs (maquette) ou champs séparés en plusieurs notes ?
3. Vignette de la galerie : rendu léger côté serveur (SVG, toujours à jour) ou capture du canvas
   (plus fidèle, prise à la fermeture) ?
4. Supprimer l'ancien accueil dès T5, ou le garder derrière une adresse le temps d'un lot ?

## 6. Décisions d'augus (15 octobre, ~15:50)

1. Déclencheur = bascule « générer automatiquement au dépôt d'une photo » **activée par défaut**,
   plus le bouton « Générer maintenant ». Zéro geste mesuré avec la bascule (0 geste) et sans
   (1 geste).
2. **Une seule note Exigences à champs** (comme la maquette) ; les notes libres restent possibles
   à côté.
3. Vignette de galerie = **rendu léger côté serveur**.
4. L'ancien accueil est **supprimé dès T5** : la route « / » devient la galerie, sans adresse de
   repli.

Règles : un commit et un message par lot, e2e sur 8010, captures 1280 clair/sombre, rien de
perdu, push après chaque commit29 vert.
