# Registre des modèles

Source machine : [`models/registry.yaml`](../../models/registry.yaml). Ce document en est la
version lisible ; un test (`tests/unit/test_model_registry.py`) vérifie que chaque entrée du
YAML est documentée ici, que les identifiants utilisés dans le code y figurent avec le statut
`implemented`, et qu'aucune entrée n'a de licence inconnue.

Règles (brief §11, CONTRIBUTING) :

- aucun poids dans le dépôt ; téléchargement uniquement par une commande explicite ;
- licence vérifiée avant intégration ; « inconnue » ou « non commerciale » = refusé par défaut ;
- un service distant (`remote: true`) n'est jamais appelé sans `privacy.allow_remote_models`
  **et** une confirmation par projet ; chaque appel est journalisé (quoi, vers où, combien
  d'octets), jamais son contenu.

## Implémentés (0.1)

| Identifiant | Rôle | Version | Licence | Poids | Mémoire | Limites | Usage commercial | Alternative locale |
|---|---|---|---|---|---|---|---|---|
| `exif_camera@0.1` | estimateur de caméra | 0.1 | Apache-2.0 (code du dépôt) | aucun | 0 | Tangage et roulis supposés nuls ; sans EXIF, focale par défaut (0,8 × grand côté) à confiance 0,2 | oui | — (déjà local) |
| `vp_camera@0.1` | estimateur de caméra | 0.1 | Apache-2.0 ; utilise LSD d'OpenCV (Apache-2.0) | aucun | quelques Mo | Exige des lignes alignées sur deux axes horizontaux au moins (pièces « Manhattan ») ; échoue sur une pièce nue ou courbe ; focale estimée seulement si deux points de fuite orthogonaux sont finis | oui | — |
| `heuristic_box@0.1` | détecteur d'éléments | 0.1 | Apache-2.0 | aucun | quelques Mo | Une seule photo ; pièce rectangulaire ; hauteur de caméra supposée ; sol et murs de teintes proches non séparés ; murs cachés placés arbitrairement ; ni portes ni fenêtres | oui | — |

## Implémenté, poids à télécharger explicitement (0.1.x)

| Identifiant | Rôle | Version | Licence | Poids | Mémoire | Limites | Usage commercial | Alternative locale |
|---|---|---|---|---|---|---|---|---|
| `depth_anything_v2_metric_indoor_small@1.0` | profondeur métrique (mètres) | Depth Anything V2 Small, variante intérieure Hypersim, conversion `transformers` (`depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf`) | Apache-2.0 (seule la Small ; Base/Large sont CC-BY-NC 4.0 et exclues) | 99 Mo, `uv run openplan models pull depth_anything_v2_metric_indoor_small@1.0` | ≈ 1,2 Go (CPU ou CUDA) | Échelle apprise sur des intérieurs synthétiques : ±10–15 % attendu, non mesuré sur photos réelles ; bornée à 20 m ; ouvertures vers l'extérieur saturent | oui | c'est l'alternative locale |
| `depth_anything_v2_metric_indoor_small_web@1.0` | profondeur métrique calculée dans le navigateur (KAN-79, en ligne) | le modèle précédent converti en ONNX fp16 (opset 17, entrées et sorties float32), exécuté par onnxruntime-web 1.30.0 (WebGPU, sinon WASM) | Apache-2.0 (celle du modèle précédent) | ≈ 50 Mo, sans téléchargement : `uv run --extra neural --with onnx --with onnxscript openplan models export-web-depth` (processeur, ≈ 10 s), servi par `GET /api/web-models/depth` | celle du navigateur (≈ 300 Mo) | Mêmes limites que le modèle du serveur ; écart avec lui mesuré sur la photo du grenier (KAN-79) | oui | c'est déjà local (le navigateur de la personne) |
| `stable_diffusion_1_5@1.5` | image d'inspiration (diffusion) | Stable Diffusion 1.5, miroir `stable-diffusion-v1-5/stable-diffusion-v1-5` (l'original RunwayML est retiré), variante fp16 | **CreativeML OpenRAIL-M** : gratuit, usage commercial et redistribution permis, restrictions d'usage (annexe A) à respecter et transmettre ; **acceptation explicite** : `uv run openplan models pull stable_diffusion_1_5@1.5 --accept-license` (décision d'augus du 24 sept. 2026) | ≈ 2,1 Go (UNet 1,72 + VAE 0,17 + encodeur de texte 0,25) ; filtre de sécurité non téléchargé | ≈ 3,5 Go GPU en fp16 avec le ContrôleNet | Sortie **générée**, jamais une mesure ni une photo ; ne respecte pas les cotes ; anglais mieux compris ; sortie non filtrée (locale) | oui, sous les restrictions OpenRAIL-M | c'est déjà local ; alternative Apache-2.0 (Kandinsky 2.2) trop lourde pour 6 Go |
| `siglip2_base@1.0` | mots de style des images d'inspiration (KAN-97, poste de rendu) | SigLIP2 base patch16 224 (`google/siglip2-base-patch16-224`), classification zéro-shot sur un vocabulaire fermé | Apache-2.0 | ≈ 1,5 Go, `uv run openplan models pull siglip2_base@1.0` | ≈ 2 Go de mémoire vive, processeur seulement (quelques secondes pour 6 images), libéré avant Z-Image | Mots de style, d'ambiance, de palette et d'objets seulement, jamais une matière de surface ; anglais (langue d'entraînement) ; au plus 8 mots | oui | c'est déjà local |
| `owlv2_base@1.0` | détecteur d'éléments : portes et fenêtres (rectangles image) | OWLv2 base patch16 ensemble (`google/owlv2-base-patch16-ensemble`), détection zéro-shot conditionnée par le texte | Apache-2.0 (fiche relue le 25 sept. 2026) | ≈ 0,6 Go, `uv run openplan models pull owlv2_base@1.0` | ≈ 1,5 Go GPU ; processeur 5 à 15 s | Zéro-shot : faux positifs connus (tableaux, miroirs, placards) filtrés par la géométrie en 2.4b ; rien en mètres par lui-même ; anglais seulement pour les requêtes | oui | c'est déjà local ; repli Grounding DINO tiny (Apache-2.0, plus lourd) |
| `controlnet_depth_sd15@1.1` | guidage de la diffusion par la profondeur | ControlNet 1.1 depth pour SD 1.5 (`lllyasviel/control_v11f1p_sd15_depth`), fp16 | CreativeML OpenRAIL-M (poids ; le code est Apache-2.0), même acceptation que ci-dessus | 0,72 Go, `uv run openplan models pull controlnet_depth_sd15@1.1 --accept-license` | compris ci-dessus | Suit la profondeur de la photo (proche = clair), poids de guidage 0,8 : structure approximative | oui, idem | idem |
| `controlnet_canny_sd15@1.1` | finition IA d'un rendu (D5) : tenue des contours | ControlNet 1.1 canny pour SD 1.5 (`lllyasviel/control_v11p_sd15_canny`), fp16 | CreativeML OpenRAIL-M (étiquette « openrail » sur la fiche), même acceptation que ci-dessus ; téléchargement accordé par le PO le 4 oct. 2026 | 0,72 Go, `uv run openplan models pull controlnet_canny_sd15@1.1 --accept-license` ; facultatif (sans lui, la finition suit la profondeur seule) | ≈ 4,8 Go GPU au total avec SD 1.5 et la profondeur, en img2img 768 px | Finition img2img à force bornée (0,35 par défaut, 0,55 au plus) sur le rendu Blender : la géométrie tient, de petits objets plats et peu contrastés (tapis) peuvent s'effacer, sortie toujours **générée** | oui, sous les restrictions OpenRAIL-M | idem |
| `ip_adapter_sd15@1.0` | références de style pour le rendu contextualisé (A2) | IP-Adapter pour SD 1.5 (`h94/IP-Adapter`, adaptateur `ip-adapter_sd15` + encodeur d'images OpenCLIP ViT-H/14) | Apache-2.0 (fiche relue le 14 oct. 2026), dépôt non protégé, pas d'acceptation à part | 2,6 Go (45 Mo + 2,53 Go d'encodeur), `uv run openplan models pull ip_adapter_sd15@1.0` ; accordé par augus le 14 oct. 2026 | ≈ 1,3 Go GPU en plus, chargé seulement quand une référence est cochée | Guide le style par des images (matières, ambiance), pas la géométrie : poids 0,6 par défaut ; sortie toujours **générée** | oui | c'est local ; sans référence cochée, génération par le texte seul |
| `z_image_turbo@1.0` | finition locale des rendus (KAN-88), moteur par défaut quand ses quatre poids sont là et qu'une carte CUDA est présente | Z-Image-Turbo (`Tongyi-MAI/Z-Image-Turbo` @ f332072) : VAE, ordonnanceur, découpeur de texte, configurations | Apache-2.0 | 0,18 Go, `uv run openplan models pull z_image_turbo@1.0` (ou `--from <dossier>` : reprise par lien d'une copie déjà là, sha256 vérifiés) | voir le transformer | Local seulement ; sortie toujours **générée** ; inspiration laissée à SD 1.5 | oui | SD 1.5 (chaîne d'avant, `OPENPLAN_FINISH_ENGINE=sd15`) |
| `z_image_turbo_gguf_q4km@1.0` | idem, transformer | `unsloth/Z-Image-Turbo-GGUF` @ 6c80814, Q4_K_M | Apache-2.0 | 4,8 Go, même commande | pic 4,9 Gio sur la carte (blocs montés un par un, mmap, tampon épinglé, VAE en tuiles) ; garde-fou à 5 000 Mio : génération arrêtée, message clair, pas de gel | ≈ 1 min par vue sur une carte de 6 Go, plus ≈ 45 s d'encodage du texte | oui | idem |
| `z_image_controlnet_union@2.1` | idem, guidage par les arêtes | `alibaba-pai/Z-Image-Turbo-Fun-Controlnet-Union-2.1` @ 5155fc5, complet 2602, 8 pas | Apache-2.0 | 6,4 Go, même commande | lu en projection mémoire, sans copie en RAM | Arêtes à 0,7 (0,9 tient mieux le plan mais lisse la pierre) | oui | idem |
| `qwen3_4b_gguf_q4km@1.0` | idem, encodeur de texte | `Qwen/Qwen3-4B-GGUF` @ bc64014, Q4_K_M | Apache-2.0 | 2,4 Go, même commande | ≈ 8 Go de RAM sur le processeur pendant l'encodage, rendus avant la génération | 512 jetons : la description entière de KAN-86 passe | oui | idem |

## Outil externe optionnel

| Identifiant | Rôle | Version | Licence | Installation | Mémoire | Limites | Usage commercial |
|---|---|---|---|---|---|---|---|
| `blender@4.2` | rendu, `.blend`, GLB | 4.2 LTS (4.5 accepté) | GPL-3.0-or-later | binaire téléchargé par l'utilisateur (`docs/contributing/setup.md`) | ≈ 1,5 Go pour l'aperçu Cycles CPU | Rendu CPU lent ; sans Blender, GLB par trimesh et pas de rendu | oui (le programme GPL est appelé en sous-processus ; seul `blender/scripts/` est GPL) |

## Prévus (0.1.x / 0.2), derrière les mêmes protocoles

| Identifiant | Rôle | Licence | Poids | Mémoire | Limites connues | Usage commercial | Alternative locale |
|---|---|---|---|---|---|---|---|
| `sam2_hiera_small@1.0` | segmentation guidée | Apache-2.0 | ≈ 185 Mo | ≈ 2,5 Go | Guidage par clics ; pas de sémantique « mur/sol » sans prompt | oui | c'est l'alternative locale |
| détecteur portes/fenêtres | détection | à choisir, licence permissive exigée | — | — | Les modèles Ultralytics (YOLOv8+) sont AGPL-3.0 : exclus (doc 04) | — | — |

## Services distants (opt-in, jamais par défaut)

| Identifiant | Rôle | Conditions | Ce qui part | Consentement | Usage commercial | Statut |
|---|---|---|---|---|---|---|
| `openai_compatible_vlm@0.1` | vision-langage, rôle **Perception** (0.1.x) : éléments en boîtes, forme et dimensions estimées de la pièce ; rôles Intention (3.3, brief texte → `LayoutBrief`) et Critique (3.5, variante → remarques) réalisés, texte seul | celles du fournisseur (OpenAI, ou Gemini via son point d'entrée compatible `…/v1beta/openai`) ; un serveur local Ollama / llama.cpp / vLLM derrière la même API n'est **pas** distant (hôte de boucle locale) | la photo réduite à 1600 px, réencodée en JPEG, EXIF retiré ; deux prompts fixes (`vlm_detector.py`) | `privacy.allow_remote_models` **et** `privacy.remote_consent` (confirmation par projet dans le panneau IA), vérifiés par `openplan.adapters.gateway` | à vérifier par l'utilisateur selon son contrat avec le fournisseur | implémenté |
| `gemini_vlm@0.1` | idem, API Gemini native (`generateContent`) | conditions Google | idem | idem | idem | prévu ; Gemini est déjà utilisable via l'entrée compatible |

Chaque appel laisse une ligne dans `<projet>/network_log.jsonl` (rôle, destination, octets,
durée, succès) sans le contenu ; `GET /api/projects/{id}/network-log` la montre.
Leur sortie est toujours une proposition `estimated` ou `generated` (score plafonné à 0,6),
jamais une mesure ; en 0.1.x la perception ne modifie pas la scène, l'utilisateur applique
ou ignore chaque suggestion.

## Ajouter un modèle

1. Vérifier la licence des poids **et** du code d'inférence ; noter la source exacte.
2. Ajouter l'entrée dans `models/registry.yaml` et une ligne ici (mêmes champs).
3. Implémenter le protocole correspondant (`openplan/adapters/protocols.py`) avec les imports
   lourds à l'intérieur des fonctions et `AdapterUnavailableError` si le paquet ou les poids
   manquent.
4. Ajouter le cas à `docs/evaluation/protocol.md` avec une métrique réellement mesurée.
