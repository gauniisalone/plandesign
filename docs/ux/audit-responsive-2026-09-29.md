# Audit responsive du site (29 septembre 2026)

Directive d'augus du 29 septembre 2026 (17:27) : finition du site et responsive sur ordinateur,
mobile gelé. Cet audit précède toute correction.

## Méthode

- Script `apps/web/e2e-audit/responsive.spec.ts` (hors `make e2e`) :
  `AUDIT_LABEL=avant OPENPLAN_PORT=8010 OPENPLAN_WEB_PORT=5183 npx playwright test -c playwright.audit.config.ts`.
- 13 écrans × 6 largeurs (1920 × 1080, 1536 × 864, 1366 × 768, 1280 × 720, 1024 × 768,
  768 × 1024) = 78 captures dans `apps/web/e2e/screenshots/responsive/avant/`
  (`<écran>-<largeur>.png`), plus `audit.json` et `audit.md` (mesures automatiques).
- Écrans : 01 accueil, 02 importer (vide), 03 importer (plan DXF lu), 04 éditer sans sélection,
  05 éditer mur sélectionné, 06 meuble (catalogue), 07 meuble (fiche), 08 à 10 aménager pas 1, 2,
  3, 11 paramètres avancés, 12 plan + 3D, 13 rendre avec galerie.
- Mesures automatiques : défilement horizontal, chevauchement des zones flottantes, éléments hors
  écran, débordement des conteneurs, texte tronqué, cibles cliquables de moins de 24 px, texte de
  moins de 12 px. Elles ne voient pas le texte dessiné dans le plan : la revue à l'œil complète.
- Référence : maquette Lovable d'augus (captures du 27 septembre).

## Ce qui va bien

Aucun défilement horizontal, aucun élément hors écran, aucun conteneur qui déborde, aux six
largeurs et sur les treize écrans. Les quatre zones flottantes ne se chevauchent pas en vue
« Plan » seule. Le titre long est tronqué avec son infobulle.

## Défauts, du plus gênant au moins gênant

### Gravité 1 : chevauchements et contenus masqués

| # | Écran | Largeurs | Défaut | Capture |
|---|---|---|---|---|
| 1.1 | 12 plan + 3D | 1280, 1024, 768 | La palette d'outils recouvre l'en-tête du volet 3D (« Aperçu 3D », cases « Plafonds et toits » / « Matières » en partie illisibles à 1024). Cause : palette, barre du bas et aide-mémoire sont centrés sur toute la scène, pas sur le volet du plan (dans la maquette, ils appartiennent au volet du plan) | `12-plan-et-3d-1024.png` |
| 1.2 | 04 à 12 (éditeur) | 1366 et moins | L'aide-mémoire du canevas (coordonnées, « Glisser un sommet… ») passe sous la palette : texte barré par le bord de la palette ; en vue partagée il est coupé par la limite du volet | `04-editer-sans-selection-1366.png` |
| 1.3 | 07, 08 à 12 | toutes | Dans le plan, le nom du meuble et le nom de la pièce s'écrivent l'un sur l'autre (« Bureau 140 × 70 » sur « Pièce 1 ») ; les étiquettes des variantes fantômes se chevauchent entre elles et avec les cotes | `07-meuble-fiche-1366.png`, `12-plan-et-3d-1024.png` |
| 1.4 | 04 à 12 | 1024, 768 (petit zoom) | Les cotes des murs verticaux mordent sur le mur (« 3.15 m » lu « 8.15 m ») | `04-editer-sans-selection-1024.png` |
| 1.5 | 03 importer, plan lu | hauteur 768 et moins | La carte dépasse la hauteur de l'écran : « Continuer vers Éditer » est sous la ligne de flottaison, sans indice de défilement | `03-importer-plan-dxf-1366.png` |
| 1.6 | 12 plan + 3D | 1280 et moins | Le modèle 3D est cadré pour un volet large : il sort du volet à droite | `12-plan-et-3d-1024.png` |

### Gravité 2 : disposition incohérente selon la largeur

| # | Écran | Largeurs | Défaut | Capture |
|---|---|---|---|---|
| 2.1 | éditeur, rendre | 1024, 768 | Barre du haut : le nom du projet disparaît et les étapes, le bouton du panneau et les réglages se tassent à gauche, en laissant la moitié droite vide ; à 1024 les étapes perdent leur libellé (« 1 2 3 ») alors que la place existe | `04-editer-sans-selection-1024.png`, `13-rendre-galerie-1024.png` |
| 2.2 | éditeur | 1366 et moins | La bande des photos remonte au milieu du bord gauche alors que rien ne la gêne en bas (à 768 elle flotte au milieu du plan) | `04-editer-sans-selection-768.png` |
| 2.3 | éditeur, rendre | 1536 et moins | Le bouton violet « Afficher la 3D » perd son libellé et devient une icône qui fait doublon avec « 3D » du sélecteur de vue juste à côté | `04-editer-sans-selection-1366.png` |
| 2.4 | 13 rendre | toutes | Commandes sans objet laissées visibles : zoom et « Recadrer » grisés en 3D ; pastille « Rendre : aperçu 3D » et croix flottante dont l'effet n'est pas dit | `13-rendre-galerie-1366.png` |
| 2.5 | 13 rendre | 1366 et moins | La galerie est sous la ligne de flottaison du panneau : après un rendu, rien de visible ne change sans faire défiler (seule la ligne « Rendu terminé ») | `13-rendre-galerie-1366.png` |
| 2.6 | 02, 03 importer | toutes | Le bouton du panneau latéral est affiché actif (violet) alors que l'écran Importer n'a pas de panneau | `02-importer-vide-1366.png` |
| 2.7 | 768 | 768 | Le panneau passe sous le plan et prend 45 % de la hauteur même presque vide (niveau) ; le plan n'a plus que la moitié haute | `04-editer-sans-selection-768.png` |

### Gravité 3 : zones cliquables et lisibilité

| # | Écran | Défaut |
|---|---|---|
| 3.1 | aménager (pas 2 et 3), rendre, avancés, accueil | 19 liens-boutons de 20 à 23 px de haut (« voir sur le plan », « Avis sur cette variante », « comparer », « Vignette 3D », « retirer », « supprimer », « voir en 3D », « placer dans le plan », « télécharger », « annuler », « ouvrir les réglages avancés ») ; seuil visé : 24 px au moins, 32 px pour les actions principales |
| 3.2 | aménager pas 2 | Listes déroulantes du brief de 22 px de haut, plus petites que tous les autres champs (32 px) |
| 3.3 | importer (plan lu), fiches | Cases à cocher natives de 13 px sans zone élargie |
| 3.4 | barre du haut | Numéros des étapes en 11,5 px ; aide-mémoire et cotes du plan en 10 px |
| 3.5 | aménager pas 3 | Chaque variante répète trois fois la même phrase (dans le résumé, puis deux fois dans la liste) : mur de texte, actions noyées ; les cinq actions d'une variante passent à la ligne sans ordre (lien, bouton, lien, lien, lien) |
| 3.6 | accueil | Dates au format du navigateur (« 9/29/2026, 5:32:50 PM » dans le navigateur de test) au lieu d'un format français fixe |

### Gravité 4 : écarts avec la maquette et finitions

| # | Écran | Défaut |
|---|---|---|
| 4.1 | éditeur | Maquette : palette en haut à gauche du plan, à côté de la pastille du projet, sur deux rangées courtes ; ici une rangée centrée sous une barre pleine largeur. À trancher avec augus : garder la barre pleine largeur (choisie le 28 septembre pour le parcours Importer · Éditer · Rendre) ou revenir à la pastille |
| 4.2 | fiches | Largeurs de champs inégales dans une même fiche (« Nom » et « Unités » plus larges que les champs avec unité, « Rotation » encore différente) : bords droits non alignés |
| 4.3 | avancés | Champ de fichier natif (« Choose File / No file chosen », en anglais selon le navigateur) au lieu du bouton du thème ; « Hauteur photo » sur deux lignes ; nuage de sept boutons d'analyse sans hiérarchie ; « supprimer » isolé sur sa ligne |
| 4.4 | accueil | Séparateurs irréguliers dans le rappel des étapes (« Importer ·  2 ») ; texte de la zone de dépôt qui ne cite que « plan DXF » (PDF et SVG sont acceptés) ; cartes de projet de hauteurs égales mais « supprimer » à des hauteurs différentes ; à 1920 la grille des projets est collée à gauche d'une colonne centrée |
| 4.5 | importer | La ligne « téléphone : l'appareil photo s'ouvre… » sous « Prendre une photo » n'a plus de sens sur ordinateur depuis le gel du mobile : à masquer ou à retirer, sur décision |
| 4.6 | rendre | La vignette d'un point de vue libre posé au centre est un aplat bleu (la caméra regarde un mur de près) : elle n'aide pas à choisir |
| 4.7 | tous | États survol / actif / désactivé : le désactivé (« Créer », « Envoyer au modèle », zoom en 3D) est un simple éclaircissement, sans curseur ni infobulle d'explication partout ; à vérifier bouton par bouton au lot correspondant |

## Lots proposés

| Lot | Contenu | Défauts |
|---|---|---|
| 1 | Chevauchements et débordements évidents : zones flottantes rattachées au volet du plan en vue partagée, aide-mémoire hors de la palette, carte Importer qui défile, cadrage 3D du volet, cotes hors des murs | 1.1, 1.2, 1.4, 1.5, 1.6 |
| 2 | Étiquettes du plan sans collision (pièce, meubles, fantômes) | 1.3 |
| 3 | Barre du haut, bande des photos, barre du bas et panneau selon la largeur | 2.1 à 2.7 |
| 4 | Zones cliquables, tailles de texte, fiche Aménager allégée, dates | 3.1 à 3.6 |
| 5 | Finitions et maquette, après réponse d'augus sur 4.1 et 4.5 | 4.1 à 4.7 |

Chaque lot : captures « après » dans `responsive/lot-N/`, mêmes noms de fichiers, et le scénario
navigateur par largeur (absence de chevauchement et de défilement horizontal) arrive avec le lot 1.

## Questions pour augus

1. Palette : rester sur la barre du haut pleine largeur avec la palette centrée dessous, ou
   revenir à la disposition de la maquette (pastille du projet et palette en haut à gauche) ?
2. « Prendre une photo » et sa phrase sur le téléphone : masquer sur ordinateur, garder, retirer ?
3. À 768 de large, le panneau sous le plan convient-il, ou préférez-vous un panneau en tiroir
   par-dessus le plan ?

## Décisions (PO, 29 septembre 2026)

1. Palette : retour à la maquette (pastille du projet et palette en haut à gauche du plan), le
   bandeau des trois étapes reste visible et centré. 2. « Prendre une photo » et sa phrase :
masqués sur ordinateur, le dépôt de fichier reste. 3. À 768 de large : panneau en tiroir
par-dessus le plan, fermé par défaut. Lot 4 : dates « 28 sept. 2026 à 15:27 ». Captures
« après » dans `responsive/apres/`, mêmes noms.

## Lot 1 : résultat (29 septembre 2026)

| Défaut | État | Après |
|---|---|---|
| 1.1 palette sur l'en-tête du volet 3D | corrigé : palette dans le volet du plan, sur deux rangées si besoin | `apres/12-plan-et-3d-1024.png` |
| 1.2 aide-mémoire sous la palette | corrigé : marges mesurées, aide-mémoire 20 px sous la palette, réduit aux coordonnées dans un volet de moins de 560 px | `apres/04-editer-sans-selection-1366.png` |
| 1.4 cotes sur les murs | corrigé : écart en pixels, indépendant du zoom | `apres/04-editer-sans-selection-1024.png` |
| 1.5 carte Importer plus haute que l'écran | corrigé : zone de dépôt compacte dès qu'un plan ou une photo est là ; tout tient dans 768 px de haut | `apres/03-importer-plan-dxf-1366.png` |
| 1.6 modèle 3D hors du volet | corrigé : cadrage selon la forme du volet | `apres/12-plan-et-3d-1024.png` |
| 1.3 étiquettes du plan superposées | reporté au lot 2 | |

Garde-fou : `apps/web/e2e/responsive.spec.ts`, un scénario par largeur.

## Audit refait sur l'interface simplifiée (30 septembre 2026)

Après les directives « tout automatique » (A1 → A5) et l'assistant (B1 → B3), les captures ont été
refaites : `responsive/avant-simplifie/` (avant les lots 2 et 3) et `responsive/apres-simplifie/`
(après), 14 écrans (le 14 est la carte Recalibrer) × 6 largeurs. Mesures automatiques inchangées :
aucun défilement horizontal, aucun élément hors écran, aucun débordement. Nouveau défaut vu :
les bandeaux d'arrivée (« Plan estimé », progression) recouvraient la bande des photos
(`avant-simplifie/04-editer-sans-selection-768.png`).

## Lot 2 : résultat (30 septembre 2026, commit 543313a)

| Défaut | État | Après |
|---|---|---|
| 1.3 étiquettes du plan superposées | corrigé : `editor2d/labels.ts`, cotes fixes, pièce / meubles / fantômes décalés d'un ou deux crans ; garde-fou e2e `labelsDisjoint` | `apres-simplifie/07-meuble-fiche-1366.png` |
| (course serveur) deux gestes rapprochés s'écrasaient | corrigé : verrou d'écriture par projet (ops, undo, redo) | — |

Reste : les cotes elles-mêmes ne bougent pas ; deux cotes de murs très courts peuvent se toucher.
En vue Plan + 3D à 1024 avec le panneau ouvert, le volet du plan fait 330 px : les étiquettes des
variantes fantômes se chevauchent encore (décalage borné à deux crans, `12-plan-et-3d-1024.png`).

## Lot 3 : résultat (30 septembre 2026)

Décisions PO appliquées : palette façon maquette (en haut à gauche, sous la pastille du projet),
« Prendre une photo » masqué sur ordinateur (fait en A3), panneau en tiroir **sous 900 px** (au
lieu de 768, accepté par le PO), fermé par défaut.

| Défaut | État | Après |
|---|---|---|
| 2.1 barre du haut tassée à gauche, libellés perdus à 1024 | corrigé : étapes centrées (`margin-inline: auto`), nom visible jusqu'à 700 px de volet, libellés jusqu'à 620 px | `apres-simplifie/04-editer-sans-selection-1024.png`, `-768.png` |
| 2.2 bande des photos au milieu du bord gauche | corrigé : toujours en bas à gauche, largeur 30 % sous 1150 px | `apres-simplifie/04-editer-sans-selection-768.png` |
| 2.3 « Afficher la 3D » en icône, doublon de « 3D » | corrigé : sous 1150 px le bouton disparaît, le sélecteur Plan / Plan + 3D / 3D reste | `apres-simplifie/04-editer-sans-selection-1024.png` |
| 2.4 zoom et Recadrer grisés en 3D, pastille « Rendre : aperçu 3D », croix muette | corrigé : masqués en 3D plein écran ; pastille « Aperçu 3D » ; croix « Revenir à Éditer » | `apres-simplifie/13-rendre-galerie-1366.png` |
| 2.5 galerie sous la ligne de flottaison après un rendu | corrigé : la galerie remonte à l'écran quand un rendu arrive | (rendu Blender requis) |
| 2.6 bouton du panneau actif sur Importer | corrigé : inactif et non allumé sur Importer | `apres-simplifie/02-importer-vide-1366.png` |
| 2.7 panneau sous le plan à 768 (45 % de la hauteur) | corrigé : tiroir par-dessus le plan sous 900 px, fermé au départ, fermé / rouvert quand la fenêtre passe le seuil ; le panneau reste monté replié (Aménager et la conversation gardent leur état) | `apres-simplifie/04-editer-sans-selection-768.png`, `05-editer-mur-selectionne-768.png` |
| 4.1 palette : maquette (pastille + palette en haut à gauche) | corrigé : palette à gauche sous la rangée du haut, bandeau des étapes centré | `apres-simplifie/04-editer-sans-selection-1366.png` |
| (nouveau) bandeaux sur la bande des photos | corrigé : bandeaux placés au-dessus des barres mesurées (`insets.bottom`) | `apres-simplifie/04-editer-sans-selection-768.png` |

Reste pour les lots 4 et 5 : cibles < 24 px (3.1 à 3.3 ; l'audit en compte encore 3 à 21 par
écran, surtout la fiche Aménager pas 2 et 3), textes de 10 px (3.4), fiche Aménager répétitive
(3.5), dates françaises (3.6), largeurs de champs (4.2), champ de fichier natif (4.3), accueil
(4.4), vignette d'un point de vue libre (4.6), états survol / actif / désactivé (4.7).

## Lot 4 : résultat (1er octobre 2026)

Captures dans `responsive/lot4/`. Mesures à 1366 : cibles de moins de 24 px passées de 1 à 21
par écran à 0 à 3 ; textes de moins de 12 px de 4 à 0.

| Défaut | État | Après |
|---|---|---|
| 3.1 liens-boutons de 20 à 23 px | corrigé : 26 px au moins (`button.link`, liens de la galerie, résumés repliables) | `lot4/10-amenager-3-dispositions-1366.png` |
| 3.2 listes du brief à 22 px | corrigé : toutes les listes et champs à 32 px | `lot4/09-amenager-2-brief-1366.png` |
| 3.3 cases à cocher de 13 px | en partie : cases de 16 px dans une étiquette cliquable de 26 px ; la mesure compte encore la case seule (seuls restes de l'audit) | `lot4/12-plan-et-3d-1366.png` |
| 3.4 textes de 10 à 11,5 px | corrigé : numéros d'étapes, aide-mémoire, cotes, « voir » de la bande à 12 px | `lot4/04-editer-sans-selection-1366.png` |
| 3.5 fiche Aménager répétitive | corrigé : une phrase de synthèse par variante, détail (règles, souhaits manqués) replié, « Appliquer » en bouton principal, « voir sur le plan » à côté, le reste en liens secondaires | `lot4/10-amenager-3-dispositions-1366.png` |
| 3.6 dates au format du navigateur | corrigé : « 1er oct. 2026 à 10:56 » (accueil, galerie), heure seule dans le journal réseau | `lot4/01-accueil-1366.png` |

## Lot 5 : résultat (1er octobre 2026)

| Défaut | État |
|---|---|
| 4.1 palette façon maquette | fait au lot 3 |
| 4.2 largeurs de champs inégales dans une fiche | corrigé : colonne des unités toujours présente (`.field` sur trois colonnes fixes), bords droits alignés |
| 4.3 champ de fichier natif en anglais, « Hauteur photo », nuage de boutons d'analyse | corrigé : bouton « Ajouter une photo » (le champ natif est masqué) ; « Hauteur photo » et les boutons d'analyse ont disparu en A3 |
| 4.4 accueil : séparateurs, texte de dépôt, « supprimer », grille à 1920 | corrigé : séparateurs réguliers, « plan DXF, PDF, SVG », « supprimer » en bas de chaque carte, cartes centrées (`justify-content: center`) |
| 4.5 « Prendre une photo » sur ordinateur | fait en A3 (masqué sur pointeur fin) |
| 4.6 vignette d'un point de vue libre en aplat | corrigé : un nouveau point de vue est posé dans un coin à 0,6 m des murs et tourné vers le centre |
| 4.7 états désactivés sans indice | corrigé en partie : curseur « interdit » sur tout bouton, liste ou champ inactif ; les infobulles existantes disent pourquoi ; pas de revue bouton par bouton |
| (PO) « Score -4.0 » illisible | corrigé : verdict « Très bien / Correct / À revoir » en tête de phrase, score chiffré dans le détail replié |
| (PO) « Lit double 160 × 200 1 » | corrigé : « (à gauche) / (à droite) », « (en bas) / (en haut) », sinon « (n° 1) » de gauche à droite |

Le chantier responsive (lots 1 à 5) est terminé sur ordinateur, de 1920 à 768 px de large.
