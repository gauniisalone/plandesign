"""KAN-124 (phase 1) : l'assistant de calage du parcours guidé, côté serveur seulement.

Partage des rôles (conception du PO, 6 nov.) :

- l'APP calcule, de façon déterministe, ce qui manque (`missing`) et pose la question ;
- le MODÈLE (local, par une API compatible OpenAI) ne fait qu'extraire la réponse libre de
  l'utilisateur, dans un JSON contraint par `extraction.schema.json` (`extraction`) ;
- l'APP revalide tout, puis propose une action (`proposal`) : un cas d'usage du registre
  (KAN-105) avec ses boutons ; rien ne s'exécute sans le clic de l'utilisateur.

Sans modèle, en erreur, ou trop lent (20 s), c'est le formulaire classique. Le tout est
derrière OPENPLAN_ASSISTANT (éteint par défaut), sans interface dans cette phase.
"""
