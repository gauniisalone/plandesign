# 13 — Habillage de la scène (chantier H)

Décision PO du 9 octobre 2026, 19:35. L'essai « palier image » (7 octobre) l'a montré : le
modèle d'images ne comble pas l'écart. SDXL plafonne à 3,4 sur 5 et SD 1.5 à 2,6, contre
4,5 à 5 pour les références d'augus (`scratchpad/v1/refs` 11 à 14). L'écart vient surtout de
la scène : lits-blocs, velux en simple trou, textiles en boîtes, lumière plate. Ce chantier
habille la scène elle-même (géométrie, matières Blender, aménagement), pas l'accueil ni la
galerie. Une branche parallèle (`ux/us1-us2`) y travaille : `Home.tsx`, `RenderPanel.tsx` et
les routes de galerie ne sont pas touchés.

## Ce que montrent les références

- Lits encastrés sous la pente :
  - coffre bas en bois avec deux ou trois tiroirs, tête vers la jambette ;
  - une alcôve ou une planche-chevet, deux à quatre lits par grenier, une trémie avec
    garde-corps (12).
- Velux à ébrasement :
  - caisson profond, en planches ou en enduit clair ;
  - la lumière tombe en flaque sur le plancher (12, 14).
- Textiles :
  - plaid tissé à franges posé de biais, coussins de lin, linge souple et froissé ;
  - jute ou laine au sol.
- Lumière chaude et douce de fin de journée, ombres longues, sans zones brûlées.

## Lots, dans l'ordre

Un lot = un commit et un message au PO, avec une planche avant/après.

| Lot | Contenu | Schéma, API |
| --- | --- | --- |
| H1 | Lit encastré sous pente, élément paramétrique (voir ci-dessous) | 0.13.0, nouvel élément ; relance nécessaire |
| H2 | Velux à ébrasement, flux de jour | aucun |
| H3 | Textiles et décor | aucun |
| H4 | Lumière de fin de journée | aucun |
| H5 | Densité des combles | aucun |

**H1 — lit encastré.** L'élément `BuiltInBed` est construit comme l'escalier S1 :

- champs : position, orientation, couchage de 90, 140 ou 160 cm, longueur 2,00 m, coffre de
  45 cm à 2 ou 3 tiroirs, planche-chevet optionnelle ;
- géométrie dérivée (`geom/built_in_bed.py`) : coffre, matelas, tête, et dessus du matelas à
  0,55 m ;
- règle : hauteur libre au-dessus du matelas d'au moins 0,90 m, mesurée à l'oreiller (35 cm
  de la tête) avec `clearance_at`. En dessous, l'opération refuse, et le générateur ne le pose
  pas ;
- un symbole 2D pour le plan, le SVG, le PDF et le DXF ;
- une pièce 3D procédurale : prismes du coffre, façades de tiroirs, matelas arrondi, bois du
  style ;
- un rendu Blender ;
- une carte « Couchage » dans la bibliothèque, une fiche dans l'inspecteur, un geste
  « Ajoute un lit encastré sous la pente » pour l'assistant ;
- côté générateur : dans des combles, une zone couchage pose des lits encastrés tête vers la
  jambette quand la bande basse le permet, sinon le lit classique ;
- migration 0.12.0 vers 0.13.0 sans perte, car seul un type d'élément est ajouté.

**H2 — velux.**

- Caisson biais du trou à la face intérieure du rampant : linteau horizontal en haut, appui
  vertical en bas, joues perpendiculaires.
- Planches pour naturel et chalet, enduit clair pour scandinave, contemporain et industriel.
- Soleil orienté pour qu'une flaque de jour tombe dans la pièce, à l'image.

**H3 — textiles.** Sur les lits :

- plaid à franges posé de biais ;
- linge maillé, adouci et froissé (déplacement léger) à la place des boîtes ;
- coussins de lin.

Au sol, un tapis de jute devant chaque lit. Le tout varie selon le style et la densité du
décor C3.

**H4 — lumière.**

- Un vrai préréglage « heure dorée », distinct du soir : soleil bas, environ 3 200 K, ciel
  plus doux, appoint intérieur chaud.
- L'exposition automatique reste maîtresse ; la cible de luminance moyenne ne baisse pas.

**H5 — densité.** Dans les combles, le générateur vise 2 à 4 lits encastrés selon la longueur
de la bande basse, plus la trémie et le garde-corps s'il y a un escalier, comme dans les
combles d'augus.

## Mesures, à chaque lot

- Planche avant/après sur deux scènes, chacune en trois vues :
  - la copie de la photo 2 d'augus (jamais l'original) ;
  - la scène synthétique de l'essai North Star.
- Note d'allure sur 5, avec la même grille que l'essai palier image (lumière, textures,
  netteté, réalisme), sur le rendu Blender et sur la finition SD 1.5 par défaut.
- Temps de rendu par vue, qui doit rester sous 5 minutes pour le chemin entier.
- Tests unitaires de l'élément et des règles, et l'e2e complet.
- H1 et H5 : lits posés et refusés (hauteur libre), remarques d'aménagement avant/après.

## Hors champ

- Pas de nouveaux poids ni de nouveaux modèles.
- Aucun asset tiers hors CC0 : tout est procédural ou déjà au registre.
- L'accueil et la galerie appartiennent à la branche parallèle.
