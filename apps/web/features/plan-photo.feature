# language: fr
# KAN-103, pilote (a), le chemin avec poids : en ligne (sans poids), une photo ne donne pas de
# plan. Ce parcours tourne dans les e2e de base, là où les poids sont installés ; sans eux (CI),
# il est sauté avec sa raison, dans la liste des tests sautés. KAN-120 : la photo déposée
# attend le bouton « Générer le plan » du node Photos de la pièce.
@kan-103 @poids
Fonctionnalité: Le plan se construit depuis la photo
  Une photo de la pièce, déposée comme source, ne lance rien toute seule : le bouton
  « Générer le plan » du node Photos construit le plan en un clic, sans question ni choix de
  méthode.

  Scénario: une photo déposée, puis « Générer le plan », construit un plan estimé
    Quand je crée le projet "Chambre depuis la photo"
    Et je dépose une photo de la pièce comme source du plan
    Alors le plan attend le bouton « Générer le plan »
    Quand je génère le plan à partir de la photo
    Alors le plan se construit depuis la photo, avec ses quatre murs
    Et le plan est annoncé comme estimé
