# Cadrage : l'assistant agit sur le plan (directive 3 d'augus, 29 septembre 2026)

Note courte, avant tout code. Directive : « l'assistant IA doit pouvoir intervenir sur le plan
pour comprendre ce que veut l'utilisateur et insérer les bonnes choses ». Elle change un
principe antérieur (le modèle ne posait aucune coordonnée, rien n'entrait dans la scène sans
« Appliquer ») ; les garde-fous ci-dessous sont ceux qui restent compatibles avec elle.

## 1. Ce qui existe déjà

- **Intention** (3.3) : le souhait en texte → un brief (meubles du catalogue, priorités,
  préférences ; aucune position), relu par l'utilisateur.
- **Générateur** (3.4) : moteur géométrique local qui place le brief, trois variantes scorées,
  fantôme sur le plan, « Appliquer cette variante ».
- **Critique** (3.5) : remarques (règles locales, puis modèle si autorisé) avec action en un clic.
- Règles d'aménagement (3.2), catalogue (3.1), vérification d'un déplacement (3.6).
- Modèles : Ollama local (`qwen2.5vl:3b`, 10 briefs sur 10 valides), Gemini en option consentie
  (souvent en 503). Texte seul part au modèle ; la photo jamais sans consentement explicite.

Il manque : une **conversation** dans l'atelier, et le droit pour l'assistant d'**écrire dans
le plan** sans passer par une variante à appliquer.

## 2. Principe

Une conversation dans le panneau de droite (« Assistant »). L'utilisateur écrit ce qu'il veut
(« mets un lit 160 sous la pente et un bureau près de la fenêtre », « supprime l'armoire »,
« la pièce fait 4,20 de large »). Le **modèle** choisit l'intention et les cibles ; le **moteur**
calcule positions, dimensions et respect des règles. Le modèle n'écrit jamais une coordonnée.

Contrat : le modèle rend une liste d'**intentions typées** (schéma strict, tolérant aux champs
inconnus, comme `parse_brief`) :

| Intention | Paramètres du modèle | Ce que fait le moteur |
|---|---|---|
| `add_furniture` | référence catalogue, quantité, lieu (`Place` : sous la pente, près de la fenêtre, contre un mur…), dimensions si dites | place par le générateur (3.4) dans la contrainte du lieu ; règles dures obligatoires |
| `move_furniture` | meuble ciblé (nom ou « le lit »), lieu | déplace avec `check_furniture_move`, dos au mur le plus proche |
| `remove_furniture` | meuble ciblé | supprime |
| `set_dimension` | mur ou pièce ciblé, longueur/largeur/hauteur | `set_wall_length` ou Recalibrer (une longueur connue) |
| `add_opening` | porte/fenêtre, mur ciblé (« le mur du fond », « à gauche de la porte »), largeur | pose sur le mur, largeur standard sinon |
| `name_room` / `set_room_type` | pièce, nom ou usage | met à jour |
| `ask` | question de clarification | affichée, rien n'est fait |

Tout ce qui n'entre pas dans le tableau est répondu en texte (« je ne sais pas faire ceci ;
voici comment le faire à la main »).

## 3. Garde-fous

1. **Chaque intervention = un commit** d'historique attribué à l'acteur `assistant` (nouvel
   acteur : mêmes droits qu'un `pipeline:*`, jamais `user_defined`, ne touche pas ce qui est
   verrouillé ou mesuré), donc **annulable** d'un clic (Ctrl+Z, ou « Annuler » dans le message).
2. **Visible** : les éléments touchés sont surlignés dans le plan pendant quelques secondes, et
   le message de l'assistant résume ce qu'il a fait (« Lit 160 × 200 posé sous la pente basse,
   tête contre le mur ouest ; bureau près de la fenêtre : pas de place, voulez-vous le mettre
   contre le mur est ? »).
3. **Texte seul** part au modèle : le souhait, la description de la pièce (`describe_room`),
   la liste des meubles et ouvertures avec leurs noms. Jamais la photo sans consentement.
4. **Local d'abord** (Ollama) ; un modèle distant exige les deux drapeaux de consentement, comme
   aujourd'hui, et le journal réseau.
5. **Questions plutôt que suppositions** quand une cible est ambiguë (deux lits, « le mur du
   fond » sans caméra) : l'assistant demande, il ne devine pas.
6. Les **règles dures** restent dures : l'assistant ne pose rien dans un mur ni dans un
   débattement de porte ; il dit pourquoi et propose une alternative.
7. **Rien d'automatique sans message** : l'assistant n'agit que sur une phrase de l'utilisateur,
   jamais de sa propre initiative après un import.

## 4. Critères d'acceptation

- Dix phrases de test (fichier `tests/fixtures/assistant_phrases.yaml`) → intentions attendues
  : 9 sur 10 justes avec `qwen2.5vl:3b`, réponse en moins de 20 s.
- Une phrase piégée (« mets le lit dans le mur ») → refus expliqué, rien dans la scène.
- Une phrase ambiguë (« déplace le meuble ») avec deux meubles → question.
- Chaque action apparaît dans l'historique avec l'acteur `assistant`, un Ctrl+Z la retire.
- Scénario navigateur : « ajoute un bureau près de la fenêtre » → meuble surligné, message,
  Annuler ; sans modèle configuré : le panneau le dit et propose « Aménager » à la main.

## 5. Découpage proposé

| Étape | Contenu | Taille |
|---|---|---|
| B1 | Schéma `AssistantIntent` + analyseur tolérant + acteur `assistant` dans la politique d'historique + moteur d'exécution des intentions (meubles seulement) + tests | 1 lot |
| B2 | Conversation dans le panneau (messages, surbrillance, Annuler par message, questions), sans modèle : intentions saisies par des phrases-types pour les tests | 1 lot |
| B3 | Branchement du modèle local (prompt, réessai), essai réel des dix phrases, protocole | 1 lot |
| B4 | Intentions sur les murs, dimensions, ouvertures, pièces | 1 lot |
| B5 | Gemini consenti, journal, fiche « ce qui part » comme dans Aménager | ½ lot |

## 6. Questions à augus

1. Périmètre de départ : meubles seulement (B1–B3), ou tout de suite murs et ouvertures (B4) ?
2. L'assistant peut-il aussi **répondre à des questions** sur le plan (« quelle surface à plus de
   1,80 m ? ») sans agir ? (Proposé : oui, texte seul, aucun coût.)
3. Le panneau « Aménager » en trois pas reste-t-il à côté, ou la conversation le remplace-t-elle
   (les variantes deviendraient une réponse de l'assistant) ? Proposé : garder les deux le temps
   d'un essai réel, puis trancher.
4. Sans modèle configuré, faut-il proposer d'installer Ollama depuis l'atelier (commande
   affichée, rien de lancé), ou se taire ?
