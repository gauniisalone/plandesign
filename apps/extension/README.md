# Ajouter à PlanDesign (module navigateur, KAN-102)

Un clic droit sur une image d'une page web (Pinterest, un site de déco…) propose
**« Ajouter à PlanDesign »** : une petite fenêtre demande le projet et le node Moodboard, puis
l'image entre dans ce Moodboard. C'est le serveur de PlanDesign qui récupère l'image, par la
même garde que « coller un lien » (KAN-98 L3) : adresses publiques seulement, 10 Mo au plus,
JPEG, PNG ou WebP reconnus à leurs octets. La page d'origine et l'adresse de l'image sont
gardées avec l'image (la page s'affiche au survol de la carte).

Manifest V3, Chrome et Firefox (121 ou plus). Aucune dépendance, aucune étape de compilation.
**Rien n'est publié sur les stores** : c'est une décision d'augus.

## Ce que le module peut faire, et rien d'autre

- Permissions : `contextMenus` (le menu sur les images), `storage` (le jeton, dans le stockage
  local du navigateur, jamais synchronisé), `activeTab`, et l'hôte de l'application seul
  (`https://app.plandesign.fr/*`).
- Aucun script dans les pages, aucune lecture de page en tâche de fond : au clic, le navigateur
  donne l'adresse de l'image et celle de la page, rien de plus.
- Il appelle l'API avec **son propre jeton**, jamais avec le cookie de session
  (`credentials: "omit"`). Ce jeton n'ouvre que deux routes : la liste des projets où vous êtes
  éditeur (avec leurs Moodboards) et l'ajout d'une image. Toute autre route le refuse.

## Installer en mode développeur

1. Dans PlanDesign : **Réglages › Compte › Extension navigateur**, donnez un nom (par exemple
   « Chrome du bureau ») puis **« Créer un jeton pour l'extension »**. Copiez le jeton
   (`pde_…`) : il n'est montré qu'une fois.
2. Chargez le module :
   - **Chrome, Edge, Brave** : `chrome://extensions`, activez le **Mode développeur**, puis
     **« Charger l'extension non empaquetée »** et choisissez le dossier `apps/extension/`.
     (Le manifeste du dossier porte les clés des deux navigateurs : Chrome signale
     `background.scripts` et `browser_specific_settings` comme ignorées, c'est attendu ; le zip
     construit pour Chrome ne les a pas.)
   - **Firefox** : `about:debugging#/runtime/this-firefox`, **« Charger un module
     temporaire… »**, choisissez `apps/extension/manifest.json` (il reste chargé jusqu'au
     redémarrage de Firefox). Dans `about:addons`, onglet Permissions du module, vérifiez que
     l'accès à `app.plandesign.fr` est autorisé.
3. Cliquez sur l'icône du module (barre d'outils, menu des extensions), collez le jeton,
   **Enregistrer** : le module vérifie le jeton et dit combien de projets il voit.
4. Sur une page, clic droit sur une image › **Ajouter à PlanDesign**, choisissez le projet et
   le Moodboard, **Ajouter au Moodboard**. Le choix est retenu pour la fois suivante.

Révoquer : dans PlanDesign, Réglages › Compte › Extension navigateur, **Révoquer** ; le module
reçoit aussitôt « Jeton refusé ».

## Construire les zips

```sh
python3 apps/extension/build.py                     # dist/plandesign-chrome-0.1.0.zip et -firefox-
python3 apps/extension/build.py --target chrome
python3 apps/extension/build.py --origin http://localhost:5173   # essai sur un serveur local
```

Bibliothèque standard de Python seulement. Chaque zip a le manifeste de son navigateur ;
`--origin` remplace l'hôte de l'application dans le manifeste et dans `config.js` (http n'est
accepté que pour `localhost` et `127.0.0.1`). Les zips vont dans `apps/extension/dist/`
(ignoré par git).

## Essai local

Avec l'application lancée avec les comptes (par exemple la configuration des e2e comptes, sur
`http://localhost:5173`), construisez le zip avec `--origin http://localhost:5173`, décompressez
-le et chargez ce dossier comme ci-dessus. Le serveur local refuse de récupérer une image d'une
adresse privée (127.0.0.1, réseau local) : essayez avec une image d'un vrai site public.

## Tests

- `apps/web/unit/extension.test.ts` (lancé par `npm run test:unit` dans `apps/web`) : la logique
  de `lib.js` (adresses acceptées, jeton, requêtes sans cookie, choix par défaut, messages) et
  les permissions du manifeste.
- `tests/accounts/test_kan102_extension.py` : jetons, droits, garde anti-SSRF, source gardée.
- `apps/web/e2e-accounts/extension-tokens.spec.ts` : créer, lister, révoquer dans l'application.

Le module lui-même (menu, fenêtre de choix) n'a pas de test de bout en bout : Playwright ne
charge pas d'extension dans le Chromium sans tête du dépôt.
