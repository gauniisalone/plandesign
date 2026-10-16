# 0.4 Hébergement, mobile, consolidation : cadrage avant tout code

Note de cadrage du 28 septembre 2026, à valider par le PO avant toute ligne de code (même règle
que les notes `docs/models/openings-candidates.md` et `intention-candidates.md`). Elle répond à
trois objectifs d'augus : **3** un front « exploitable et sécurisé » hors de la fenêtre de
développement, **4** une préparation mobile, et la **consolidation** pour qu'un inconnu installe
OpenPlan en dix minutes. Contrainte constante : **gratuit avant tout**, et les photos ne
quittent pas la machine d'augus sans son accord explicite. Les incréments 4.2 à 4.5 de la
roadmap (escaliers, plusieurs pièces, reconstruction multi-vues, plan photographié) restent tels
quels ; murs obliques et déplacement des meubles en 3D restent en réserve.

## Ce que l'on cherche

Qu'augus (et une ou deux personnes de confiance) ouvrent OpenPlan **depuis n'importe où**, y
compris un téléphone, **en HTTPS**, sans que les projets, photos ou clés sortent de sa machine,
sans facture, et sans perdre ce qui fait la valeur du logiciel aujourd'hui : Blender, le GPU,
les poids locaux. Puis qu'un tiers l'installe seul en dix minutes avec des messages clairs.

## Ce qui existe déjà

| Moyen | Ce qu'il donne | Limites |
|---|---|---|
| `openplan serve --web` (`make start`, `start-openplan.cmd`) | API + front construit sur **un seul port**, même origine (pas de CORS à ouvrir), écoute `127.0.0.1:8000` par défaut | HTTP seulement ; le front est reconstruit à chaque `make start` (30 s) |
| Jeton (`OPENPLAN_API_TOKEN`, `Settings.local_only`) | hors boucle locale, `Bearer` obligatoire (comparaison à temps constant, jamais dans l'URL, `/api/health` public) ; le front le demande une fois et le garde en `sessionStorage` | un seul jeton pour tout le monde ; pas de comptes, pas de rôles, pas de journal des accès |
| Vie privée | EXIF retiré à l'import (seule la focale, si autorisée), photos jamais envoyées à un modèle, consentement par projet pour un modèle distant, journal réseau sans contenu, `OPENPLAN_DOTENV` pour isoler les clés | rien ne chiffre `data/` au repos (disque d'augus) |
| Docker (`Dockerfile`, `docker-compose.yml`, 2.7c) | image « légère » API + front (2,3 Go), `network_mode: host` ou port publié + jeton, volumes `data/` et poids en lecture seule | **sans torch ni Blender** : profondeur, combles depuis la profondeur, inspiration, vignettes 3D répondent « non disponible » |
| Tâches de fond (`api/jobs.py`) | rendus et inspiration sans bloquer la page | un fil : deux utilisateurs en même temps attendent l'un l'autre |
| Front React/Vite | responsive jusqu'à ≈ 760 px de scène (requêtes de conteneur), Inter sans réseau, aucune dépendance externe au chargement | événements **souris** seulement dans le plan (`onMouseDown`…) : pas de tracé au doigt ; pas de manifeste PWA ; pas de prise de photo depuis l'appareil |
| Poids locaux | Depth Anything 95 Mo, OWLv2 593 Mo, SD 1.5 + ControlNet 2,7 Go, Blender 4.2 dans `~/opt` | tout est **sur la machine d'augus** ; rien de cela ne se copie sur un hébergeur gratuit sans perdre le GPU |
| Dépôt | `origin` GitHub configuré, **push jamais réussi** (pas d'identifiant dans Debian) | aucune release, aucun paquet publié |

## 1. Héberger le front « exploitable et sécurisé », gratuitement

Quatre options, chacune avec son coût, ce qu'elle garantit et ce qu'elle casse.

| Option | Coût | Sécurité (jeton, HTTPS, données) | Ce qui casse | Verdict |
|---|---|---|---|---|
| **A1. Machine d'augus + réseau privé Tailscale** (`tailscale serve` publie `127.0.0.1:8000` en HTTPS sur le réseau privé, certificat automatique) | 0 € (offre personnelle : 3 utilisateurs, 100 appareils) ; Tailscale est un service tiers (client open source, coordination propriétaire) | **rien n'est exposé sur Internet** : seuls les appareils d'augus (et invités) voient le serveur ; HTTPS de bout en bout ; jeton en plus (défense en profondeur) ; données, photos, clés restent sur la machine | rien : Blender, GPU, poids, tout tourne comme aujourd'hui ; la machine doit être allumée avec WSL lancé | **recommandé** pour l'objectif 3 : « exploitable » depuis le téléphone d'augus, « sécurisé » sans compte à gérer |
| **A2. Machine d'augus + tunnel public Cloudflare** (`cloudflared`, nom `*.trycloudflare.com` gratuit ou domaine d'augus) + **Cloudflare Access** (courriel à usage unique, gratuit jusqu'à 50 utilisateurs) | 0 € ; compte Cloudflare | HTTPS par le tunnel ; **exposé sur Internet** derrière Access + jeton ; données restent sur la machine mais le trafic passe par Cloudflare (déchiffré à leur bord) | rien côté fonctions ; risque : une URL publique + un jeton unique = tout le projet ; pas de limitation de débit dans l'API | option **si augus veut un lien à donner à des testeurs** hors de son réseau privé ; sinon A1 |
| **B. VPS gratuit** (Oracle Cloud « Always Free », ARM 4 vCPU / 24 Go, carte bancaire exigée ; Fly.io / Render : trop petits pour Blender) + Docker + Caddy (HTTPS automatique) | 0 € tant que l'offre dure ; carte exigée ; machine ARM | HTTPS via Caddy ; jeton ; **les photos et projets quittent la machine d'augus** (disque d'un tiers, à chiffrer soi-même) : contraire au principe sauf accord explicite | **pas de GPU** : inspiration SD impossible (des heures), rendus Blender et vignettes sur processeur (43 s / 12 s), profondeur et OWLv2 sur processeur (secondes à dizaines de secondes), poids à transférer (3,4 Go, licence OpenRAIL à réaccepter) ; image Docker à refaire avec torch CPU + Blender (≈ 6 Go) | **non recommandé** en premier : coûte le GPU et la promesse « rien ne sort » ; à garder pour une démo publique sans photos personnelles |
| **C. Front statique hébergé** (GitHub Pages / Cloudflare Pages) + **API locale** sur la machine de l'utilisateur | 0 € | le front public ne contient aucune donnée ; l'API reste locale ; mais page HTTPS → API `http://localhost` : toléré par Chrome et Firefox (localhost = contexte sécurisé), **bloqué depuis un téléphone** (l'API n'est pas sur le téléphone) ; CORS à ouvrir | ne répond pas à l'objectif « depuis le téléphone » ; deux origines à tenir à jour ensemble (versions front/API) | **non retenu** ; utile seulement comme vitrine sans API |
| **D. Docker existant** (image légère) sur une autre machine du foyer ou un VPS | 0 € | comme B ou A selon la machine | sans torch ni Blender (par construction) | complément de A/B, pas une option en soi |

Ce que l'API doit gagner pour A1/A2, quel que soit le choix (petit, testable) :

- `openplan serve --host 0.0.0.0` reste possible mais **refuse de démarrer sans jeton** (déjà
  le cas hors boucle locale : à vérifier par un test et à dire dans le message de démarrage).
- **Jeton par invité** plutôt qu'un jeton unique : liste de jetons nommés (`OPENPLAN_API_TOKENS`
  « augus:…, marie:… »), le nom apparaît dans l'historique du projet (acteur `user:marie`) et dans
  un journal des accès minimal (date, nom, route ; jamais le jeton ni les photos). Révocation =
  retirer une ligne. Zéro dépendance.
- **Limitation de débit** simple sur les routes coûteuses (rendus, inspiration, modèles) : une
  tâche de fond à la fois par jeton, refus 429 explicite au-delà.
- **En-têtes** de sécurité (`Content-Security-Policy` sans domaine externe, `X-Frame-Options`,
  `Referrer-Policy`) : le front n'a aucune dépendance externe, la CSP peut être stricte.
- Page **/api/health** enrichie (déjà publique) : version, périphérique, Blender, poids présents,
  pour que l'écran d'accueil dise ce qui marche sur cette installation.
- Documentation `docs/guide/heberger.md` : A1 pas à pas (installer Tailscale sous Windows et dans
  WSL, `tailscale serve`, ouvrir depuis le téléphone), A2 en annexe, ce qui ne marche pas.

Ce qui reste hors périmètre : comptes et mots de passe (Tailscale ou Access les remplacent),
chiffrement au repos (disque d'augus), multi-utilisateurs simultanés sur un même projet
(dernier écrit gagne ; à signaler dans la doc).

## 2. Préparation mobile

| Piste | Coût | Faisable gratuitement | Ce qu'elle donne | Limites |
|---|---|---|---|---|
| **PWA depuis le front actuel** (`vite-plugin-pwa`, MIT : manifeste, icônes, service worker qui met en cache le front, jamais l'API) | 0 € | oui, quelques heures | « Ajouter à l'écran d'accueil » sur Android et iOS, plein écran, chargement instantané ; **prise de photo** par `<input type="file" accept="image/*" capture="environment">` : l'appareil photo s'ouvre, le fichier arrive avec ses **EXIF** (focale 35 mm lue à l'import, comme aujourd'hui ; Android et iOS Safari gardent la focale, iOS retire la position GPS : tant mieux) | exige **HTTPS** (donc l'option A1/A2) ; iOS : pas de notification, cache limité, l'installation passe par le menu Partager ; pas d'accès natif au LiDAR |
| Appli native (Capacitor, MIT : même front dans une coque) | Android : 25 $ une fois pour le Play Store, **APK gratuit** hors magasin ; iOS : 99 $ / an (compte développeur) + Mac pour compiler | Android oui, iOS non sans payer | accès natif à l'appareil photo, aux fichiers, notifications ; plus tard : capteurs de profondeur | double chaîne de construction, mises à jour par magasin ; rien que la PWA ne donne pas pour l'usage actuel |
| Éditeur au doigt | 0 € | oui | tracer et déplacer dans le plan sur écran tactile : passer le canevas aux **pointer events** (`onPointerDown` au lieu de `onMouseDown`, `touch-action: none`), pincer pour zoomer, barres repliées sous 600 px | une refonte des gestes du plan (murs, sommets, cotes, meubles, caméras) : à tester sur le téléphone d'augus |
| Mode « photo d'abord » sur téléphone | 0 € | oui | écran Importer plein écran : photo → hauteur de prise de vue → « Plan depuis la profondeur » → 3D ; les réglages experts restent dans Paramètres avancés | la profondeur et les combles tournent **sur la machine d'augus** (via A1), pas sur le téléphone : 2 à 10 s de plus pour l'aller-retour photo |

Recommandation : **PWA d'abord** (objectif 4 atteint sans un euro ni compte développeur),
appli native seulement si un besoin natif apparaît. Le front reste unique.

## 3. Consolidation : installer OpenPlan en dix minutes

Aujourd'hui, un inconnu doit : installer WSL2 + Debian, cloner le dépôt (impossible sans push
GitHub), installer uv, Node, construire le front, éventuellement Blender et les poids. C'est une
heure pour un développeur, hors de portée d'un non-développeur. Ce qui manque, par ordre d'effet :

| Manque | Réponse proposée | Coût | Effet |
|---|---|---|---|
| Aucune publication du code | **débloquer le push GitHub** (augus : `gh auth login` ou jeton d'accès personnel dans Debian, une fois) ; dépôt public ou privé selon augus | 0 € | condition de tout le reste (releases, `pip install`, issues) |
| Installation en plusieurs outils | **paquet Python avec le front construit dedans** (`apps/web/dist` copié dans le wheel) publié sur PyPI ; installation en une ligne : `uv tool install openplan-studio` (uv s'installe lui-même en une ligne) puis `openplan serve --web` ; sans Node, sans clone | 0 € (PyPI gratuit, GitHub Actions gratuit pour construire) | les dix minutes deviennent réalistes sous Linux, macOS, WSL ; Windows natif : Python 3.12 + uv suffisent pour le cœur (sans Blender ni torch), à vérifier (OpenCV, ezdxf, reportlab portent bien) |
| Options lourdes opaques | `openplan doctor` : dit en une page ce qui est là (Python, GPU, Blender, poids, port libre) et **la commande** pour chaque manque (`openplan models pull …`, lien Blender, `make setup-neural`) ; l'accueil du front affiche la même chose en clair | 0 € | messages avant les erreurs |
| Windows sans WSL | `start-openplan.cmd` suppose WSL et le dépôt ; proposer un `OpenPlan.cmd` qui installe uv puis le paquet PyPI dans un dossier utilisateur (pas d'administrateur), et lance le navigateur | 0 € | double-clic pour un non-développeur ; Blender/torch restent optionnels et expliqués |
| Docs dispersées | `README` court (trois commandes, capture, ce qui est gratuit et local), `docs/guide/installer.md` (dix minutes chronométrées sur une machine vierge), `heberger.md` (A1), `mobile.md` (PWA), FAQ des messages d'erreur | 0 € | le premier essai ne demande pas de lire le dépôt |
| Aucune version | numéro de version affiché (déjà `/api/health`), `CHANGELOG.md`, release GitHub par étiquette | 0 € | savoir ce que l'on a installé |

## Garde-fous

- Rien ne quitte la machine d'augus sans son accord : A1 par défaut ; B seulement sur décision
  explicite et sans photos personnelles.
- Le jeton n'apparaît jamais dans une URL, un journal ou un message (règle existante) ; un
  journal des accès ne contient ni jeton ni contenu.
- Aucun service tiers dans le front (pas de police, pas d'analytique) : la CSP le garantit.
- Le service worker ne met en cache que le front, jamais les réponses de l'API (projets, photos).
- Les poids ne sont jamais publiés (PyPI reçoit le code et le front, pas les modèles) ; les
  licences (OpenRAIL) restent à accepter par l'utilisateur.

## Critères d'évaluation

| Critère | Mesure | Seuil |
|---|---|---|
| Hébergement | augus ouvre OpenPlan depuis son téléphone, hors de chez lui, en HTTPS, via A1 | fonctionne ; aucune connexion entrante depuis Internet visible sur la machine (netstat) |
| Sécurité | test automatisé : sans jeton hors boucle locale → 401 ; jeton nommé → acteur dans l'historique ; 429 sur une seconde tâche lourde ; en-têtes présents | verts |
| Mobile | PWA installée sur le téléphone d'augus (Android ou iOS à préciser) ; photo prise depuis l'écran Importer arrive avec sa focale (si l'appareil l'écrit) | plan depuis la profondeur lancé depuis le téléphone |
| Consolidation | machine vierge (WSL Debian neuve ou VM) : `uv tool install` + `openplan serve --web` + premier projet | **≤ 10 minutes** chronométrées, hors téléchargement de Blender et des poids |
| Gratuit | facture totale | 0 € (hors abonnement Internet d'augus) |

## Découpage proposé (un message PO par sous-incrément)

1. **4.6 sécurité de l'API pour l'hébergement** : jetons nommés, journal des accès minimal,
   429, en-têtes, refus de démarrer exposé sans jeton, `/api/health` enrichi, tests. Demi-journée.
2. **4.7 guide d'hébergement A1** : Tailscale pas à pas (Windows + WSL), `tailscale serve`,
   vérification depuis le téléphone d'augus, annexe A2. Une heure, puis l'essai réel d'augus.
3. **4.8 PWA** : manifeste, icônes, service worker (front seul), bouton « Prendre une photo »
   sur l'écran Importer, test de la focale EXIF sur une photo de téléphone. Demi-journée.
4. **4.9 éditeur au doigt** : pointer events, pincer-zoomer, barres repliées sous 600 px,
   scénario Playwright en émulation mobile. Une journée.
5. **4.10 installation en dix minutes** : push GitHub (augus), paquet avec front inclus, PyPI par
   GitHub Actions, `openplan doctor`, `OpenPlan.cmd` sans WSL, README et guides, CHANGELOG. Une à
   deux journées, plus le chronomètre sur machine vierge.

Ordre proposé : 4.6 → 4.7 (augus teste depuis son téléphone) → 4.8 → 4.10 → 4.9. La
reconstruction (4.2 à 4.5) et les réserves (murs obliques, meubles en 3D) reprennent ensuite ou
sur demande.

## Questions ouvertes pour augus

1. **Qui doit y accéder ?** Vous seul depuis vos appareils (A1 suffit), ou aussi des testeurs
   sans compte Tailscale (A2 avec Cloudflare Access) ?
2. **Un service tiers pour le réseau privé est-il acceptable ?** Tailscale (client libre,
   coordination chez eux, aucun de vos fichiers n'y transite, seulement les clés de connexion).
   Sinon : WireGuard nu (gratuit, plus de réglages) ou rien d'autre que le réseau local.
3. **Vos photos peuvent-elles un jour être sur un serveur tiers** (option B) ? Réponse attendue :
   non ; à confirmer pour clore B.
4. **Téléphone** : Android ou iPhone ? (l'installation d'une PWA et la lecture EXIF diffèrent.)
5. **Dépôt GitHub** : public (releases, PyPI, issues) ou privé ? Et pouvez-vous vous authentifier
   une fois dans Debian (`gh auth login`) pour débloquer le push ?
6. **Windows sans WSL** vaut-il l'effort maintenant (cœur sans Blender ni torch) ou plus tard ?
7. **Nom du paquet** : `openplan-studio` sur PyPI, si le nom est libre au moment de publier.

## Décision (PO, 28 septembre 2026, 17:50)

Q3 confirmée par la vision du projet : les photos ne vont jamais sur un serveur tiers, option B
close. Q6 (Windows sans WSL) : plus tard, hors 0.4. Q7 : `openplan-studio` si libre sur PyPI,
sinon `openplan-studio-app`. Questions 1, 2, 4, 5 posées à augus. **Go 4.6** (sécurité de
l'API), indépendant des réponses ; 4.7 attend le retour d'augus.

Résultat 4.6 (28 septembre 2026) : jetons nommés (`openplan token <nom>`, acteur `user:<nom>`),
journal des accès sans jeton ni contenu, une tâche lourde à la fois par utilisateur (429),
en-têtes de sécurité et CSP stricte, refus de démarrer exposé sans jeton (testé), `/api/health`
enrichi.

Résultat 4.10a (28 septembre 2026) : `openplan doctor` (ligne de commande, `/api/doctor`,
accueil) avec la commande pour chaque manque. Résultat 4.10b : paquet `openplan-studio` avec
le front et les ressources embarqués, installable par `uv tool install` sans Node ni clone,
guide de dix minutes, workflows CI / release prêts pour le jour où le dépôt est poussé.

Résultat 4.8 (28 septembre 2026) : PWA (manifeste, icônes, service worker limité au front,
CSP respectée), « Prendre une photo » sur l'écran Importer, guide `docs/guide/mobile.md` ;
l'installation sur le téléphone d'augus attend l'adresse HTTPS de 4.7.

Résultat 4.9 (28 septembre 2026) : éditeur au doigt (pointer events, pincer-zoomer, barres
étroites) avec un scénario en émulation tactile. Restent : 4.7 (retour d'augus) et la
publication PyPI (push GitHub).
