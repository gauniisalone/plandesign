# PlanDesign sur un téléphone (PWA)

Le front est une application web installable (« PWA ») : depuis le navigateur du téléphone,
« Ajouter à l'écran d'accueil » donne une icône, le plein écran et un chargement instantané.
Rien ne change côté données : tout reste sur la machine qui fait tourner PlanDesign.

## Ce qu'il faut

- **HTTPS** (ou `localhost` sur la machine elle-même) : les navigateurs n'installent une PWA et
  n'ouvrent l'appareil photo qu'en contexte sécurisé. Depuis un téléphone, c'est le guide
  d'hébergement (réseau privé Tailscale, incrément 4.7) qui fournit l'adresse HTTPS.
- PlanDesign servi par `openplan serve --web` (le manifeste, les icônes et le service worker sont
  dans le front construit).

## Installer

| Téléphone | Geste |
|---|---|
| Android (Chrome, Edge, Samsung Internet) | ouvrir l'adresse → menu ⋮ → **Installer l'application** (ou la bannière « Ajouter PlanDesign à l'écran d'accueil ») |
| iPhone / iPad (Safari) | ouvrir l'adresse → bouton **Partager** → **Sur l'écran d'accueil** |

## Prendre une photo

Sur l'écran **Ajouter une source**, **Prendre une photo** ouvre l'appareil photo (sur ordinateur, un
choix de fichier). La photo arrive avec sa focale EXIF quand l'appareil l'écrit : c'est elle
qui sert à l'estimation des dimensions ; le reste des EXIF (position, date, numéro de série)
est retiré à l'import comme pour toute photo. Ensuite, comme sur ordinateur : hauteur de prise
de vue, plan construit tout seul, 3D.

## Ce que le service worker fait, et ne fait pas

Il met en cache **le front seulement** (pages, scripts, styles, icônes) pour l'ouverture
instantanée et hors ligne de l'interface. Il ne met **jamais** en cache les réponses de l'API :
projets, photos, rendus viennent toujours du serveur ; sans réseau, l'interface s'ouvre mais dit
que l'API ne répond pas. Il se met à jour tout seul à la version suivante.

## Limites connues

- iOS : pas de notification, cache limité, installation par le menu Partager seulement.
- L'éditeur répond au doigt (incrément 4.9) : un doigt trace, sélectionne, glisse un sommet
  ou un meuble selon l'outil de la palette ; deux doigts pincent pour zoomer et déplacent la
  vue. Sur un petit écran, la palette défile sur une ligne, la barre du bas passe sur deux
  lignes et la bande des photos est masquée (les photos restent dans « Ajouter une source »). Le travail fin
  (cotes, inspecteur) reste plus confortable sur ordinateur.
- Les calculs (profondeur, Blender) tournent sur la machine qui sert PlanDesign, pas sur le
  téléphone : compter quelques secondes de plus pour l'aller-retour de la photo.
