# language: fr
# KAN-103, pilote (a) : le parcours court, en ligne (image de production : ni GPU, ni Blender,
# ni poids), avec le poste de rendu simulé comme dans e2e-online.
@kan-103
Fonctionnalité: Parcours court, du nouveau projet au rendu en file
  Un compte autorisé aux rendus crée un projet, y range une photo, obtient un plan et demande
  un rendu réaliste ; le poste de rendu le prend.

  Contexte:
    Étant donné que je suis connecté avec le compte autorisé aux rendus

  Scénario: nouveau projet, photo, plan, rendu en file
    Quand je crée le projet "Chambre du pilote"
    Et j'importe une photo de la pièce
    Alors la photo est rangée dans les photos de la pièce
    Quand je décris le projet : "Une chambre de 12 m², lumineuse"
    Et je génère le plan
    Alors le plan apparaît dans l'espace de travail
    Quand j'ajoute au plan le point de vue "Vue fenêtre"
    Et je demande le rendu de la vue "Vue fenêtre"
    Alors le rendu attend le poste de rendu
    Quand le poste de rendu prend la demande
    Alors le rendu est en cours
    Quand le poste de rendu dépose son image
    Alors l'image du rendu s'affiche
