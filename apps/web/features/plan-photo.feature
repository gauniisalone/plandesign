# language: fr
# KAN-103, pilote (a), le chemin avec poids : en ligne (sans poids), une photo ne donne pas de
# plan. Ce parcours tourne dans les e2e de base, là où les poids sont installés ; sans eux (CI),
# il est sauté avec sa raison, dans la liste des tests sautés.
@kan-103 @poids
Fonctionnalité: Le plan se construit depuis la photo
  Une photo de la pièce, déposée comme source, construit le plan toute seule, sans question
  ni bouton d'analyse.

  Scénario: une photo déposée construit un plan estimé
    Quand je crée le projet "Chambre depuis la photo"
    Et je dépose une photo de la pièce comme source du plan
    Alors le plan se construit depuis la photo, avec ses quatre murs
    Et le plan est annoncé comme estimé
